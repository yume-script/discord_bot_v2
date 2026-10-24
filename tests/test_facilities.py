"""감시탑: docker 컨테이너가 멈추거나 다시 켜지면 감시탑 불 꺼짐/결계 균열 사건, 처음은 기준만, 조회 실패는 건너뛴다."""
import json
from datetime import datetime
from types import SimpleNamespace

from poring_food import chronicle, facilities

ETC, PLEX = "docker_local", "docker_bookoasis"


def _c(name, up=True, started="2026-10-14T00:00:00Z", health=None):
    state = {"Running": up, "Restarting": False, "StartedAt": started}
    if health:
        state["Health"] = {"Status": health}
    return {"name": name, "status": "running" if up else "exited", "state": state}


def _seen(d):
    """docker MCP가 돌려주는 모양 -> fetch()가 넘기는 모양 (parse_containers를 거친 것)."""
    return {k: facilities.parse_containers(SimpleNamespace(structuredContent={"result": v}, content=[])) for k, v in d.items()}


def test_parse_structured_and_text():
    res = SimpleNamespace(structuredContent={"result": [_c("bookoasis"), _c("tautulli", health="unhealthy")]}, content=[])
    got = facilities.parse_containers(res)
    assert [(c["name"], c["up"]) for c in got] == [("bookoasis", True), ("tautulli", False)]
    res = SimpleNamespace(structuredContent=None, content=[SimpleNamespace(text=json.dumps(_c("flaskfarm", up=False)))])
    assert facilities.parse_containers(res)[0] == {"name": "flaskfarm", "up": False, "started": "2026-10-14T00:00:00Z"}


def test_diff_down_up_restart_blackout():
    now = datetime(2026, 10, 14, 15, 3)
    base = {PLEX: [_c("bookoasis"), _c("bookoasis_redis"), _c("tautulli"), _c("flaskfarm")],
            ETC: [_c("vaultwarden"), _c("litellm-proxy")]}
    state, ev = facilities.diff({}, _seen(base), now)
    assert ev == [] and state["containers"]["tautulli"]["up"]            # 처음은 기준만
    # 극장 배급소가 꺼지고, 열쇠집은 재시작
    seen = {PLEX: [_c("bookoasis"), _c("bookoasis_redis"), _c("tautulli"), _c("flaskfarm", up=False)],
            ETC: [_c("vaultwarden", started="2026-10-14T06:00:00Z"), _c("litellm-proxy")]}
    state, ev = facilities.diff(state, _seen(seen), now)
    kinds = {(e["kind"], e["name"]) for e in ev}
    assert ("down", "flaskfarm") in kinds and ("restart", "vaultwarden") in kinds
    down = next(e for e in ev if e["kind"] == "down")
    assert down["targets"] == ["별빛 환영극장"] and "이야기 배급소" in down["title"]
    # 다시 켜짐
    seen[PLEX][-1] = _c("flaskfarm")
    state, ev = facilities.diff(state, _seen(seen), datetime(2026, 10, 14, 16, 3))
    assert [(e["kind"], e["resolves"]) for e in ev] == [("up", "환영극장 이야기 배급소 불 꺼짐")]
    # 구역 전체 재시작 = 결계 균열 하나로
    later = "2026-10-14T17:00:00Z"
    seen[PLEX] = [_c(n, started=later) for n in ("bookoasis", "bookoasis_redis", "tautulli", "flaskfarm")]
    state, ev = facilities.diff(state, _seen(seen), datetime(2026, 10, 14, 17, 3))
    assert [e["kind"] for e in ev] == ["blackout"] and "도서관·극장 쪽 성벽" in ev[0]["title"]
    assert set(ev[0]["targets"]) == {"오아시스 마법도서관", "별빛 환영극장"}
    # 사라진 컨테이너는 멈춤으로
    seen[ETC] = [_c("vaultwarden", started="2026-10-14T06:00:00Z")]
    state, ev = facilities.diff(state, _seen(seen), datetime(2026, 10, 14, 18, 3))
    assert [(e["kind"], e["name"]) for e in ev] == [("down", "litellm-proxy")]


def test_check_delivers_and_resolves_thread():
    sent = []

    def inject(targets, detail, title):
        sent.append((targets, title))
        chronicle.open_thread(title, detail)
        return ["a", "b"]

    base = {PLEX: [_c("bookoasis_redis")]}
    assert facilities.check(datetime(2026, 10, 14, 9), lambda: _seen(base), inject) == []      # 기준만
    assert facilities.check(datetime(2026, 10, 14, 10), lambda: {}, inject) == []       # 조회 실패 = 건너뜀
    facilities.check(datetime(2026, 10, 14, 11), lambda: _seen({PLEX: [_c("bookoasis_redis", up=False)]}), inject)
    assert sent == [(["오아시스 마법도서관"], "도서관 대출대 수정구 불 꺼짐")]
    assert "대출대 수정구" in facilities.prompt_block()
    facilities.check(datetime(2026, 10, 14, 12), lambda: _seen(base), inject)
    assert not any(t["title"] == "도서관 대출대 수정구 불 꺼짐" for t in chronicle.open_threads())
    assert facilities.prompt_block() == ""


def test_check_with_real_inject_reaches_shop_people_only():
    from poring_food import agents
    base = _seen({PLEX: [_c("tautulli")]})
    facilities.check(datetime(2026, 10, 14, 9), lambda: base)
    ev = facilities.check(datetime(2026, 10, 14, 10), lambda: _seen({PLEX: [_c("tautulli", up=False)]}))
    assert ev and ev[0]["kind"] == "down"
    got = {n for n, st in agents._load_state().items() if any("관객 세는 수정구" in m.get("text", "") for m in st.get("inbox", []))}
    by = {a["name"]: a for a in agents.load_agents()}
    assert got and all(by[n]["company"] == "별빛 환영극장" for n in got)
    t = next(t for t in chronicle.open_threads() if "관객 세는 수정구" in t["title"])
    assert chronicle._scope_of(t) == "별빛 환영극장"
