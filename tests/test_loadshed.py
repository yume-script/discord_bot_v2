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


def _run_minutes(cpus, start=T0, running=None, docker_log=None, slow_log=None):
    """매분 한 번씩 step - cpus는 분마다 plex01 부하. 반환: 한 일 전부. 늦추기는 slow_log에 (실제 명령은 안 부른다)."""
    done = []

    def throttler(h, a):
        if slow_log is not None:
            slow_log.append((h, a["do"], a["kind"], a["name"], a.get("cap"), a.get("orig")))
        return {"orig": 4_000_000_000 if a["kind"] == "cpus" else None} if a["do"] == "throttle" else {}
    for i, c in enumerate(cpus):
        now = start + timedelta(minutes=i)
        readings = {"etc": {"cpu": 10.0, "running": {"docker-warproxy": 0.1}},
                    "plex01": {"cpu": c, "running": running if running is not None else {"tautulli": 0.4, "plex": 150.0}}}
        done += loadshed.step(now, measurer=lambda r=readings: r, throttler=throttler,
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
                      post=posts.append, react=lambda d, title, key: sent.append((title, key)) or [])
    assert "환영극장 관객 세는 수정구" in posts[0] and "슈나" in posts[0]
    assert sent[0][0] == "환영극장 관객 세는 수정구 잠시 소등 (결계 조절)"
    assert sent[0][1][:2] == ["슈나", "베니마루"] and any(n in sent[0][1] for n in ("소라", "일레이나", "크리스"))   # 극장 사람
    assert any("불을 잠시 껐다" in m["text"] for m in memory.store.memories("슈나"))
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


def test_throttles_busy_containers_unit_and_process_then_restores_originals():
    slow = []
    busy = {"tautulli": 0.4, "flaskfarm": 58.0, "bookoasis_mariadb": 100.3, "bookoasis": 0.1}
    done = _run_minutes([95] * 6, running=busy, slow_log=slow)
    assert ("plex01", "throttle", "cpus", "flaskfarm", 1.0, None) in slow
    assert ("plex01", "throttle", "cpus", "bookoasis_mariadb", 2.0, None) in slow
    assert ("plex01", "throttle", "unit", "bookoasis_assistant.service", None, None) in slow
    assert ("plex01", "throttle", "nice", "Plex Media Scanner", None, None) in slow
    assert not any(n == "bookoasis" for _, _, _, n, _, _ in slow)          # 목록에 없는 건 안 건드림
    assert {d["do"] for d in done} == {"stop", "throttle"}
    st = loadshed._load()["hosts"]["plex01"]["throttled"]
    assert st["cpus:flaskfarm"]["orig"] == 4_000_000_000
    slow.clear()
    _run_minutes([40] * 17, start=T0 + timedelta(minutes=6), running=busy, slow_log=slow)
    assert ("plex01", "unthrottle", "cpus", "flaskfarm", None, 4_000_000_000) in slow    # 원래 상한(4코어)으로 되돌림
    assert len([x for x in slow if x[1] == "unthrottle"]) == 4 and not loadshed._load()["hosts"]["plex01"].get("throttled")
    assert "늦춤" in loadshed.summary(T0 + timedelta(minutes=30)) or "되돌림" in loadshed.summary(T0 + timedelta(minutes=30))


def test_quiet_container_is_not_capped():
    slow = []
    _run_minutes([95] * 6, running={"flaskfarm": 3.0, "bookoasis_mariadb": 1.0}, slow_log=slow)
    assert not [x for x in slow if x[2] == "cpus"]                        # 거의 안 쓰는 건 상한을 안 건다


def test_throttle_story():
    posts = []
    loadshed.announce([{"host": "plex01", "do": "throttle", "name": "flaskfarm", "kind": "cpus", "why": "x"},
                       {"host": "plex01", "do": "unthrottle", "name": "Plex Media Scanner", "kind": "nice", "why": "y"}],
                      T0, post=posts.append, react=lambda *a: [])
    assert "이야기 배급소으로" not in posts[0] and "마나 흐름을 좁혔다" in posts[0] and "🐢" in posts[0]
    assert "환영극장 서고 정리 정령" in posts[1] and "되돌렸다" in posts[1]


def test_process_and_cap_parsing(monkeypatch):
    monkeypatch.setenv("X_CAP", "flaskfarm:1, bad;x:2, db:0, ok:1.5")
    assert loadshed._caps("X_CAP", "") == {"flaskfarm": 1.0, "ok": 1.5}
    monkeypatch.setenv("X_P", "Plex Media Scanner, rm -rf;/")
    assert loadshed._procs("X_P", "") == ["Plex Media Scanner"]


def test_react_now_reaches_everyone_quietly_and_key_people_react_now(fake_llm):
    from poring_food import agents
    calls = []

    def llm(system, user):
        calls.append(system[:20])
        return {"consider": "어쩌지", "matters": 7, "react": "가만히", "thought": "결계가 걱정된다"}
    fake_llm(llm)
    key = loadshed.key_people(["flaskfarm"])
    got = agents.react_now("슈나가 이야기 배급소로 가는 마나 흐름을 좁혔다.", "이야기 배급소 마나 흐름 제한 (결계 조절)", key)
    awake_key = [n for n in key if not agents.is_asleep_now(n)]
    assert set(got) == set(awake_key)
    st = agents._load_state()
    someone_else = next(a["name"] for a in agents.load_agents() if a["name"] not in key)
    inbox = st[someone_else]["inbox"]
    assert inbox and inbox[-1].get("quiet") and not inbox[-1].get("urgent")      # 다른 사람은 조용히 안다
    assert len(calls) >= len(awake_key)


def test_facilities_quick_catches_manual_stop_and_start():
    from poring_food import facilities
    server = list(facilities.HOSTS)[1]
    facilities.check(T0, lambda: {server: [{"name": "flaskfarm", "up": True, "started": "a"},
                                           {"name": "tautulli", "up": True, "started": "a"}]}, inject=lambda *a: [])
    assert facilities.quick({server: ["tautulli"]}, T0 + timedelta(minutes=1))[0]["title"] == "환영극장 이야기 배급소 불 꺼짐"
    assert facilities.quick({server: ["tautulli"]}, T0 + timedelta(minutes=2)) == []        # 같은 일은 한 번만
    posts, reacted = [], []
    ev = facilities.quick({server: ["tautulli", "flaskfarm"]}, T0 + timedelta(minutes=3))
    assert ev[0]["kind"] == "up"
    facilities.notify(ev, T0, post=posts.append)
    facilities.react(ev, react_now=lambda d, title, key, targets=None: reacted.append((title, targets)) or [])
    assert "`flaskfarm` ▶ 켜짐" in posts[0] and reacted == [("환영극장 이야기 배급소 다시 불 켜짐", ["별빛 환영극장"])]
    # 매시 조회가 같은 상태를 보면 또 알리지 않는다
    sent = []
    facilities.check(T0 + timedelta(hours=1), lambda: {server: [{"name": "flaskfarm", "up": True, "started": "a"},
                                                              {"name": "tautulli", "up": True, "started": "a"}]},
                     inject=lambda *a: sent.append(a) or [])
    assert sent == []


def test_running_by_server_after_step():
    _run_minutes([20])
    got = loadshed.running_by_server(T0 + timedelta(seconds=30))
    assert got[loadshed.HOSTS["plex01"]["docker_mcp"]] == ["plex", "tautulli"]
