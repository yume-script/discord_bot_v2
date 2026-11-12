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


FEED = {"computer": {"Linux": {"version": "999.1.2.3-abcdef012", "items_added": "(Player) 새 기능", "items_fixed": "(DVR) 고침\n(Web) 고침 둘",
                               "releases": [
                                   {"distro": "debian", "url": "https://downloads.plex.tv/plex-media-server-new/999.1.2.3-abcdef012/debian/plexmediaserver_999.1.2.3-abcdef012_ARCH.deb",
                                    "checksum": "a" * 40},
                                   {"distro": "redhat", "url": "https://downloads.plex.tv/x/plexmediaserver-999.x86_64.rpm", "checksum": "b" * 40}]}}}


def _deb_check(monkeypatch, tmp_path, version, package="bash"):
    """확인 프로그램을 이 컴퓨터에서 그대로 돌려 본다 (깔려 있는 꾸러미 bash를 Plex인 셈 치고, 내려받기 목록은 파일로)"""
    import json
    import shutil
    import subprocess
    if not shutil.which("dpkg-query"):
        pytest.skip("dpkg가 없는 환경")
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True).stdout.strip()
    feed = json.loads(json.dumps(FEED).replace("ARCH", arch))
    feed["computer"]["Linux"]["version"] = version
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed), encoding="utf-8")
    monkeypatch.setitem(updater.APPS["plex"], "package", package)
    monkeypatch.setitem(updater.APPS["plex"], "feed", path.as_uri())
    monkeypatch.setattr(updater.waitfree, "_remote", lambda script, timeout: (
        lambda p: (p.returncode, p.stdout + p.stderr))(subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60)))
    return updater.check("plex"), arch


def test_plex_check_reads_the_official_feed(monkeypatch, tmp_path):
    info, arch = _deb_check(monkeypatch, tmp_path, "999.1.2.3-abcdef012")
    assert info["up"] == info["new_version"] == "999.1.2.3-abcdef012" and info["head"] == info["version"] != info["up"]
    assert info["url"].endswith(f"_{arch}.deb") and info["sha1"] == "a" * 40 and info["dirty"] == 0
    assert info["log"] == ["- (Player) 새 기능", "- (DVR) 고침", "- (Web) 고침 둘"]
    older, _ = _deb_check(monkeypatch, tmp_path, "0.0.1-old")                     # 깔린 것보다 낮으면 최신으로 본다
    assert older["head"] == older["up"]
    missing, _ = _deb_check(monkeypatch, tmp_path, "999.1", package="no-such-package-zz")
    assert missing is None                                                        # 꾸러미로 안 깔려 있으면 대상이 아니다


def test_plex_update_installs_only_a_verified_official_deb(monkeypatch, tmp_path):
    app = updater.APPS["plex"]
    assert app["kind"] == "deb" and app["hours"] == (4, 8) and "plexmediaserver" in app["health"]
    scripts = []
    monkeypatch.setattr(updater.waitfree, "_remote", lambda script, timeout: (scripts.append(script), (0, "NOW 2 OLD 1 RC 0 HEALTH 0"))[1])
    good = {"head": "1.0.0.1-aaa", "up": "1.0.0.2-bbb", "sha1": "c" * 40,
            "url": "https://downloads.plex.tv/plex-media-server-new/1.0.0.2-bbb/debian/plexmediaserver_1.0.0.2-bbb_amd64.deb"}
    ok, _out = updater.run_update("plex", good)
    assert ok and len(scripts) == 1
    assert "sha1sum -c" in scripts[0] and "c" * 40 in scripts[0] and "dpkg -i" in scripts[0] and good["url"] in scripts[0]
    assert scripts[0].index("sha1sum -c") < scripts[0].index("dpkg -i")           # 확인이 먼저
    for bad in (dict(good, url="https://evil.example/plexmediaserver.deb"), dict(good, url=good["url"] + ";reboot"),
                dict(good, sha1="zz"), dict(good, url="http://downloads.plex.tv/a.deb")):
        ok, why = updater.run_update("plex", bad)
        assert not ok and "올바르지" in why
    assert len(scripts) == 1                                                      # 이상한 주소로는 아무것도 실행하지 않는다
    assert updater._short("a" * 40) == "aaaaaaa" and updater._short("1.43.5.11079-b3b13e506") == "1.43.5.11079"
    # 재생이 없어도 낮에는 하지 않는다
    assert "04~08시" in updater.guard("plex", T0.replace(hour=14), dict(good, dirty=0), playing=0)
    assert updater.guard("plex", T0.replace(hour=5), dict(good, dirty=0), playing=0) is None
    assert "재생 중" in updater.guard("plex", T0.replace(hour=5), dict(good, dirty=0), playing=1)
