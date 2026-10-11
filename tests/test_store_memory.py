"""SQLite 영구 기록: 예전 jsonl 옮기기, 기억 저장/떠올리기/정리(지우지 않고 보관), 사건/행동 기록."""
import json
import os

from poring_food import chronicle, memory, store


def test_legacy_jsonl_imported_once(state_dir):
    os.makedirs(os.path.join(state_dir, "archive"), exist_ok=True)
    with open(os.path.join(state_dir, "memories.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"name": "소라", "at": "2026-10-01T10:00", "text": "커피 원두를 바꿨다", "importance": 4},
                           ensure_ascii=False) + "\n")
    with open(os.path.join(state_dir, "archive", "memories.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"name": "소라", "at": "2026-09-01T10:00", "text": "강단이와 공모전 얘기", "importance": 3},
                           ensure_ascii=False) + "\n")
    with open(os.path.join(state_dir, "archive", "events.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-10-01T09:00", "kind": "세계", "title": "정전", "detail": "공장 정전",
                            "targets": ["포링푸드"]}, ensure_ascii=False) + "\n")
    assert [m["text"] for m in memory._load("소라")] == ["커피 원두를 바꿨다"]
    assert [m["text"] for m in store.memories("소라", archived=True)] == ["강단이와 공모전 얘기"]
    assert store.events_on("2026-10-01")[0]["title"] == "정전"
    assert os.path.exists(os.path.join(state_dir, "memories.jsonl.imported"))
    store._ready.clear()  # 다시 열어도 두 번 옮기지 않는다
    assert len(memory._load("소라")) == 1


def test_remember_recall_and_archived_recall():
    memory.remember("애순이", "유메미루가 커피를 사 줬다", 6, ["유메미루"], "판단")
    memory.remember("애순이", "라인 점검", 2)
    assert {m["text"] for m in memory.recall("애순이", ["유메미루"], k=1)} == {"유메미루가 커피를 사 줬다"}
    assert "애순이" in memory.all_names()


def test_prune_archives_but_keeps_permanent(monkeypatch):
    monkeypatch.setattr(memory, "PRUNE_AT_LINES", 1)
    monkeypatch.setattr(memory, "MAX_PER_PERSON", 1)
    memory.remember("김정봉", "마을 이름은 포근동으로 정했다", 9, permanent=True)
    memory.remember("김정봉", "로또 번호 연구", 2)
    memory.remember("김정봉", "우표 정리", 2)
    memory.prune()
    kept = [m["text"] for m in memory._load("김정봉")]
    assert "마을 이름은 포근동으로 정했다" in kept and "우표 정리" in kept and "로또 번호 연구" not in kept
    # 정리된 기억도 지워지지 않고, 화제가 겹치면 다시 떠오른다
    assert "로또 번호 연구" in [m["text"] for m in memory.recall("김정봉", ["로또"], k=5)]


def test_events_and_actions_recorded():
    chronicle.record_event("모임", "마을 이름 짓기 (1차)", "결론 없음", ["여화정"], decided=False)
    chronicle.record_action("여화정", {"location": "주민센터", "activity": "민원 정리", "state": "일하는 중"})
    from poring_food.clock import now_kst
    today = now_kst().strftime("%Y-%m-%d")
    ev = store.events_on(today)
    assert ev[0]["title"] == "마을 이름 짓기 (1차)" and ev[0]["decided"] is False
    assert store.actions_on(today)[0]["name"] == "여화정"


def test_lookup_shows_events_and_projects():
    from poring_food import projects
    from poring_food.clock import now_kst
    chronicle.record_event("이사", "새 이웃 마현이", "이태원 클라쓰 사람들이 이사 옴", ["마현이"])
    projects.create("골목 화단 만들기", "꽃길", ["a", "b"], "여화정", [], "동네", [], "모임")
    text = chronicle.lookup(now_kst().strftime("%Y-%m-%d"))
    assert "새 이웃 마현이" in text and "골목 화단 만들기" in text
