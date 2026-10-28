"""결계 조절: 단계(0~4) - 시청 경로는 절대 안 건드리고, 희생 가능한 것부터 쉬게 했다가 역순으로 되돌린다."""
from datetime import datetime, timedelta

from poring_food import loadshed

T0 = datetime(2026, 10, 25, 20, 0)


def _probe(index, psi=None):
    return {"index": index, "cpu": index, "busy": index, "mem": 30.0, "psi": psi or {}}


def test_parsers():
    a = "cpu  100 50 100 800 0 0 0 0 0 0"
    b = "cpu  190 100 190 820 0 0 0 0 0 0"
    assert loadshed.cpu_from(a, b) == 92.0                        # 전체 (nice 포함)
    assert loadshed.busy_from(a, b) == 72.0                       # 지수용 - nice(50틱)는 안 센다
    assert loadshed.cpu_from(a, a) is None and loadshed.busy_from("x", b) is None
    assert loadshed.parse_stats("tautulli\t0.35%\nplex\t180.2%\nbad line\n") == {"tautulli": 0.35, "plex": 180.2}
    out = (f"{a}\n{b}\nMemTotal: 1000 kB\nMemAvailable: 200 kB\n"
           "psi_cpu some avg10=12.50 avg60=1 avg300=0 total=1\npsi_io some avg10=3.00 avg60=0 avg300=0 total=1\npsi_memory \n")
    p = loadshed.parse_probe(out)
    assert p["mem"] == 80.0 and p["busy"] == 72.0 and p["index"] == 80.0       # 지수 = max(CPU, 메모리, I/O)
    assert p["psi"]["cpu"] == 12.5 and p["psi"]["memory"] is None and loadshed.psi_alarm(p["psi"])
    assert loadshed.parse_probe("garbage") is None


def test_watching_path_is_never_touched(monkeypatch):
    monkeypatch.setenv("X_TEST", "tautulli, bad;rm -rf /,ok-name, bookoasis_mariadb, flaskfarm, Plex, shyni, vaultwarden")
    assert loadshed._names("X_TEST", "") == ["tautulli", "ok-name"]
    for n in ("bookoasis", "bookoasis_redis", "plexmediaserver.service", "Plex Media Scanner", "rclone-gds2",
              "litellm-proxy", "YM_PLAYER", "flaskfarm"):
        assert loadshed.protected(n), n
    assert not loadshed.protected("bookoasis_assistant.service") and not loadshed.protected("docker-warproxy")
    assert loadshed._docker("plex01", "stop", "bookoasis_mariadb") is False
    assert loadshed._throttle("plex01", {"do": "throttle", "name": "flaskfarm", "kind": "cpus"}) is None
    assert all(not loadshed.protected(n) for cfg in loadshed.HOSTS.values() for n in cfg["stop"] + cfg["units"])


def _run(indexes, start=T0, etc=None, playing=None, psi=None, running=None, docker_log=None, slow_log=None, struggle="same"):
    """매분 한 번씩 step - indexes는 분마다 plex01 지수 (etc는 etc 값, 없으면 같은 값). 반환: 한 일 전부."""
    done = []

    def throttler(h, a):
        if slow_log is not None:
            slow_log.append((h, a["do"], a["kind"], a["name"], a.get("weight"), a.get("orig")))
        return {"orig": 100} if a["do"] == "throttle" else {}
    for i, c in enumerate(indexes):
        now = start + timedelta(minutes=i)
        readings = {"etc": {"probe": _probe(c if etc is None else etc), "running": {"docker-warproxy": 0.1, "litellm-proxy": 1.0}},
                    "plex01": {"probe": _probe(c, psi), "running": running if running is not None
                               else {"bookoasis_mariadb": 0.5, "flaskfarm": 3.0, "bookoasis": 1.0}},
                    "_playing": playing, "_struggle": playing if struggle == "same" else struggle}
        done += loadshed.step(now, measurer=lambda r=readings: r, throttler=throttler,
                              docker=lambda h, do, n: (docker_log.append((h, do, n)) if docker_log is not None else None) or True)
    return done


def _stage(host="plex01"):
    return loadshed._load()["hosts"][host].get("stage", 0)


def test_stage_climbs_one_step_every_five_minutes_and_slows_carrier_not_stops():
    docker, slow = [], []
    done = _run([87] * 18, playing=1, docker_log=docker, slow_log=slow)
    stages = [(d["host"], d["to"]) for d in done if d["do"] == "stage" and d["host"] == "plex01"]
    assert stages == [("plex01", 1), ("plex01", 2), ("plex01", 3), ("plex01", 4)]   # 한 번에 한 단계씩
    assert ("etc", "stop", "docker-warproxy") in docker                       # 1단계: 놀고 있는 희생 가능 시설
    assert ("plex01", "throttle", "unit", "bookoasis_assistant.service", 30, None) in slow    # 재생 중 4단계(85%): 짐꾼은 끄지 않고 늦춤
    assert not [x for x in docker if x[1] == "stop" and x[2] != "docker-warproxy"]           # 시청 경로는 그대로
    slow.clear()
    _run([99] * 3, start=T0 + timedelta(minutes=18), playing=1, slow_log=slow)
    assert _stage() == 5 and ("plex01", "throttle", "unit", "bookoasis_assistant.service", 15, None) in slow
    assert loadshed._load()["hosts"]["plex01"]["throttled"]["unit:bookoasis_assistant.service"]["orig"] == 100
    assert loadshed.stage(T0 + timedelta(minutes=21)) == 5 and loadshed.max_cpu(T0 + timedelta(minutes=21)) == 99


def test_spike_jumps_to_top_and_short_blip_is_only_watched():
    _run([40, 40, 40, 99], etc=10)
    assert _stage() == 0                                                     # 1분만 튐 - 5분 평균은 낮다
    done = _run([97] * 3, start=T0 + timedelta(minutes=10), etc=10)
    st = [d for d in done if d["do"] == "stage"]
    assert st and st[-1]["to"] == 5 and st[-1]["alert"]                      # 96% 이상 급등 -> 바로 5단계 + 경고


def test_playback_lowers_thresholds_and_psi_escalates():
    _run([67] * 3, etc=10)
    assert _stage() == 0                                                     # 재생 없음: 70% 아래는 평시
    _run([67] * 3, start=T0 + timedelta(minutes=10), etc=10, playing=2)
    assert _stage() == 1                                                     # 재생 중: 65%부터 1단계
    loadshed._save({})
    _run([30] * 3, etc=10, psi={"io": 15.0})
    assert _stage() == 1                                                     # 지수는 낮아도 실제 지연(PSI)이면 한 단계


def test_hysteresis_and_reverse_restore():
    slow, docker = [], []
    _run([87] * 18, playing=1, docker_log=docker, slow_log=slow)
    assert _stage() == 4
    slow.clear()
    _run([83] * 15, start=T0 + timedelta(minutes=18), playing=1, slow_log=slow)
    assert _stage() == 4 and not slow                                        # 85 아래지만 82(85-3%p)보다 높다 -> 그대로
    _run([80] * 11, start=T0 + timedelta(minutes=33), playing=1, slow_log=slow)
    assert _stage() == 3                                                     # 10분 평균 82 아래 -> 한 단계만 내림
    assert ("plex01", "unthrottle", "unit", "bookoasis_assistant.service", None, 100) in slow   # 원래 값으로
    docker.clear()
    _run([30] * 45, start=T0 + timedelta(minutes=44), playing=1, docker_log=docker)
    assert _stage() == 0 and _stage("etc") == 0
    assert docker == [("etc", "start", "docker-warproxy")]                   # 직접 내린 것만 다시 올림
    assert not loadshed._load()["hosts"]["plex01"].get("throttled")


def test_ttl_restores_even_while_high():
    slow = []
    _run([97] * 4, playing=1, slow_log=slow)
    assert _stage() == 5
    slow.clear()
    later = T0 + timedelta(hours=loadshed.SLOW_TTL_H, minutes=5)
    _run([97] * 3, start=later, playing=1, slow_log=slow)
    assert slow[0][:4] == ("plex01", "unthrottle", "unit", "bookoasis_assistant.service")    # ttl 지나면 되돌림
    assert not [x for x in slow if x[1] == "throttle"]                                       # 방금 되돌린 건 잠시 쉼(쿨다운)


def test_old_version_throttles_on_watching_path_are_undone_right_away():
    loadshed._save({"hosts": {"plex01": {
        "shed": {"tautulli": "2026-10-25T19:00:00"},
        "throttled": {"cpus:bookoasis_mariadb": {"kind": "cpus", "name": "bookoasis_mariadb", "orig": 0, "at": "2026-10-25T19:00:00"},
                      "nice:Plex Media Scanner": {"kind": "nice", "name": "Plex Media Scanner", "orig": 0, "at": "2026-10-25T19:01:00"}}}}})
    slow, docker = [], []
    _run([95], slow_log=slow, docker_log=docker)                              # 부하가 높아도
    assert {x[3] for x in slow if x[1] == "unthrottle"} == {"bookoasis_mariadb", "Plex Media Scanner"}
    assert [x[3] for x in slow if x[1] == "unthrottle"][0] == "Plex Media Scanner"       # 마지막에 한 것부터
    assert ("plex01", "start", "tautulli") in docker
    st = loadshed._load()["hosts"]["plex01"]
    assert not st.get("throttled") and not st.get("shed")


def test_announce_story_and_facilities_ignore_quiet():
    from poring_food import facilities, memory
    posts, sent = [], []
    loadshed.announce([{"host": "etc", "do": "stop", "name": "docker-warproxy", "why": "결계 경보 1단계"},
                       {"host": "plex01", "do": "stage", "name": "결계 경보", "from": 3, "to": 4, "why": "5분 평균 95%",
                        "alert": True}], T0,
                      post=posts.append, react=lambda d, title, key: sent.append((title, key)) or [])
    assert "성문 전령 초소" in posts[0] and "슈나" in posts[0]
    assert "4단계" in posts[1] and "⚠️" in posts[1] and "환영극과 책 열람은 그대로" in posts[1]
    assert sent[0][0] == "성문 전령 초소 잠시 소등 (결계 조절)" and sent[1][0].startswith("결계 경보 4단계")
    assert sent[1][1][:3] == ["슈나", "베니마루", "애순이"]
    assert any("불을 잠시 껐다" in m["text"] for m in memory.store.memories("슈나"))
    # 결계 조절로 끈 docker-warproxy는 facilities가 "불 꺼짐" 사고로 알리지 않는다
    _run([85] * 3)
    etc = list(facilities.HOSTS)[0]
    got = []
    facilities.check(T0, lambda: {etc: [{"name": "docker-warproxy", "up": True, "started": "a"},
                                        {"name": "litellm-proxy", "up": True, "started": "a"}]}, inject=lambda *a: got.append(a))
    facilities.check(T0 + timedelta(minutes=7), lambda: {etc: [{"name": "docker-warproxy", "up": False, "started": "a"},
                                                              {"name": "litellm-proxy", "up": False, "started": "a"}]},
                     inject=lambda t, d, title: got.append(title) or [])
    assert got == ["수비대 마나 중계탑 불 꺼짐"]


def test_throttle_story():
    posts = []
    loadshed.announce([{"host": "plex01", "do": "throttle", "name": "bookoasis_assistant.service", "kind": "unit", "why": "x"},
                       {"host": "plex01", "do": "unthrottle", "name": "upload", "kind": "rclone", "why": "y"}],
                      T0, post=posts.append, react=lambda *a: [])
    assert "도서관 두루마리 운반 정령으로 가는 마나 흐름을 좁혔다" in posts[0] and "🐢" in posts[0]
    assert "왕도로 가는 두루마리 짐마차로" in posts[1] and "되돌렸다" in posts[1]
    assert "1~5단계" in loadshed.summary(T0)


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
    _run([20])
    got = loadshed.running_by_server(T0 + timedelta(seconds=30))
    assert got[loadshed.HOSTS["plex01"]["docker_mcp"]] == ["bookoasis", "bookoasis_mariadb", "flaskfarm"]


def test_old_version_samples_do_not_trigger_stage_after_upgrade():
    old = [[(T0 - timedelta(minutes=m)).isoformat(timespec="seconds"), 95.0] for m in range(5, 0, -1)]
    loadshed._save({"hosts": {"plex01": {"samples": old}, "etc": {"samples": old}}})
    done = _run([30])
    assert not [d for d in done if d["do"] == "stage"] and _stage() == 0     # 옛 전체 CPU 기록으로 4단계가 되지 않는다
    assert loadshed._load()["hosts"]["plex01"]["samples"] == [[T0.isoformat(timespec="seconds"), 30, False]]


def test_steady_psi_alarm_does_not_trap_stage_at_top():
    slow = []
    _run([97] * 4, playing=1, slow_log=slow)
    assert _stage() == 5
    slow.clear()
    _run([35] * 60, start=T0 + timedelta(minutes=4), playing=1, psi={"cpu": 25.0}, slow_log=slow)
    assert _stage() == 1                                                     # 지수 35% + PSI 경보 = 1단계까지는 내려온다
    assert ("plex01", "unthrottle", "unit", "bookoasis_assistant.service", None, 100) in slow   # 짐꾼은 원래 속도로


def test_clamav_on_host_follows_stage_through_village_ctl():
    assert loadshed.parse_ctl("quota clamav-risk-scan.service 25%\nquota clamav-deep-scan.service 25%\n"
                              "stopped clamav-deep-scan.service\n", 2) == [
        {"host": "zeeps", "do": "stop", "name": "clamav-deep-scan.service",
         "why": "결계 경보 2단계 - 정기 스캔은 다음 타이머 때 다시 (실시간 감시는 유지)"},
        {"host": "zeeps", "do": "throttle", "name": "clamav", "kind": "quota", "why": "결계 경보 2단계 - 방역관 CPU 25%로"}]
    assert loadshed.parse_ctl("quota clamav-risk-scan.service 60%\n", 0)[0]["do"] == "unthrottle"
    assert loadshed.parse_ctl("", 1) == []                                   # 바뀐 게 없으면 조용히
    sent, posts = [], []

    def writer(n):
        sent.append(n)
        return loadshed.parse_ctl("quota clamav-risk-scan.service 25%\n" if n >= 1 and len(sent) == 3 else "", n)
    done = []
    for i in range(3):
        done += loadshed.step(T0 + timedelta(minutes=i), stage_writer=writer, throttler=lambda h, a: {},
                              docker=lambda *a: True,
                              measurer=lambda: {"etc": {"probe": _probe(82), "running": {}},
                                                "plex01": {"probe": _probe(30), "running": {}}})
    assert sent == [0, 0, 1]                                                 # 매분 지금 단계를 보낸다 (게이트가 신선도를 본다)
    assert [d["name"] for d in done if d["host"] == "zeeps"] == ["clamav"]
    loadshed.notify([d for d in done if d["host"] == "zeeps"], T0, post=posts.append)
    assert "방역관의 정화 의식(ClamAV)" in posts[0] and "🐢" in posts[0]


def test_carrier_keeps_working_at_88_when_nobody_watches_but_yields_while_watching():
    slow = []
    _run([88] * 20, slow_log=slow)
    assert _stage() == 3 and not [x for x in slow if x[1] == "throttle"]     # 아무도 안 보면 88%여도 짐꾼은 그대로
    loadshed._save({})
    _run([88] * 20, playing=1, slow_log=slow)
    assert _stage() == 4 and ("plex01", "throttle", "unit", "bookoasis_assistant.service", 30, None) in slow   # 보는 중이면 양보


def test_cpu_is_averaged_over_the_minute_not_one_second():
    store = {}
    a = "cpu  1000 0 1000 8000 0 0 0 0 0 0"
    one_sec = {"index": 99.0, "busy": 99.0, "mem": 40.0, "psi": {"io": 2.0}, "raw": a}
    assert loadshed.smooth(one_sec, store, "2026-10-28T08:00:00", "2026-10-28T07:55:00")["index"] == 99.0   # 처음엔 그대로
    b = "cpu  1300 0 1300 13400 0 0 0 0 0 0"                                  # 지난 1분: 600 바쁨 / 6000
    got = loadshed.smooth({**one_sec, "raw": b}, store, "2026-10-28T08:01:00", "2026-10-28T07:56:00")
    assert got["busy"] == 10.0 and got["index"] == 40.0                      # 1분 평균 10% -> 지수는 메모리 40%
    assert loadshed.smooth({**one_sec, "raw": a}, {"raw": b, "raw_at": "2026-10-28T07:00:00"},
                           "2026-10-28T08:02:00", "2026-10-28T07:57:00")["index"] == 99.0      # 지난 값이 너무 오래됨


def test_single_spikes_do_not_keep_stage_at_top_for_hours():
    _run([97] * 4)
    assert _stage() == 5
    noisy = [45, 60, 99, 50, 47, 62, 99, 48, 55, 46] * 6                    # 평균 61%인데 가끔 99%가 튐 (예전엔 몇 시간 갇힘)
    _run(noisy, start=T0 + timedelta(minutes=4), psi={"cpu": 29.0})
    assert _stage() <= 2


def test_ymplayer_telegram_and_live_count_as_watching(monkeypatch):
    assert loadshed.ymplayer_playing(lambda: {"by": {"telegram": 1}}) is None            # 주소/열쇠가 없으면 안 묻는다
    monkeypatch.setattr(loadshed, "YMPLAYER_URL", "http://ym:8899")
    monkeypatch.setattr(loadshed, "YMPLAYER_TOKEN", "t")
    assert loadshed.ymplayer_playing(lambda: {"playing": 3, "by": {"plex": 1, "telegram": 1, "live": 1}}) == 2   # Plex는 빼고
    assert loadshed.ymplayer_playing(lambda: {"by": {}}) == 0
    assert loadshed.ymplayer_playing(lambda: None) is None


def test_watching_counts_only_no_titles(monkeypatch):
    monkeypatch.setattr(loadshed, "YMPLAYER_URL", "http://ym:8899")
    monkeypatch.setattr(loadshed, "YMPLAYER_TOKEN", "t")
    sessions = [{"media_type": "episode", "title": "비밀 제목"}, {"media_type": "movie", "title": "x"},
                {"media_type": "episode", "title": "y"}]
    w = loadshed.watching(sessions, {"plex": 1, "telegram": 2, "live": 1})
    assert w["total"] == 6 and w["plex_kinds"] == {"드라마/시리즈": 2, "영화": 1}   # Plex 3 + 텔레그램 2 + 라이브 1
    text = loadshed.watching_text(T0, w)
    assert "모두 **6**개" in text and "드라마/시리즈 2" in text and "텔레그램 2" in text and "그중 YM_PLAYER로 1" in text
    assert "비밀 제목" not in text and "단계 기준값(재생 중): 1~5단계 = 65/70/75/85/90%" in text
    none = loadshed.watching_text(T0, {"plex": None, "plex_kinds": {}, "ym": None, "total": None})
    assert "못 받았어요" in none
    assert loadshed.ymplayer_activity(lambda: {"by": {"telegram": "3"}}) == {"plex": 0, "telegram": 3, "live": 0,
                                                                            "stalls": 0, "retries": 0}


def test_chat_can_ask_watching_now(monkeypatch):
    from core.tool_policy import is_server_affecting
    from poring_food import tools
    tool = next(t for t in tools.PORING_FOOD_TOOLS if t.name == "get_watching_now")
    assert "몇 명 보고 있어" in tool.description and not is_server_affecting(tool.name)   # 조회라 누구나
    monkeypatch.setattr(loadshed, "watching", lambda: {"plex": 1, "plex_kinds": {"영화": 1}, "ym": None, "total": 1})
    text = tools.get_watching_now()
    assert "Plex로 확인된 재생 **1**개" in text and "모름" in text and "있을 수 있음" in text   # YM_PLAYER를 모르면 단정 안 함


def test_nobody_watching_means_carrier_is_never_slowed():
    slow = []
    _run([99] * 6, slow_log=slow)
    assert _stage() == 5 and not [x for x in slow if x[1] == "throttle"]     # 아무도 안 보면 5단계여도 짐꾼은 그대로
    _run([99] * 2, start=T0 + timedelta(minutes=6), playing=1, slow_log=slow)
    assert ("plex01", "throttle", "unit", "bookoasis_assistant.service", 15, None) in slow   # 누가 틀면 바로 양보
    slow.clear()
    done = _run([99], start=T0 + timedelta(minutes=8), slow_log=slow)        # 다 보고 껐다
    assert slow[0][:4] == ("plex01", "unthrottle", "unit", "bookoasis_assistant.service")
    assert any(d.get("why") == "재생이 없어 자료 등록 우선" for d in done)


def test_carrier_yields_again_right_away_when_someone_starts_watching_again():
    slow = []
    _run([99] * 4, playing=1, slow_log=slow)
    _run([99] * 2, start=T0 + timedelta(minutes=4), slow_log=slow)            # 껐다 (제한 풀림)
    slow.clear()
    _run([99], start=T0 + timedelta(minutes=6), playing=1, slow_log=slow)     # 2분 만에 다시 틀었다
    assert slow and slow[0][1] == "throttle"                                 # 30분 쿨다운 없이 바로 양보


def test_smooth_playback_means_carrier_is_not_slowed():
    from poring_food import plex_api
    assert plex_api.struggling({"state": "buffering"})
    assert plex_api.struggling({"state": "playing", "transcode_speed": 0.8})
    assert not plex_api.struggling({"state": "playing", "transcode_speed": 0.8, "transcode_throttled": True})
    assert not plex_api.struggling({"state": "playing", "transcode_speed": 3.2})
    assert not plex_api.struggling({"state": "playing"})                     # 원본 재생, 잘 나옴
    s = plex_api.to_session({"type": "movie", "Player": {"state": "playing"}, "TranscodeSession": {"speed": "0.9"}})
    assert s["transcode_speed"] == 0.9 and plex_api.struggling(s)
    slow = []
    _run([97] * 6, playing=2, struggle=0, slow_log=slow)
    assert _stage() == 5 and not [x for x in slow if x[1] == "throttle"]     # 보는 중이어도 잘 나오면 짐꾼은 그대로
    _run([97], start=T0 + timedelta(minutes=6), playing=2, struggle=1, slow_log=slow)
    assert [x for x in slow if x[1] == "throttle"]                           # 버벅이면 바로 양보
    slow.clear()
    _run([97] * 5, start=T0 + timedelta(minutes=7), playing=2, struggle=0, slow_log=slow)
    assert not [x for x in slow if x[1] == "unthrottle"]                     # 10분은 버벅이는 걸로 본다
    done = _run([97] * 6, start=T0 + timedelta(minutes=12), playing=2, struggle=0, slow_log=slow)
    assert [x for x in slow if x[1] == "unthrottle"]
    assert any(d.get("why") == "재생이 잘 나와서(버퍼링 없음) 자료 등록 우선" for d in done)
    text = loadshed.watching_text(T0, loadshed.watching([{"media_type": "movie", "state": "buffering"}], None))
    assert "버벅이는 재생 1" in text


def test_ymplayer_stalls_and_retries_count_as_struggling(monkeypatch):
    from poring_food import plex_api
    monkeypatch.setattr(loadshed, "YMPLAYER_URL", "http://ym:8899")
    monkeypatch.setattr(loadshed, "YMPLAYER_TOKEN", "t")
    monkeypatch.setattr(plex_api, "enabled", lambda: False)
    monkeypatch.setattr("poring_food.metrics.streams", lambda: None)
    body = {"playing": 1, "by": {"plex": 0, "telegram": 1, "live": 0},
            "stalls": {"plex": 0, "telegram": 2, "live": 0}, "retries": {"plex": 0, "telegram": 0, "live": 1}, "window": 300}
    act = loadshed.ymplayer_activity(lambda: body)
    assert act["stalls"] == 2 and act["retries"] == 1
    monkeypatch.setattr(loadshed, "ymplayer_activity", lambda get=None: act)
    assert loadshed._viewing() == (1, 3)                                    # 텔레그램 1개 재생, 최근 5분 끊김 2 + 재연결 1
    monkeypatch.setattr(loadshed, "ymplayer_activity", lambda get=None: {**act, "stalls": 0, "retries": 0})
    assert loadshed._viewing() == (1, None)                                 # 잘 나오면 버벅임 없음
    text = loadshed.watching_text(T0, {"plex": 0, "plex_kinds": {}, "ym": act, "total": 1, "struggle": 0})
    assert "최근 5분 끊김 2 / 재연결 1" in text
