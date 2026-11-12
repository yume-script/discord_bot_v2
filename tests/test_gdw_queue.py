"""원고 입고 대기열: bookoasis_assistant gdw_status → 숫자·한 줄·단계, 달라질 때만 알림."""
from datetime import datetime

from poring_food import gdw_queue


def _status(pending=0, waiting=0, failed=0, oldest="", alive=True):
    return {"worker": {"alive": alive, "activity": "스캔 중"}, "counts": {"pending": pending, "waiting": waiting, "failed": failed, "done": 900},
            "pending_detail": {"buffering": 2, "screening": 0, "ready": pending - 2 if pending > 2 else 0, "waiting": waiting, "oldest": oldest},
            "warnings": ["보관함 '공유 서재'의 경로 share:x는 절대 경로가 아니어서 쓰지 않습니다."]}


def test_digest_line_levels():
    now = datetime(2026, 11, 12, 13, 0)
    d = gdw_queue.digest(_status(12, 3, 4, "2026-11-12T12:25:00"), now)
    assert (d["pending"], d["failed"], d["oldest_min"], d["level"], d["alive"]) == (15, 4, 35, 0, True)
    assert gdw_queue.line(d) == "입고 대기 15건(가장 오래된 것 35분) · 실패 4건"
    assert gdw_queue.digest(_status(40, 0, 0, "2026-11-12T12:55:00"), now)["level"] == 1            # 건수로
    assert gdw_queue.digest(_status(3, 0, 0, "2026-11-12T11:30:00"), now)["level"] == 1             # 오래 기다린 것으로
    assert gdw_queue.digest(_status(200, 0, 0, "2026-11-12T12:59:00"), now)["level"] == 2
    assert gdw_queue.line(gdw_queue.digest(_status(0, 0, 0), now)) == "입고 대기 없음"
    assert gdw_queue.line(gdw_queue.digest(_status(5, 0, 0, alive=False), now)) == "입고 워커가 멈춰 있음 · 대기 5건"
    assert gdw_queue.digest(None) is None and gdw_queue.line(None) == ""


def test_notice_only_on_change():
    now = datetime(2026, 11, 12, 13, 0)
    calm = gdw_queue.digest(_status(2, 0, 0), now)
    busy = gdw_queue.digest(_status(40, 0, 0, "2026-11-12T12:00:00"), now)
    assert gdw_queue.notice(calm, {}) is None
    text, title = gdw_queue.notice(busy, {})
    assert "대기 40건" in text and "1시간 0분" in text and title == "원고 입고 대기 40건"
    assert gdw_queue.notice(busy, {"level": 1}) is None                                             # 같은 단계면 조용히
    assert gdw_queue.notice(calm, {"level": 1})[1] == "원고 입고 대기 해소"
    dead = gdw_queue.digest(_status(5, 0, 0, alive=False), now)
    assert gdw_queue.notice(dead, {})[1] == "원고 입고 워커 멈춤" and gdw_queue.notice(dead, {"dead": True}) is None
    assert gdw_queue.notice(calm, {"dead": True})[1] == "원고 입고 워커 재가동"
    fails = gdw_queue.digest(_status(1, 0, 12), now)
    assert gdw_queue.notice(fails, {})[1] == "원고 입고 실패 12건" and gdw_queue.notice(fails, {"fail": True}) is None


def test_check_posts_and_reacts(monkeypatch, tmp_path):
    monkeypatch.setattr(gdw_queue, "URL", "http://assistant.test")
    monkeypatch.setattr(gdw_queue, "TOKEN", "tok")
    monkeypatch.setattr(gdw_queue, "STATE_PATH", str(tmp_path / "q.json"))
    now = datetime(2026, 11, 12, 13, 0)
    box = {"s": _status(40, 0, 0, "2026-11-12T12:00:00")}
    monkeypatch.setattr(gdw_queue, "fetch", lambda timeout=8: box["s"])
    posts, reacts = [], []
    assert gdw_queue.check(now, posts.append, lambda t, title, who: reacts.append((title, who))) == "원고 입고 대기 40건"
    assert "📦 원고 입고" in posts[0] and reacts[0][0] == "원고 입고 대기 40건" and reacts[0][1]
    assert gdw_queue.check(now, posts.append, lambda *a: reacts.append(a)) == "" and len(posts) == 1    # 그대로면 다시 알리지 않는다
    box["s"] = _status(0, 0, 0)
    assert gdw_queue.check(now, posts.append, lambda t, title, who: reacts.append((title, who))) == "원고 입고 대기 해소"
    assert "입고 대기 없음" in gdw_queue.summary(now) or "원고 입고 대기열" in gdw_queue.summary(now)
