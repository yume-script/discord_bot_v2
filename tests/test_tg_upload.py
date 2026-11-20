"""텔레그램 자동 올리기 대기열 (poring_food/tg_upload.py) - BOA MCP tgup_status 결과를 디스코드 글로"""
from poring_food import gdw_queue, tg_upload

SAMPLE = {
    "running": [{"account": "첫 계정", "title": "엘리오 (2025)", "phase": "텔레그램에 올리는 중", "percent": 42.3, "elapsed_min": 12.4,
                 "channel": "영화 > 최신", "subs": "소프트 자막"}],
    "queue_total": 18,
    "queue": [{"pos": n, "title": f"영화{n}", "channel": "영화 > 최신", "account": "첫 계정"} for n in range(1, 16)],
    "waiting_subs": [{"title": "드라마 S01E03", "why": "영어 자막만", "waiting_min": 75}],
    "retry": [{"title": "실패한 것", "tries": 2, "in_min": 25}],
    "offline_drives": [], "off_hours": False, "skipped_total": 3, "error": "",
    "accounts": {"첫 계정": {"queued": 18, "ok": 120, "fail": 2}},
    "recent": [{"title": "발레리나", "result": "올림", "note": "그대로 올림", "ago_min": 5, "subs": ""},
               {"title": "중복된 것", "result": "건너뜀: 중복", "note": "건너뜀: 중복 - 같은 파일", "ago_min": 30, "subs": ""}],
}


def test_text(monkeypatch):
    monkeypatch.setattr(gdw_queue, "URL", "http://boa")
    monkeypatch.setattr(gdw_queue, "TOKEN", "t")
    out = tg_upload.text(SAMPLE)
    assert out.startswith("📤 **텔레그램 자동 올리기** - 올리는 중 1개 · 대기 18개 · 자막 기다림 1개 · 다시 시도 1개")
    assert "▶ [첫 계정] 엘리오 (2025) - 텔레그램에 올리는 중 42% · 12분째 → 영화 > 최신" in out
    assert "1. 영화1 → 영화 > 최신" in out and "15. 영화15" in out and "… 외 3개" in out
    assert "드라마 S01E03 (영어 자막만) · 1시간 15분째" in out
    assert "✅ 발레리나 (5분 전)" in out and "⏭️ 중복된 것 - 건너뜀: 중복 - 같은 파일 (30분 전)" in out
    assert "읽지 못했습니다" in tg_upload.text(None)


def test_fetch_calls_tgup_status(monkeypatch):
    monkeypatch.setattr(gdw_queue, "URL", "http://boa")
    monkeypatch.setattr(gdw_queue, "TOKEN", "t")
    calls = []
    monkeypatch.setattr(gdw_queue, "call_tool", lambda name, args=None, timeout=8: calls.append((name, args)) or SAMPLE)
    assert tg_upload.fetch() is SAMPLE and calls == [("tgup_status", {"limit": 30})]

    def boom(*a, **k):
        raise RuntimeError("알 수 없는 도구: tgup_status")
    monkeypatch.setattr(gdw_queue, "call_tool", boom)
    assert tg_upload.fetch() is None   # 예전 BOA (도구 없음) - 오류 대신 None


def test_disabled(monkeypatch):
    monkeypatch.setattr(gdw_queue, "URL", "")
    assert tg_upload.fetch() is None and "연결 안 됨" in tg_upload.text(None)


def test_local_read_tools_need_no_confirm():
    """조회만 하는 로컬 도구(get_·preview_·search_·read_feed)는 관리자 확인 없이 - 바꾸는 도구(set_·update_)만 확인"""
    from ai.local_tools import LOCAL_TOOLS
    from ai.rss_feed import RSS_FEED_TOOLS
    from core.tool_policy import is_server_affecting
    from poring_food.tools import PORING_FOOD_TOOLS
    for t in [*PORING_FOOD_TOOLS, *LOCAL_TOOLS, *RSS_FEED_TOOLS]:
        read_only = t.name.startswith(("get_", "preview_", "search_", "read_feed", "check_"))
        assert is_server_affecting(t.name) is (not read_only), t.name


def test_read_tools_by_name_and_hint():
    """조회 이름(get_·list_·search_…)이면 이름에 upload·add_·start가 섞여도 확인 없이, MCP readOnlyHint도 인정.
    관리자 전용 서버·강제 목록·파괴적인 말(delete 등)은 그대로 확인"""
    from types import SimpleNamespace as NS
    from core.tool_policy import is_server_affecting
    for name in ("get_recently_added", "list_started_jobs", "search_uploads", "get_upload_status", "fetch_post", "check_push_status"):
        assert not is_server_affecting(name), name
    for name in ("get_and_delete_cache", "check_and_restart", "list_create_table", "upload_file", "set_waitfree_window",
                 "read_query", "call_api"):
        assert is_server_affecting(name), name
    hinted = NS(metadata={"readOnlyHint": True})
    assert not is_server_affecting("sync_library_upload", hinted)            # 서버가 읽기 전용이라고 밝힘
    assert is_server_affecting("sync_library_upload", NS(metadata={"readOnlyHint": True, "destructiveHint": True}))
    assert is_server_affecting("sync_library_upload", NS(metadata={}))
    assert is_server_affecting("read_query", hinted)                          # 강제 관리자 전용이 먼저


def test_throttle_lines(monkeypatch):
    monkeypatch.setattr(gdw_queue, "URL", "http://boa")
    monkeypatch.setattr(gdw_queue, "TOKEN", "t")
    data = {**SAMPLE, "accounts": {"첫 계정": {"queued": 3, "upload": {"state": "limited", "speed": 0.8, "baseline": 9.5}}},
            "throttle_warnings": ["첫 계정: 텔레그램이 업로드를 늦춤 - 최근 10분 45초 대기 (FLOOD_PREMIUM_WAIT)"]}
    out = tg_upload.text(data)
    assert "🐢 첫 계정: 텔레그램이 업로드를 늦춤" in out and "업로드 속도: 첫 계정 0.8MB/s(평소 9.5)" in out
