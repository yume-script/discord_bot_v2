"""마인크래프트 마을: 이벤트 -> 장부, 일 정하기 규칙, 한 바퀴(가짜 API) - 바쁠 때는 모두 나간다."""
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


def test_names():
    assert minecraft.mc_name("페른") == "Fern" and minecraft.mc_name("새사람").startswith("V")
    assert len(minecraft.mc_name("새사람")) <= 16


def test_apply_events():
    st = {"pending": {"Fern": {"task": "build", "blueprint": "hut", "cost": 20}}, "stock": 0}
    evs = [{"id": 3, "name": "Stark", "kind": "done", "task": "gather", "result": {"logs": 12}},
           {"id": 4, "name": "Fern", "kind": "failed", "task": "build"},
           {"id": 5, "name": "Benimaru", "kind": "done", "task": "patrol", "result": {"kills": 3}},
           {"id": 6, "name": "Ubel", "kind": "done", "task": "build", "result": {"blueprint": "tower"}},
           {"id": 7, "name": "Kazuma", "kind": "died"}]
    lines = minecraft.apply_events(st, evs, {"Stark": "슈타르크", "Fern": "페른", "Benimaru": "베니마루", "Ubel": "위벨"})
    assert st["stock"] == 32 and st["kills"] == 3 and st["built"] == {"tower": 1} and st["deaths"] == 1
    assert st["last_event"] == 7 and "Fern" not in st["pending"]
    assert [w for w, _ in lines] == ["슈타르크", "베니마루", "위벨", "Kazuma"]


def test_decide_rules():
    people = [_ag("페른"), _ag("베니마루")]
    st = {"stock": 10, "built": {}}
    got = minecraft.decide(people, st, day=False, llm=lambda s, u: {"tasks": [
        {"name": "페른", "task": "build", "blueprint": "hut", "say": "지을게요"},
        {"name": "베니마루", "task": "gather", "say": "나무"}]})
    assert got[0]["task"] == "gather"                       # 통나무 10개로는 오두막(20) 못 짓는다
    assert got[1]["task"] == "patrol"                       # 밤엔 싸움꾼 한 명은 순찰
    got = minecraft.decide([_ag("페른")], {"stock": 30}, day=True,
                           llm=lambda s, u: {"tasks": [{"name": "페른", "task": "build", "blueprint": "nope"}]})
    assert got[0]["task"] == "build" and got[0]["blueprint"] in minecraft.BLUEPRINTS


def test_tick_assigns_and_backs_off(monkeypatch, tmp_path):
    monkeypatch.setattr(minecraft, "ENABLED", True)
    monkeypatch.setattr(minecraft, "STATE_PATH", str(tmp_path / "mc.json"))
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 0, "playing": 0}}})
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 0)
    monkeypatch.setattr("poring_food.memory.remember", lambda *a, **k: None)
    monkeypatch.setattr("poring_food.chronicle.record_event", lambda *a, **k: None)
    agents = [_ag("페른"), _ag("베니마루"), _ag("슈타르크"), _ag("카즈마")]
    api, posts = FakeApi(), []
    out = minecraft.tick(datetime(2026, 11, 1, 21), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": []})
    joined = [b["name"] for p, b in api.calls if p == "/join"]
    assert len(joined) == minecraft.MAX_BOTS and len(out["assigned"]) == minecraft.MAX_BOTS
    assert sum(1 for p, b in api.calls if p == "/task") == minecraft.MAX_BOTS and posts
    # 결계 2단계 -> 모두 나감
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 2)
    reacted = []
    out = minecraft.tick(datetime(2026, 11, 1, 21, 10), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         react=lambda d, t, people, kind="": reacted.append((t, people, d)))
    assert out == {"busy": "결계 경보 2단계"} and ("/leave", {"name": "*"}) in api.calls and api.bots == []
    assert reacted and reacted[0][0] == "개척지 일 중단" and "결계 경보 2단계" in reacted[0][2]   # 서버 사정이 이야기로
    # 인물 판단 프롬프트에 개척지 사정 + 내가 한 일 + 서버 사정과 엮으라는 안내
    who = "페른"
    block = minecraft.prompt_block(who)
    assert "개척지" in block and "결계" in block


def test_off_and_down(monkeypatch):
    monkeypatch.setattr(minecraft, "ENABLED", False)
    assert minecraft.tick() == {}
    monkeypatch.setattr(minecraft, "ENABLED", True)

    class Down:
        def get(self, path):
            raise OSError("connection refused")
    assert "down" in minecraft.tick(api=Down(), agents=[])


def test_server_line_goes_into_decision(monkeypatch):
    monkeypatch.setattr(minecraft, "server_line", lambda: "결계 경보 3단계(방어) / 환영극장에 손님 2명이 상영 중")
    seen = []
    minecraft.decide([_ag("페른")], {"stock": 0}, day=True, llm=lambda s, u: seen.append(u) or {"tasks": []})
    assert "환영극장에 손님 2명" in seen[0] and "[마을 서버 사정]" in seen[0]
