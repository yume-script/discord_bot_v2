"""책방 손님 알림: 북오아시스에서 새로 읽은 기록이 생기면 단톡방에 알리고, 처음은 기준만, 심야 서고는 제목 없이 책방 안에서만."""
import json
from datetime import datetime

from poring_food import agents, book_watch


def _row(book_id, title, at, pages=10, db="general", series="", lib="일반 도서", done=0):
    return {"book_id": book_id, "pages_read": pages, "is_completed": done, "last_read_at": at, "title": title,
            "series_name": series, "total_pages": 200, "library_name": lib, "db_type": db}


def _posts():
    try:
        with open(agents.GROUP_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def test_first_is_baseline_then_new_read_announced(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    fake_llm(lambda system, user: {"text": "책방에 손님이 오셨어요!"})
    now = datetime(2026, 10, 14, 15, 0)
    base = [_row(1, "어린 왕자", "2026-10-14 10:00:00")]
    assert book_watch.has_new(base)                       # 처음 - 기준을 적으러
    assert book_watch.process(base, now) == [] and _posts() == []
    assert not book_watch.has_new(base)                   # 그대로면 할 일 없음
    rows = [_row(1, "어린 왕자", "2026-10-14 14:55:00", pages=30), _row(2, "나 혼자만 레벨업", "2026-10-14 14:58:00",
                                                                     series="나 혼자만 레벨업", lib="웹툰")]
    assert book_watch.has_new(rows)
    got = book_watch.process(rows, now)
    assert [g["title"] for g in got] == ["어린 왕자", "나 혼자만 레벨업"]
    posts = _posts()
    assert posts[-1]["name"] == "애순이" and posts[-1]["group"] == agents.TOWN_GROUP and "📚" in posts[-1]["text"]
    # 같은 책을 계속 읽어 쪽수만 바뀌면 다시 안 알린다
    rows[0] = _row(1, "어린 왕자", "2026-10-14 15:10:00", pages=50)
    assert book_watch.process(rows, datetime(2026, 10, 14, 15, 10)) == []
    assert "어린 왕자" in book_watch.prompt_block("오아시스 거리", datetime(2026, 10, 14, 15, 20))


def test_adult_shelf_hidden_and_inside_shop_only(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    monkeypatch.setattr(book_watch, "BOOKOASIS_ADULT_DB_TYPES", ["adult"])
    fake_llm(lambda system, user: {"text": "심야 서고에 손님 한 분."})
    now = datetime(2026, 10, 14, 21, 0)
    book_watch.process([_row(9, "비밀 화보집", "2026-10-14 20:00:00", db="adult")], now)
    got = book_watch.process([_row(9, "비밀 화보집", "2026-10-14 20:55:00", db="adult", pages=40)], now)
    assert got == [{"work": "adult|비밀 화보집", "title": ""}]
    p = _posts()[-1]
    assert p["name"] == "김동식" and p["group"] == "책방 단톡방" and "비밀 화보집" not in p["text"]
    assert book_watch.prompt_block("오아시스 거리", now) == ""              # 동네 사람은 모른다
    block = book_watch.prompt_block("책방 오아시스", now)
    assert "심야 서고 책 한 권" in block and "비밀 화보집" not in block
