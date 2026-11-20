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
