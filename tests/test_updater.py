"""앱 자동 업데이트: 새 버전 확인 -> 담당 인물 판단 -> 안전할 때만 실행 -> 알림/이야기."""
from datetime import datetime

from poring_food import loadshed, updater

T0 = datetime(2026, 10, 30, 5, 0)      # 새벽 (북오아시스 시간대 안)
NOON = datetime(2026, 10, 30, 13, 0)


def _info(head="a" * 40, up="b" * 40, dirty=0, notes="", log=("- fix x",)):
    return {"head": head, "up": up, "dirty": dirty, "version": "1.0", "new_version": "1.1",
            "log": list(log), "notes": notes}


def test_parse_check():
    out = ("HEAD aaa\nUP bbb\nDIRTY 2\nVERSION \"dashboard\": \"3.0.6\"\nNEW_VERSION \"dashboard\": \"3.0.7\"\n"
           "@@log\n- fix one\n- add two\n@@notes\n## v3.0.7\n- (fix) 무언가\n")
    got = updater.parse_check(out)
    assert got["head"] == "aaa" and got["up"] == "bbb" and got["dirty"] == 2
    assert got["log"] == ["- fix one", "- add two"] and "v3.0.7" in got["notes"]
    assert "3.0.7" in got["new_version"]
    assert updater.parse_check("garbage") is None


def _env(monkeypatch, tmp_path, stage=1, playing=0):
    monkeypatch.setattr(updater, "STATE_PATH", str(tmp_path / "updater.json"))
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": stage}}})
    monkeypatch.setattr(loadshed, "_viewing", lambda: (playing, 0))
    monkeypatch.setattr(updater, "_who", lambda company: "담당자")


def test_step_updates_when_safe(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    runs, asked = [], []
    infos = {"bookoasis": _info(notes="## v1.1\n- (fix) 빠르게"), "ym_player": _info(head="c" * 40, up="c" * 40),
             "bookoasis_assistant": _info()}

    def decide(facts):
        asked.append(facts)
        return {"update": True, "reason": "수정과 성능 개선뿐", "story": "서가 감시탑에 새 마법진(북오아시스 1.1)을 새겼다."}
    ev = updater.step(T0, checker=infos.get, decide=decide, runner=lambda k, i: (runs.append(k), (True, "ok"))[1])
    assert sorted(runs) == ["bookoasis", "bookoasis_assistant"] and len(asked) == 2
    assert "(fix) 빠르게" in asked[0] or "(fix) 빠르게" in asked[1]
    assert {e["kind"] for e in ev} == {"updated"} and all(e["story"] for e in ev)
    posts = []
    updater.notify(ev, T0, post=posts.append)
    assert len(posts) == 2 and "✅" in posts[0]
    # 같은 버전은 다시 판단/실행하지 않음 (이미 최신이 되었다고 치면)
    done = {k: _info(head="b" * 40, up="b" * 40) for k in infos}
    assert updater.step(T0.replace(minute=40), checker=done.get, decide=decide, runner=lambda k, i: (True, "")) == []
    assert len(asked) == 2


def test_step_waits_and_defers(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, playing=1)
    runs = []
    info = {"ym_player": _info()}
    ev = updater.step(T0, checker=info.get, decide=lambda f: {"update": True, "reason": "ok", "story": "s"},
                      runner=lambda k, i: (runs.append(k), (True, ""))[1])
    assert ev == [] and runs == []                       # 재생 중 - 조용히 기다림
    monkeypatch.setattr(loadshed, "_viewing", lambda: (0, 0))
    asked = []
    ev = updater.step(T0.replace(minute=31), checker=info.get, decide=lambda f: asked.append(1),
                      runner=lambda k, i: (runs.append(k), (True, ""))[1])
    assert runs == ["ym_player"] and not asked           # 판단은 처음 한 번만
    # 북오아시스: 낮에는 안 함
    monkeypatch.setattr(updater, "STATE_PATH", str(tmp_path / "u2.json"))
    runs.clear()
    updater.step(NOON, checker={"bookoasis": _info()}.get, decide=lambda f: {"update": True},
                 runner=lambda k, i: (runs.append(k), (True, ""))[1])
    assert runs == []
    # 수동 작업이 필요하면 미루고 한 번 알림
    ev = updater.step(NOON.replace(minute=40), checker={"bookoasis_assistant": _info(up="d" * 40)}.get,
                      decide=lambda f: {"update": False, "reason": ".env 추가 필요"}, runner=lambda k, i: (True, ""))
    assert [e["kind"] for e in ev] == ["deferred"] and ".env" in ev[0]["text"]


def test_dirty_and_failure(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    ev = updater.step(T0, checker={"ym_player": _info(dirty=3)}.get, decide=lambda f: {"update": True},
                      runner=lambda k, i: (True, ""))
    assert any("로컬" in e["text"] for e in ev)
    assert updater.guard("bookoasis", T0, _info(dirty=3), playing=0) is None     # update.sh가 알아서
    monkeypatch.setattr(updater, "STATE_PATH", str(tmp_path / "u3.json"))
    ev = updater.step(T0, checker={"bookoasis_assistant": _info()}.get, decide=lambda f: {"update": True},
                      runner=lambda k, i: (False, "Traceback"))
    assert ev[0]["kind"] == "failed" and "git checkout aaaaaaa" in ev[0]["text"]


def test_tool_policy():
    from core.tool_policy import is_server_affecting
    from poring_food import tools
    names = {t.name for t in tools.PORING_FOOD_TOOLS}
    assert {"get_app_versions", "update_app"} <= names
    assert is_server_affecting("update_app") and not is_server_affecting("get_app_versions")
