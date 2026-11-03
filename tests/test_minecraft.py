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
    assert [w for w, _ in lines] == ["슈타르크", "베니마루", "위벨", minecraft.NAME, "Kazuma", "게르도"]
    assert "첫 오두막" in lines[3][1] and st["history"][-1]["text"].startswith("개척 1일째")
    assert st["made"] == {"logs": 12, "stone": 9} and st["stats"]["build"] == {"done": 1, "failed": 1, "empty": 0}
    assert any("짓기 실패" in x["text"] for x in st["lessons"])


def test_eras():
    st = {"built": {}, "res": {}}
    assert minecraft.ERAS[minecraft.era(st)][0] == "개척 캠프" and set(minecraft.needed(st)) == {"hut", "farm"}
    assert "smithy" not in minecraft.allowed(st)
    st["built"] = {"hut": 2, "farm": 1}
    assert minecraft.advance(st) == "통나무 마을" and minecraft.needed(st) == ["hut", "farm", "well"]
    assert minecraft.advance(st) is None
    st["built"].update(hut=4, farm=2, well=1)
    assert minecraft.advance(st) is None and minecraft.unmet(st) == ["식량 창고 0/20"]   # 건물만으론 안 된다 - 식량 자급
    st["res"]["food"] = 25
    assert minecraft.advance(st) == "돌담 마을" and "smithy" in minecraft.allowed(st)
    assert "석탄 누적 0/8" in minecraft.ledger(st)
    assert "돌담 마을" in minecraft.ledger(st)


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
    gathers = [b for p, b in api.calls if p == "/task" and b["kind"] == "gather"]
    assert gathers and gathers[0]["args"]["repeat"] is True                 # 다음 바퀴까지 이어서 모은다
    # 오두막 2채 + 밭 1채가 다 지어지면 다음 시대 + 개척지 반응
    st = minecraft._load()
    st["built"] = {"hut": 2}
    minecraft._save(st)
    api.events = [{"id": 99, "name": "Fern", "kind": "done", "task": "build", "result": {"blueprint": "farm"}}]
    minecraft.tick(datetime(2026, 11, 1, 21, 10), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                   llm=lambda s, u: {"tasks": []}, react=react)
    assert minecraft._load()["era"] == 1 and any("통나무 마을" in t for _, t in posts) and reacted[-1][0] == "새 시대"
    # 결계 2단계 -> 그대로 일한다
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 2)
    api.events = []
    out = minecraft.tick(datetime(2026, 11, 1, 21, 15), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": []}, react=react)
    assert "assigned" in out and len(api.bots) == minecraft.MAX_BOTS
    # 3단계 + 아무도 안 봄 -> 한 사람만, 마을 안 일만
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 3)
    mark = len(api.calls)
    out = minecraft.tick(datetime(2026, 11, 1, 21, 18), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": [{"name": a["name"], "task": "ore"} for a in agents]}, react=react)
    assert len(api.bots) == 1 and all(t["task"] in minecraft.LIGHT_TASKS for t in out["assigned"])
    lite_calls = [b for p, b in api.calls[mark:] if p == "/task"]
    assert lite_calls and all(b["kind"] != "rest" for b in lite_calls)               # 쉬기로 바꾸지 않는다
    assert all(b["args"].get("near") is True for b in lite_calls if b["kind"] in ("gather", "mine"))
    assert any("한 사람만 남아" in t for _, t in posts)
    # 4단계 -> 모두 철수 + 결계 담당과 돌아온 사람이 반응, 그 뒤 REJOIN_MIN분은 다시 안 들어감
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 4)
    out = minecraft.tick(datetime(2026, 11, 1, 21, 20), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         react=react)
    assert out == {"busy": "결계 경보 4단계"} and ("/leave", {"name": "*"}) in api.calls and api.bots == []
    assert reacted[-1][0] == "개척지 철수" and "결계 경보 4단계" in reacted[-1][2]
    monkeypatch.setattr(loadshed, "stage", lambda now=None, fresh_min=15: 0)
    out = minecraft.tick(datetime(2026, 11, 1, 21, 30), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": []}, react=react)
    assert out == {"busy": "철수 뒤 숨 고르기"} and api.bots == []
    out = minecraft.tick(datetime(2026, 11, 1, 21, 41), api=api, agents=agents, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"tasks": []}, react=react)
    assert "assigned" in out


def test_stage3_with_playback_withdraws(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, stage=3)
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 3, "playing": 1}}})
    assert minecraft.busy() == "결계 경보 3단계에 환영극 상영 중"
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 3, "playing": 0}}})
    assert minecraft.busy() is None and minecraft.light()


def test_hourly_talk(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agents = [_ag("페른"), _ag("베니마루"), _ag("슈타르크")]
    monkeypatch.setattr("poring_food.agents.load_agents", lambda: agents)
    monkeypatch.setattr(minecraft, "server_line", lambda: "결계 경보 1단계(경계)")
    monkeypatch.setattr(minecraft, "checkup", lambda *a, **k: {})
    heard, posts = [], []

    def speak(who, user):
        heard.append(user)
        return f"{who}: 돌이 모자라"
    plan = lambda s, u: {"focus": "food", "target": 15, "why": "밭을 돌려야 한다"}  # noqa: E731
    out = minecraft.hourly(datetime(2026, 11, 1, 14), speak=speak, post=lambda n, t: posts.append((n, t)), llm=plan)
    assert len(out) == minecraft.TALK_LINES and len({x["speaker"] for x in out}) >= 2
    assert "다음 시대로 가려면" in heard[0] and "결계 경보 1단계" in heard[0] and len(posts) == minecraft.TALK_LINES + 1
    assert "🎯 식량 늘리기 +15" in posts[-1][1] and minecraft._load()["plan"]["focus"] == "food"
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


def test_off_and_down(monkeypatch, tmp_path):
    monkeypatch.setattr(minecraft, "STATE_PATH", str(tmp_path / "mc.json"))
    monkeypatch.setattr(minecraft, "ENABLED", False)
    assert minecraft.tick() == {} and minecraft.hourly() == []
    monkeypatch.setattr(minecraft, "ENABLED", True)

    class Down:
        def get(self, path):
            raise OSError("connection refused")
    assert "down" in minecraft.tick(api=Down(), agents=[])


def test_ore_first_and_empty_lesson():
    st = {"res": {}}
    now = datetime(2026, 11, 1)
    lines = minecraft.apply_events(st, [
        {"id": 1, "name": "Stark", "kind": "done", "task": "ore", "result": {"coal": 3, "iron": 0}},
        {"id": 2, "name": "Stark", "kind": "done", "task": "farm", "result": {"food": 0}}], {"Stark": "슈타르크"}, now)
    assert st["made"]["coal"] == 3 and any("처음으로 석탄" in x for _, x in lines)
    assert st["stats"]["farm"]["empty"] == 1 and "빈손" in st["lessons"][-1]["text"]


def test_plan_evaluation(monkeypatch, tmp_path):
    now = datetime(2026, 11, 1, 12)
    st = {"res": {"food": 2}, "made": {"food": 10}, "built": {"farm": 1}}
    plan = minecraft.set_plan(st, now, llm=lambda s, u: {})                  # LLM이 비면 규칙: 식량 문제부터
    assert plan["focus"] == "food" and plan["base"] == 10 and plan["target"] == 12
    st["made"]["food"] = 30
    text = minecraft.evaluate_plan(st, now)
    assert "+20" in text and "달성" in text and st["plans"] == {"ok": 1, "miss": 0} and "plan" not in st
    minecraft.set_plan(st, now, llm=lambda s, u: {"focus": "ore", "target": 9})
    assert "미달" in minecraft.evaluate_plan(st, now) and "미달" in st["lessons"][-1]["text"]


def test_farm_growth_by_time():
    st = {}
    now = datetime(2026, 11, 1, 12, 0)
    assert minecraft.grow_stages(st, now) == 7                              # 처음엔 다 익었다고 본다
    st["farm_at"] = "2026-11-01 11:50"
    assert minecraft.grow_stages(st, now) == 3                              # 10분 = 3단계 (3분에 한 단계)
    st["farm_at"] = "2026-11-01 09:00"
    assert minecraft.grow_stages(st, now) == 7


def test_farm_no_crops_lesson_and_death_reset(monkeypatch, tmp_path):
    st = {"res": {}}
    minecraft.apply_events(st, [{"id": 1, "name": "Dani", "kind": "done", "task": "farm",
                                 "result": {"food": 0, "crops": 0}}], {"Dani": "강단이"}, datetime(2026, 11, 1))
    assert "한 포기도 없음" in st["lessons"][-1]["text"]
    import json
    p = tmp_path / "mc.json"
    p.write_text(json.dumps({"deaths": 489, "lessons": [{"text": "밤에 몬스터에게 쓰러짐 - x", "n": 400}]}), encoding="utf-8")
    monkeypatch.setattr(minecraft, "STATE_PATH", str(p))
    st = minecraft._load()
    assert st["deaths"] == 0 and st["lessons"] == []                         # 부풀려진 쓰러짐 수는 한 번 비운다


def test_audit_fixes_phantom_buildings(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    st = minecraft._load()
    st.update(built={"hut": 1, "farm": 1, "wall": 3}, slots={"hut": 2, "farm": 1, "wall": 3}, res={"food": 50})
    minecraft._save(st)
    api, posts = FakeApi(), []
    out = minecraft.tick(datetime(2026, 11, 1, 12), api=api, agents=[_ag("페른"), _ag("게르도")],
                         post=lambda n, t: posts.append((n, t)), llm=lambda s, u: {"tasks": []}, react=lambda *a, **k: None)
    audit = [b for p, b in api.calls if p == "/task" and b["kind"] == "audit"]
    assert len(audit) == 1 and audit[0]["args"]["slots"] == {"hut": 2, "farm": 1, "wall": 3}
    assert out["assigned"][-1]["task"] == "audit"
    st = minecraft._load()
    lines = minecraft.apply_events(st, [{"id": 9, "name": "Geld", "kind": "done", "task": "audit",
                                         "result": {"found": {"hut": 1, "farm": 0, "wall": 0}, "checked": 4}}],
                                   {"Geld": "게르도"}, datetime(2026, 11, 1, 12, 5))
    assert st["built"] == {"hut": 1} and st["audited"] and st["slots"] == {"hut": 1}
    assert "장부와 달랐다" in lines[0][1] and "밭 1→0" in lines[0][1]
    lines = minecraft.apply_events(st, [{"id": 10, "name": "Geld", "kind": "done", "task": "farm",
                                         "result": {"food": 0, "crops": 0, "rebuilt": 1}}], {"Geld": "게르도"})
    assert "다시 일구고" in lines[0][1] and not any("한 포기도" in x["text"] for x in st.get("lessons") or [])



class Healthy(FakeApi):
    def __init__(self, started="s1", rcon=None):
        super().__init__(bots=["Fern"])
        self.started, self.rcon = started, rcon or {"ok": True}

    def get(self, path):
        if path == "/health":
            return {"ok": True, "bots": self.bots, "started": self.started, "rcon": self.rcon, "heapMB": 300}
        return super().get(path)


class Dead:
    def get(self, path):
        raise OSError("connection refused")


def test_checkup_restarts_dead_agents(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    posts, restarts = [], []
    restart = lambda: restarts.append(1) or ""  # noqa: E731
    out = minecraft.checkup(datetime(2026, 11, 1, 10), api=Dead(), post=lambda n, t: posts.append(t), restart=restart)
    assert restarts == [1] and "다시 열었다" in out["actions"][0]
    minecraft.checkup(datetime(2026, 11, 1, 10, 30), api=Dead(), post=lambda n, t: posts.append(t), restart=restart)
    assert restarts == [1]                                                   # 한 시간에 한 번만
    for h in (11, 12, 13):
        minecraft.checkup(datetime(2026, 11, 1, h), api=Dead(), post=lambda n, t: posts.append(t), restart=restart)
    assert len(restarts) == minecraft.RESTART_MAX and any("사람 손이 필요해요" in p for p in posts)


def test_checkup_rcon_password_alert_once(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    posts = []
    api = Healthy(rcon={"ok": False, "error": "RCON 비밀번호가 틀림"})
    out = minecraft.checkup(datetime(2026, 11, 1, 10), api=api, post=lambda n, t: posts.append(t))
    assert out["alerts"] == ["rcon_pass"] and "MC_RCON_PASSWORD" in posts[0]
    out = minecraft.checkup(datetime(2026, 11, 1, 11), api=api, post=lambda n, t: posts.append(t))
    assert out["alerts"] == [] and len(posts) == 1                          # 같은 알림은 ALERT_H시간에 한 번


def test_checkup_stall_avoids_failing_task(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    st = minecraft._load()
    st.update(made={"logs": 5}, lessons=[{"text": "나무 베기: 빈손으로 돌아옴 (근처에 없음)", "n": 4}])
    minecraft._save(st)
    posts = []
    minecraft.checkup(datetime(2026, 11, 1, 10), api=Healthy(), post=lambda n, t: posts.append(t))
    out = minecraft.checkup(datetime(2026, 11, 1, 11, 5), api=Healthy(), post=lambda n, t: posts.append(t))
    assert "돌을 캐게" in out["actions"][0]
    st = minecraft._load()
    assert minecraft.avoided(st, datetime(2026, 11, 1, 11, 30)) == {"gather"}
    got = minecraft.decide([_ag("페른")], {**st, "res": {"food": 50}}, day=True,
                           llm=lambda s, u: {"tasks": [{"name": "페른", "task": "gather"}]}, now=datetime(2026, 11, 1, 11, 30))
    assert got[0]["task"] == "mine"
    out = minecraft.checkup(datetime(2026, 11, 1, 13, 10), api=Healthy(), post=lambda n, t: posts.append(t))
    assert out["alerts"] == ["stalled"]                                     # 3시간 넘게 그대로 -> 사람에게
    out = minecraft.checkup(datetime(2026, 11, 1, 21, 5), api=Healthy(), post=lambda n, t: posts.append(t))
    assert any("일째 보고" in p for p in posts)


def test_checkup_counts_crash(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    posts = []
    minecraft.checkup(datetime(2026, 11, 1, 10), api=Healthy("a"), post=lambda n, t: posts.append(t))
    out = minecraft.checkup(datetime(2026, 11, 1, 11), api=Healthy("b"), post=lambda n, t: posts.append(t))
    assert "다시 시작됐다" in out["actions"][0]


def _jobs(st, **jobs):
    st.setdefault("jobs", {}).update(jobs)
    return st


def test_give_job_from_profile_and_balance():
    st = {}
    now = datetime(2026, 11, 1)
    assert minecraft.give_job(st, _ag("센시", "드워프 요리사 - 밭과 주방을 맡는다"), now)[0] in ("farm", "mine")
    assert minecraft.give_job(st, _ag("카이진", "드워프 대장장이, 광산"), now)[0] == "mine"
    job, line = minecraft.give_job(st, _ag("베니마루", "오니 무사"), now)
    assert job == "patrol" and "경비" in line
    assert minecraft.give_job(st, _ag("베니마루"), now) == ("patrol", None)          # 한 번 정하면 그대로
    for i in range(10):
        minecraft.give_job(st, _ag(f"사람{i}"), now)
    counts = {j: list(st["jobs"].values()).count(j) for j in minecraft.JOBS}
    assert counts["farm"] >= 2 and counts["gather"] >= 2                            # 직업 없는 사람은 모자란 직업으로
    assert "농부" in minecraft.change_job(st, "베니마루", "farm", now, "밀이 모자라") and st["jobs"]["베니마루"] == "farm"


def test_decide_by_job():
    people = [_ag("페른"), _ag("게르도"), _ag("카이진"), _ag("리그루드")]
    st = _jobs({"res": {"logs": 25, "stone": 0, "food": 50}, "built": {}},
               페른="farm", 게르도="mine", 카이진="build", 리그루드="gather")
    got = {t["name"]: t for t in minecraft.decide(people, st, day=True, llm=lambda s, u: {"says": [
        {"name": "페른", "say": "밭을 일굴게"}]})}
    assert got["페른"]["task"] == "build" and got["페른"]["blueprint"] == "farm"      # 농부: 밭이 없으면 밭부터
    assert got["게르도"]["task"] == "mine" and got["리그루드"]["task"] == "gather"
    assert got["카이진"]["task"] == "gather"                    # 건축가: 밭을 짓고 나면 오두막(통나무 20) 재료가 모자라 나무
    assert got["페른"]["say"] == "밭을 일굴게" and got["페른"]["job"] == "farm"
    st["built"] = {"farm": 1}
    st["res"]["logs"] = 30
    got = {t["name"]: t for t in minecraft.decide(people, st, day=False, llm=lambda s, u: {})}
    assert got["페른"]["task"] == "farm" and got["카이진"]["task"] == "build" and got["카이진"]["blueprint"] == "hut"
    assert any(t["task"] == "patrol" for t in got.values())                         # 밤엔 한 명 순찰
    st["era"] = 2
    st["built"] = {"farm": 2, "hut": 4, "well": 1}
    got = {t["name"]: t for t in minecraft.decide(people, st, day=True, llm=lambda s, u: {})}
    assert got["게르도"]["task"] == "ore"                                             # 시대가 석탄을 원하면 광부는 광석


def test_hungry_someone_helps_farm_and_ate_event():
    st = _jobs({"res": {"food": 0, "logs": 5}, "built": {"farm": 1}}, 게르도="mine", 리그루드="gather")
    now = datetime(2026, 11, 1, 10)
    assert "굶주림" in minecraft.eat(st, 2, now) and st["hungry"]
    got = minecraft.decide([_ag("게르도"), _ag("리그루드")], st, day=True, llm=lambda s, u: {})
    assert [t["task"] for t in got].count("farm") == 1                              # 농부가 없어도 한 명은 밭으로
    minecraft.apply_events(st, [{"id": 1, "name": "Fern", "kind": "done", "task": "farm", "result": {"food": 30}},
                                {"id": 2, "name": "Geld", "kind": "ate"}], {"Fern": "페른", "Geld": "게르도"}, now)
    assert st["res"]["food"] == 30 - minecraft.MEAL
    assert "식량 부족을 넘겼다" in minecraft.eat(st, 2, now) and "hungry" not in st


def test_pick_covers_needed_jobs(monkeypatch):
    monkeypatch.setattr("poring_food.agents._asleep", lambda a, h: False)
    agents = [_ag(n) for n in ("페른", "게르도", "카이진", "리그루드", "센시")]
    st = _jobs({"res": {"food": 0}, "built": {"farm": 1}}, 페른="gather", 게르도="mine", 카이진="build",
               리그루드="gather", 센시="farm")
    online = [minecraft.mc_name(n) for n in ("페른", "게르도", "리그루드")]
    got = [a["name"] for a in minecraft.pick(agents, online, True, k=3, st=st)]
    assert "센시" in got and len(got) == 3                                           # 굶는데 농부가 없으면 농부를 들인다
    assert sum(1 for n in got if n in ("페른", "리그루드")) == 1                       # 겹치는 나무꾼 하나가 나간다



def test_center_move_resets_phantom_buildings(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    st = minecraft._load()
    st.update(built={"hut": 1, "farm": 1}, slots={"hut": 2, "farm": 1}, res={"logs": 15, "food": 40})
    minecraft._save(st)

    class Centered(FakeApi):
        def get(self, path):
            if path == "/health":
                return {"ok": True, "bots": self.bots, "center": {"x": -300, "z": -150}}
            return super().get(path)
    posts = []
    minecraft.tick(datetime(2026, 11, 1, 12), api=Centered(), agents=[_ag("페른")],
                   post=lambda n, t: posts.append(t), llm=lambda s, u: {}, react=lambda *a, **k: None)
    st = minecraft._load()
    assert st["center"] == {"x": -300, "z": -150} and st["res"]["logs"] in (15, 7)   # 자원은 그대로 (밭 짓기 예약 8 빼고)
    assert not any(v for v in st["built"].values())
    assert any("(-300, -150)" in p and "실제로는 서지 못했다" in p for p in posts)


def test_tick_survives_slow_bot_service(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)

    class Slow(FakeApi):
        def post(self, path, body):
            if path in ("/leave", "/task"):
                self.calls.append((path, body))
                return {"error": "Read timed out"}
            return super().post(path, body)
    api = Slow(bots=["Zzz"])
    st = minecraft._load()
    st.update(res={"logs": 40, "food": 50}, jobs={"카이진": "build"})
    minecraft._save(st)
    out = minecraft.tick(datetime(2026, 11, 1, 12), api=api, agents=[_ag("카이진")], post=lambda n, t: None,
                         llm=lambda s, u: {}, react=lambda *a, **k: None)
    assert "assigned" in out and out["assigned"][0]["task"] == "-"               # 멈추지 않고 끝까지
    assert minecraft._load()["res"]["logs"] == 40                                # 못 맡긴 짓기 재료는 되돌림


def test_stuck_event_is_a_lesson():
    st = {"res": {}}
    minecraft.apply_events(st, [{"id": 1, "name": "Geld", "kind": "stuck", "task": "farm", "minutes": 13}],
                           {"Geld": "게르도"}, datetime(2026, 11, 1))
    assert st["stats"]["farm"]["failed"] == 1 and "13분 넘게" in st["lessons"][-1]["text"]


def test_storage_is_the_ledger_and_event_reset(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    st = minecraft._load()
    st.update(res={"logs": 7, "stone": 0, "food": 0}, last_event=1762070000000)
    minecraft._save(st)

    class Real(FakeApi):
        def get(self, path):
            if path == "/storage":
                return {"ok": True, "counts": {"logs": 52, "stone": 90, "food": 0, "coal": 0, "iron": 0}}
            if path.startswith("/events?after=0"):
                return {"events": [{"id": 5, "name": "Fern", "kind": "done", "task": "gather", "result": {"logs": 18}}],
                        "last": 5}
            if path.startswith("/events"):
                return {"events": [], "last": 5}            # 봇 서비스가 처음부터 다시 셈
            return super().get(path)
    api = Real()
    minecraft.tick(datetime(2026, 11, 1, 12), api=api, agents=[_ag("페른")], post=lambda n, t: None,
                   llm=lambda s, u: {}, react=lambda *a, **k: None)
    st = minecraft._load()
    assert st["last_event"] == 5 and st["made"]["logs"] == 18          # 다시 센 번호도 받는다
    assert st["storage_at"] and st["res"]["stone"] == 90                # 창고 실제 재고가 장부
    builds = [b for p, b in api.calls if p == "/task" and b["kind"] == "build"]
    assert builds and builds[0]["args"]["cost"]                          # 짓기는 창고에서 꺼낼 재료를 들고 간다


class Live(FakeApi):
    def __init__(self, events):
        super().__init__(bots=["Fern", "Geld"])
        self.evs = events

    def get(self, path):
        if path.startswith("/events"):
            after = int(path.split("after=")[1]) if "after=" in path else 0
            return {"events": [e for e in self.evs if e["id"] > after], "last": max([e["id"] for e in self.evs] or [0])}
        return super().get(path)


def _live_state(monkeypatch, tmp_path, **kw):
    _env(monkeypatch, tmp_path)
    st = minecraft._load()
    st.update(online=["Fern", "Geld"], jobs={"페른": "build", "게르도": "mine"}, res={"logs": 5, "food": 50},
              built={"farm": 1}, **kw)
    minecraft._save(st)


def test_fast_reacts_to_stuck_with_llm_and_opens_request(monkeypatch, tmp_path):
    _live_state(monkeypatch, tmp_path)
    api = Live([{"id": 1, "name": "Geld", "kind": "stuck", "task": "mine", "minutes": 3},
                {"id": 2, "name": "Fern", "kind": "failed", "task": "build", "error": "창고에 logs 부족 (5/20)"}])
    posts = []
    seen = []

    def llm(s, u):
        seen.append(u)
        return {"task": "gather", "why": "돌이 막혔으니 나무", "say": "숲으로 간다", "lesson": "채석장 길이 막힘"}
    out = minecraft.fast(datetime(2026, 11, 3, 12), api=api, post=lambda n, t: posts.append((n, t)), llm=llm,
                         agents=[_ag("페른"), _ag("게르도")])
    tasks = {b["name"]: b["kind"] for p, b in api.calls if p == "/task"}
    assert tasks["Geld"] == "gather" and "돌 캐기" in seen[0] and "3분" in seen[0]
    st = minecraft._load()
    assert st["requests"][0]["resource"] == "logs" and st["requests"][0]["qty"] == 15
    assert any("채석장 길이 막힘" in x["text"] for x in st["lessons"]) and st["llm_day"]["n"] == 2
    assert ("게르도", "gather") in out["reacted"]
    assert minecraft.fast(datetime(2026, 11, 3, 12, 1), api=api, post=lambda n, t: None, llm=llm,
                          agents=[_ag("페른"), _ag("게르도")]) == {}             # 이미 본 사건은 다시 안 본다


def test_fast_chat_reply_and_request_complete(monkeypatch, tmp_path):
    _live_state(monkeypatch, tmp_path, requests=[{"id": 1, "from": "페른", "resource": "logs", "qty": 10, "base": 5,
                                                  "status": "ACCEPTED", "by": "게르도", "at": "2026-11-03 11:50"}])
    st = minecraft._load()
    st["res"]["logs"] = 16
    minecraft._save(st)
    api = Live([{"id": 7, "name": "Fern", "kind": "chat", "from": ".Steve", "text": "Geld 나무 좀 더 베어 줘"}])
    posts = []
    out = minecraft.fast(datetime(2026, 11, 3, 12), api=api, post=lambda n, t: posts.append((n, t)),
                         llm=lambda s, u: {"say": "알았어, 지금 숲으로 간다!", "task": "gather"},
                         agents=[_ag("페른"), _ag("게르도")])
    assert out["chat"] == ["게르도"] and ("/say", {"name": "Geld", "text": "알았어, 지금 숲으로 간다!"}) in api.calls
    assert any(p == "/task" and b["name"] == "Geld" and b["kind"] == "gather" for p, b in api.calls)
    assert any("부탁한 통나무 10개" in t for _, t in posts) and minecraft._load()["requests"][0]["status"] == "COMPLETE"
    assert ".Steve" not in " ".join(t for _, t in posts)                    # 손님 게임 이름은 채널에 안 남긴다


def test_request_is_accepted_by_matching_job():
    st = {"res": {"logs": 0, "food": 50}, "built": {"farm": 1}, "jobs": {"리그루드": "gather", "게르도": "mine"},
          "requests": [{"id": 3, "from": "페른", "resource": "stone", "qty": 12, "base": 0, "status": "PENDING",
                        "at": "2026-11-03 12:00"}]}
    got = {t["name"]: t for t in minecraft.decide([_ag("리그루드"), _ag("게르도")], st, day=True, llm=lambda s, u: {})}
    assert got["게르도"]["task"] == "mine" and got["게르도"]["request"] == 3
