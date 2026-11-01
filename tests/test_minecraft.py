"""숲 너머 개척지: 이벤트 -> 장부(자원/건물), 시대 발전, 일 정하기 규칙, 한 바퀴(가짜 API), 매시 회의, 습격."""
from datetime import datetime

from poring_food import loadshed, minecraft


def _ag(name, profile="마을 사람"):
    return {"name": name, "company": "오아시스 마을", "sleep": [3, 4], "profile": profile, "speech": ""}


class FakeApi:
    def __init__(self, bots=(), events=(), day=False):
        self.bots, self.events, self.day, self.calls = list(bots), list(events), day, []

    def get(self, path):
        if path == "/health":
            return {"ok": True, "bots": self.bots}
        if path.startswith("/events"):
            return {"events": self.events}
        if path == "/state":
            return {"day": self.day, "bots": [{"name": b, "task": None} for b in self.bots]}
        raise AssertionError(path)

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/join":
            self.bots.append(body["name"])
        if path == "/leave":
            self.bots = [] if body["name"] == "*" else [b for b in self.bots if b != body["name"]]
        return {"ok": True}


def test_names_and_mode():
    assert minecraft.mc_name("페른") == "Fern" and minecraft.mc_name("새사람").startswith("V")
    assert len(minecraft.mc_name("새사람")) <= 16
    assert minecraft.mc_world() == (minecraft.WORLD_MODE == "minecraft")


def test_old_ledger_migrates(monkeypatch, tmp_path):
    import json
    p = tmp_path / "mc.json"
    p.write_text(json.dumps({"stock": 15, "built": {"hut": 1}}), encoding="utf-8")
    monkeypatch.setattr(minecraft, "STATE_PATH", str(p))
    st = minecraft._load()
    assert st["res"] == {"logs": 15} and "stock" not in st


def test_apply_events():
    st = {"pending": {"Fern": {"task": "build", "blueprint": "tower", "cost": {"stone": 24, "logs": 6}}}, "res": {}}
    evs = [{"id": 3, "name": "Stark", "kind": "done", "task": "gather", "result": {"logs": 12}},
           {"id": 4, "name": "Fern", "kind": "failed", "task": "build"},
           {"id": 5, "name": "Benimaru", "kind": "done", "task": "patrol", "result": {"kills": 3}},
           {"id": 6, "name": "Ubel", "kind": "done", "task": "build", "result": {"blueprint": "hut"}},
           {"id": 7, "name": "Kazuma", "kind": "died"},
           {"id": 8, "name": "Geld", "kind": "done", "task": "mine", "result": {"stone": 9}}]
    lines = minecraft.apply_events(st, evs, {"Stark": "슈타르크", "Fern": "페른", "Benimaru": "베니마루", "Ubel": "위벨",
                                             "Geld": "게르도"})
    assert st["res"] == {"logs": 18, "stone": 33}          # 12 + 되돌린 6 / 되돌린 24 + 캔 9
    assert st["kills"] == 3 and st["built"] == {"hut": 1} and st["deaths"] == 1 and st["last_event"] == 8
    assert [w for w, _ in lines] == ["슈타르크", "베니마루", "위벨", "Kazuma", "게르도"]


def test_eras():
    st = {"built": {}, "res": {}}
    assert minecraft.ERAS[minecraft.era(st)][0] == "개척 캠프" and set(minecraft.needed(st)) == {"hut", "farm"}
    assert "smithy" not in minecraft.allowed(st)
    st["built"] = {"hut": 2, "farm": 1}
    assert minecraft.advance(st) == "통나무 마을" and minecraft.needed(st) == ["hut", "farm", "well"]
    assert minecraft.advance(st) is None
    st["built"].update(hut=4, farm=2, well=1)
    assert minecraft.advance(st) == "돌담 마을" and "smithy" in minecraft.allowed(st)
    assert "돌담 마을" in minecraft.ledger(st)


def test_decide_rules():
    people = [_ag("페른"), _ag("베니마루"), _ag("게르도")]
    st = {"res": {"logs": 25, "stone": 0}, "built": {}}
    got = minecraft.decide(people, st, day=False, llm=lambda s, u: {"tasks": [
        {"name": "페른", "task": "build", "blueprint": "hut", "say": "지을게요"},
        {"name": "베니마루", "task": "gather", "say": "나무"},
        {"name": "게르도", "task": "build", "blueprint": "hut", "say": "나도"}]})
    assert got[0]["task"] == "build" and got[0]["blueprint"] == "hut"   # 통나무 25 -> 오두막 하나는 된다
    assert got[2]["task"] == "gather"                                  # 남은 5개로는 두 번째 오두막을 못 지음 -> 나무
    assert got[1]["task"] == "patrol"                                  # 밤엔 가장 센 사람이 순찰
    got = minecraft.decide([_ag("페른")], {"res": {"logs": 50}, "built": {}, "era": 1}, day=True,
                           llm=lambda s, u: {"tasks": [{"name": "페른", "task": "build", "blueprint": "well"}]})
    assert got[0]["task"] == "mine"                                    # 우물은 돌이 필요 -> 돌 캐기
    got = minecraft.decide([_ag("페른")], {"res": {"logs": 99, "stone": 99}, "built": {}}, day=True,
                           llm=lambda s, u: {"tasks": [{"name": "페른", "task": "build", "blueprint": "hall"}]})
    assert got[0]["blueprint"] in ("hut", "farm")                      # 아직 못 짓는 시대 건물은 이번 시대 목표로


def test_server_line_goes_into_decision(monkeypatch):
    monkeypatch.setattr(minecraft, "server_line", lambda: "결계 경보 3단계(방어) / 환영극장에 손님 2명이 상영 중")
    seen = []
    minecraft.decide([_ag("페른")], {"res": {}}, day=True, llm=lambda s, u: seen.append(u) or {"tasks": []})
    assert "환영극장에 손님 2명" in seen[0] and "[마을 서버 사정]" in seen[0] and "개척 캠프" in seen[0]


def _env(monkeypatch, tmp_path, stage=0):
    monkeypatch.setattr(minecraft, "ENABLED", True)
    monkeypatch.setattr(minecraft, "STATE_PATH", str(tmp_path / "mc.json"))
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": stage, "playing": 0}}})
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: stage)
    monkeypatch.setattr("poring_food.memory.remember", lambda *a, **k: None)
    monkeypatch.setattr("poring_food.chronicle.record_event", lambda *a, **k: None)


def test_tick_assigns_era_and_backs_off(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agents = [_ag("페른"), _ag("베니마루"), _ag("슈타르크"), _ag("카즈마")]
    api, posts, reacted = FakeApi(), [], []
    react = lambda d, t, people, kind="": reacted.append((t, people, d))  # noqa: E731
    out = minecraft.tick(datetime(2026, 11, 1, 21), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": []}, react=react)
    assert len(out["assigned"]) == minecraft.MAX_BOTS and posts
    # 오두막 2채 + 밭 1채가 다 지어지면 다음 시대 + 개척지 반응
    st = minecraft._load()
    st["built"] = {"hut": 2}
    minecraft._save(st)
    api.events = [{"id": 99, "name": "Fern", "kind": "done", "task": "build", "result": {"blueprint": "farm"}}]
    minecraft.tick(datetime(2026, 11, 1, 21, 10), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                   llm=lambda s, u: {"tasks": []}, react=react)
    assert minecraft._load()["era"] == 1 and any("통나무 마을" in t for _, t in posts) and reacted[-1][0] == "새 시대"
    # 결계 2단계 -> 모두 철수 + 결계 담당과 돌아온 사람이 반응
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 2)
    api.events = []
    out = minecraft.tick(datetime(2026, 11, 1, 21, 20), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         react=react)
    assert out == {"busy": "결계 경보 2단계"} and ("/leave", {"name": "*"}) in api.calls and api.bots == []
    assert reacted[-1][0] == "개척지 철수" and "결계 경보 2단계" in reacted[-1][2]


def test_hourly_talk(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agents = [_ag("페른"), _ag("베니마루"), _ag("슈타르크")]
    monkeypatch.setattr("poring_food.agents.load_agents", lambda: agents)
    monkeypatch.setattr(minecraft, "server_line", lambda: "결계 경보 1단계(경계)")
    heard, posts = [], []

    def speak(who, user):
        heard.append(user)
        return f"{who}: 돌이 모자라"
    out = minecraft.hourly(datetime(2026, 11, 1, 14), speak=speak, post=lambda n, t: posts.append((n, t)))
    assert len(out) == minecraft.TALK_LINES and len({x["speaker"] for x in out}) >= 2
    assert "다음 시대로 가려면" in heard[0] and "결계 경보 1단계" in heard[0] and len(posts) == minecraft.TALK_LINES
    assert "돌이 모자라" in heard[-1]                                   # 앞사람 말을 듣고 말한다


def test_surge_raid(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    api, posts = FakeApi(), []
    assert minecraft.surge_raid({"peak": 88, "base": 30, "rise": 58, "playing": 2}, api=api, post=posts.append) is None
    st = minecraft._load()
    st["online"] = ["Fern"]
    minecraft._save(st)
    got = minecraft.surge_raid({"peak": 92, "base": 30, "rise": 62, "playing": 2}, api=api, post=posts.append)
    assert got["mob"] == "husk" and 3 <= got["count"] <= 10 and api.calls[-1][0] == "/raid" and "30%→92%" in posts[0]


def test_off_and_down(monkeypatch):
    monkeypatch.setattr(minecraft, "ENABLED", False)
    assert minecraft.tick() == {} and minecraft.hourly() == []
    monkeypatch.setattr(minecraft, "ENABLED", True)

    class Down:
        def get(self, path):
            raise OSError("connection refused")
    assert "down" in minecraft.tick(api=Down(), agents=[])
