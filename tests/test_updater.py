"""앱 자동 업데이트: 새 버전 확인 -> 담당 인물 판단 -> 안전할 때만 실행 -> 알림/이야기."""
from datetime import datetime

import pytest

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
        return {"update": True, "reason": "수정과 성능 개선뿐", "story": "서가 모니터링에 새 마법진(북오아시스 1.1)을 새겼다."}
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


def test_bookoasis_3d_is_updated_from_mnt(monkeypatch, tmp_path):
    """3D 서재도 자동 업데이트 대상 - /mnt/bookoasis_3d에서 git pull + 의존성 설치 + 재시작, 시간대 제한 없음"""
    app = updater.APPS["bookoasis_3d"]
    assert app["dir"] == "/mnt/bookoasis_3d" and app["hours"] is None and app["notes"] == "CHANGELOG.md"
    assert "git pull --ff-only" in app["run"] and "requirements.txt" in app["run"] and app["run"].endswith("systemctl restart bookoasis_3d")
    assert "bookoasis_3d" in app["health"]
    _env(monkeypatch, tmp_path)
    runs = []
    ev = updater.step(T0, checker={"bookoasis_3d": _info(notes="## 2.63.0\n- 새 기능")}.get,
                      decide=lambda f: {"update": True, "reason": "기능 추가", "story": "3D 서재를 손봤다."},
                      runner=lambda k, i: (runs.append(k), (True, "ok"))[1])
    assert runs == ["bookoasis_3d"] and [e["kind"] for e in ev] == ["updated"]


def _local(monkeypatch):
    """plex01 대신 이 컴퓨터에서 그대로 돌린다"""
    import subprocess
    monkeypatch.setattr(updater.waitfree, "_remote", lambda script, timeout: (
        lambda p: (p.returncode, p.stdout + p.stderr))(subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=120)))


def test_plex_check_uses_the_apt_repository(monkeypatch, tmp_path):
    """확인 프로그램을 실제로 돌려 본다 (깔려 있는 꾸러미 bash를 Plex인 셈 치고, 저장소 목록은 빈 파일)"""
    import shutil
    if not shutil.which("apt-cache") or not shutil.which("dpkg-query"):
        pytest.skip("apt가 없는 환경")
    _local(monkeypatch)
    said = []
    monkeypatch.setattr(updater, "print", lambda *a: said.append(" ".join(map(str, a))), raising=False)
    repo = tmp_path / "plex.list"
    repo.write_text("", encoding="utf-8")
    monkeypatch.setitem(updater.APPS["plex"], "package", "bash")
    monkeypatch.setitem(updater.APPS["plex"], "repo_list", str(repo))
    monkeypatch.setitem(updater.APPS["plex"], "feed", (tmp_path / "none.json").as_uri())
    info = updater.check("plex")
    assert info and info["head"] == info["up"] == info["version"] and info["dirty"] == 0     # 설치 후보가 같으면 최신
    monkeypatch.setitem(updater.APPS["plex"], "repo_list", str(tmp_path / "missing.list"))  # 새 저장소를 아직 안 넣음
    assert updater.check("plex") is None and "setupRepo.sh" in said[-1]
    monkeypatch.setitem(updater.APPS["plex"], "repo_list", str(repo))
    monkeypatch.setitem(updater.APPS["plex"], "package", "no-such-package-zz")              # apt로 안 깔려 있음 (도커 등)
    assert updater.check("plex") is None and "apt로 깔려 있지" in said[-1]


def test_plex_update_goes_through_apt(monkeypatch, tmp_path):
    app = updater.APPS["plex"]
    assert app["kind"] == "apt" and app["hours"] == (4, 8) and "plexmediaserver" in app["health"]
    assert app["repo_list"] == "/etc/apt/sources.list.d/plex.list"
    scripts = []
    monkeypatch.setattr(updater.waitfree, "_remote", lambda script, timeout: (scripts.append(script), (0, "NOW 2 OLD 1 RC 0 HEALTH 0"))[1])
    info = {"head": "1.43.4.10000-aaaaaaaaa", "up": "1.43.5.11079-b3b13e506", "dirty": 0, "log": ["- (DVR) 고침"], "notes": ""}
    ok, _out = updater.run_update("plex", info)
    assert ok and "apt-get install -y -qq --only-upgrade" in scripts[0] and scripts[0].count("plexmediaserver") >= 3
    assert "curl" not in scripts[0] and "git " not in scripts[0]                             # 받아 오기·서명 확인은 apt가 한다
    assert updater._short("a" * 40) == "aaaaaaa" and updater._short("1.43.5.11079-b3b13e506") == "1.43.5.11079"
    # 새 버전이면 담당이 판단하고, 새벽·재생 없음일 때만 실행
    _env(monkeypatch, tmp_path)
    runs = []
    go = dict(checker={"plex": info}.get, decide=lambda f: {"update": True, "reason": "수정뿐", "story": "상영관 영사기를 손봤다."},
              runner=lambda k, i: (runs.append(k), (True, "ok"))[1])
    from datetime import timedelta
    assert updater.step(T0, playing=1, **go) == [] and runs == []                            # 재생 중이면 기다린다
    assert updater.step(NOON, playing=0, **go) == [] and runs == []                          # 낮에는 기다린다
    ev = updater.step(T0 + timedelta(days=1), playing=0, **go)
    assert runs == ["plex"] and ev[0]["kind"] == "updated" and "1.43.5.11079" in ev[0]["text"]
    failed = updater.step(T0 + timedelta(days=2), playing=0, checker={"plex": dict(info, up="1.43.6.1-ccccccccc")}.get,
                          decide=lambda f: {"update": True}, runner=lambda k, i: (False, "E: broken"))
    assert failed[0]["kind"] == "failed" and "apt-get install plexmediaserver=1.43.4.10000-aaaaaaaaa" in failed[0]["text"]


def test_bot_updates_itself_anytime_and_checks_it_came_back(monkeypatch, tmp_path):
    """이 봇 자신: 재생 중·낮·높은 단계여도 한다. 배포를 따로 띄우고, 다시 켜졌는지는 다음 번에 본다"""
    import os
    from datetime import timedelta
    app = updater.APPS["discord_bot"]
    assert app["kind"] == "self" and app["local"] and app["anytime"] and app["run"] == "scripts/git_pull_deploy.sh"
    assert os.path.isfile(os.path.join(app["dir"], app["run"]))                         # 기본 경로 = 이 저장소
    _env(monkeypatch, tmp_path, stage=5, playing=3)
    assert updater.guard("discord_bot", NOON, _info(), playing=3) is None                # 재생 3개 · 5단계 · 낮이어도
    assert "로컬" in updater.guard("discord_bot", NOON, _info(dirty=2), playing=0)      # 로컬 수정이 있으면 안 한다
    # 실행 명령: 이 서버에서, 따로 띄워서 (봇이 꺼져도 배포는 이어진다)
    scripts = []
    monkeypatch.setattr(updater, "_exec", lambda a, script, timeout: (scripts.append((a.get("local"), script)), (0, "started"))[1])
    assert updater.run_update("discord_bot", _info()) == (True, "started")
    assert scripts[0][0] is True and "setsid nohup" in scripts[0][1] and "scripts/git_pull_deploy.sh" in scripts[0][1]
    # 흐름: 새 버전 -> 판단 -> 바로 실행 -> '다시 켜는 중' 알림
    go = dict(checker={"discord_bot": _info()}.get, decide=lambda f: {"update": True, "reason": "수정뿐", "story": "안내 창구를 손봤다."},
              runner=lambda k, i: (True, "started"), playing=3)
    ev = updater.step(NOON, **go)
    assert [e["kind"] for e in ev] == ["updated"] and "다시 켜는 중" in ev[0]["text"]
    posts = []
    updater.notify(ev, NOON, post=posts.append)
    assert "etc `discord_bot`" in posts[0]
    # 같은 프로세스로 5분 뒤: 아직 기다린다 / 10분 넘게 그대로면 실패 알림
    assert updater.step(NOON + timedelta(minutes=5), **go) == []
    late = updater.step(NOON + timedelta(minutes=12), checker={"discord_bot": _info(head="b" * 40, up="b" * 40)}.get, playing=0)
    assert [e["kind"] for e in late] == ["failed"] and "다시 켜지지 않았어요" in late[0]["text"]
    # 다시 켜진 경우(다른 프로세스): 조용히 지운다
    updater.step(NOON + timedelta(hours=2), **go)
    monkeypatch.setattr(updater.os, "getpid", lambda: 999999)
    assert updater.step(NOON + timedelta(hours=3), checker={"discord_bot": _info(head="b" * 40, up="b" * 40)}.get) == []
    assert "restart" not in updater._state()["discord_bot"]
