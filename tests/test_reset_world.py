"""세계 초기화: 이야기 기록은 백업 후 지우고, 실제 데이터 캐시는 남기고, 1일차 시작 + 첫 주 사건 씨앗."""
import json
import os
from datetime import datetime

from poring_food import chronicle, memory, reset_world, world, worldcfg
from poring_food.config import STATE_DIR


def test_reset_backs_up_wipes_story_and_keeps_caches(tmp_path, monkeypatch):
    memory.remember("애순이", "예전 공장 이야기", 7, [], "업무")
    chronicle.open_thread("포링 젤리 신제품", "공장 일")
    for name in ("news.json", "metrics.json"):
        with open(os.path.join(STATE_DIR, name), "w") as f:
            f.write("{}")
    now = datetime(2026, 10, 15, 9)
    res = reset_world.reset(now=now)
    assert res["backup"] and os.path.isdir(res["backup"])
    assert os.path.exists(os.path.join(res["backup"], "threads.json"))
    try:
        assert "news.json" in res["kept"] and "metrics.json" in res["kept"] and "threads.json" in res["removed"]
        assert not memory.recall("애순이", k=5)
        titles = [t["title"] for t in chronicle.all_threads()]
        assert titles == ["오아시스 마을의 시작"] and chronicle.facts(worldcfg.STREET)
        assert worldcfg.start_date() == "2026-10-15" and worldcfg.day_number("2026-10-16") == 2
        assert "주막 남숙" in worldcfg.seed_block("2026-10-15") and "마을 회의" in worldcfg.seed_block("2026-10-16")
        assert worldcfg.seed_block("2026-10-30") == ""
        assert reset_world.main([]) == 1          # --yes 없이는 안 지운다
    finally:
        import shutil
        shutil.rmtree(res["backup"], ignore_errors=True)


def test_world_plan_gets_seed(fake_llm, monkeypatch):
    with open(os.path.join(STATE_DIR, worldcfg.START_PATH_NAME), "w") as f:
        json.dump({"date": "2026-10-15"}, f)
    seen = {}

    def handler(system, user):
        if "세계 엔진" in system:
            seen["user"] = user
        return {"events": []}

    fake_llm(handler)
    from poring_food import agents, characters
    world._plan_day(datetime(2026, 10, 15, 6, 3), agents.load_agents(), characters.load_roster(), "", [])
    assert "1일차" in seen["user"] and "주막 남숙" in seen["user"]
