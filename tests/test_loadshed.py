"""결계 조절: 부하가 오래 높으면 놀고 있는 허용 컨테이너만 내리고, 내려가면 직접 내린 것만 다시 올린다."""
from datetime import datetime, timedelta

from poring_food import loadshed

T0 = datetime(2026, 10, 25, 20, 0)


def test_parsers():
    a = "cpu  100 0 100 800 0 0 0 0 0 0"
    b = "cpu  190 0 190 820 0 0 0 0 0 0"
    assert loadshed.cpu_from(a, b) == 90.0                       # 200틱 중 쉰 건 20틱
    assert loadshed.cpu_from(a, a) is None and loadshed.cpu_from("x", b) is None
    assert loadshed.parse_stats("tautulli\t0.35%\nplex\t180.2%\nbad line\n") == {"tautulli": 0.35, "plex": 180.2}


def _run_minutes(cpus, start=T0, running=None, docker_log=None):
    """매분 한 번씩 step - cpus는 분마다 plex01 부하. 반환: 마지막 step에서 한 일까지 전부."""
    done = []
    for i, c in enumerate(cpus):
        now = start + timedelta(minutes=i)
        readings = {"etc": {"cpu": 10.0, "running": {"docker-warproxy": 0.1}},
                    "plex01": {"cpu": c, "running": running if running is not None else {"tautulli": 0.4, "plex": 150.0}}}
        done += loadshed.step(now, measurer=lambda r=readings: r,
                              docker=lambda h, do, n: (docker_log.append((h, do, n)) if docker_log is not None else None) or True)
    return done


def test_sheds_idle_allowed_container_after_sustained_high_and_restores_after_low():
    log = []
    done = _run_minutes([95] * 6, docker_log=log)
    assert log == [("plex01", "stop", "tautulli")]                # plex는 목록에 없어서 안 건드림, etc는 부하가 낮음
    assert done[0]["do"] == "stop" and "tautulli" in loadshed.quiet_names(T0 + timedelta(minutes=6))
    assert loadshed.max_cpu(T0 + timedelta(minutes=6)) == 95
    log.clear()
    _run_minutes([50] * 10, start=T0 + timedelta(minutes=6), docker_log=log)
    assert log == []                                              # 아직 15분이 안 됐다
    _run_minutes([50] * 7, start=T0 + timedelta(minutes=16), docker_log=log)
    assert log == [("plex01", "start", "tautulli")]
    st = loadshed._load()["hosts"]["plex01"]
    assert not st.get("shed") and "tautulli" in st["restored"]


def test_short_spike_or_busy_container_is_left_alone():
    log = []
    _run_minutes([95, 95, 40, 95, 95, 95], docker_log=log)        # 중간에 내려간 적 있음 -> 5분 연속 아님
    assert log == []
    _run_minutes([95] * 7, start=T0 + timedelta(hours=1), running={"tautulli": 12.0}, docker_log=log)
    assert log == []                                              # 일하는 중(12%)이면 안 내림
    _run_minutes([95] * 7, start=T0 + timedelta(hours=2), running={}, docker_log=log)
    assert log == []                                              # 원래 꺼져 있던 건 상관없음


def test_max_off_hours_and_cooldown():
    log = []
    _run_minutes([95] * 6, docker_log=log)
    log.clear()
    later = T0 + timedelta(hours=loadshed.MAX_OFF_HOURS, minutes=10)
    _run_minutes([95], start=later, docker_log=log)
    assert log == [("plex01", "start", "tautulli")]               # 너무 오래 꺼져 있으면 부하가 높아도 다시 올림
    log.clear()
    _run_minutes([95] * 6, start=later + timedelta(minutes=1), docker_log=log)
    assert log == []                                              # 방금 올린 건 잠시 다시 안 내림


def test_announce_story_and_facilities_ignore_quiet(monkeypatch):
    from poring_food import facilities, memory
    posts, sent = [], []
    loadshed.announce([{"host": "plex01", "do": "stop", "name": "tautulli", "why": "결계 부하 95%"}], T0,
                      post=posts.append, inject=lambda t, d, title: sent.append(title) or [])
    assert "환영극장 관객 세는 수정구" in posts[0] and "지은탁" in posts[0] and sent == ["환영극장 관객 세는 수정구 잠시 소등 (결계 조절)"]
    assert any("불을 잠시 껐다" in m["text"] for m in memory.store.memories("지은탁"))
    # 결계 조절로 끈 tautulli는 facilities가 "불 꺼짐" 사고로 알리지 않는다
    _run_minutes([95] * 6)
    plex = facilities.HOSTS and list(facilities.HOSTS)[1]
    base = {plex: [{"name": "tautulli", "up": True, "started": "a"}, {"name": "flaskfarm", "up": True, "started": "a"}]}
    got = []
    facilities.check(T0, lambda: base, inject=lambda *a: got.append(a))
    facilities.check(T0 + timedelta(minutes=7), lambda: {plex: [{"name": "tautulli", "up": False, "started": "a"},
                                                                {"name": "flaskfarm", "up": False, "started": "a"}]},
                     inject=lambda t, d, title: got.append(title) or [])
    assert got == ["환영극장 이야기 배급소 불 꺼짐"]


def test_unsafe_names_are_dropped(monkeypatch):
    monkeypatch.setenv("X_TEST", "tautulli, bad;rm -rf /,ok-name")
    assert loadshed._names("X_TEST", "") == ["tautulli", "ok-name"]
