"""거리 프로젝트(거리 범위는 누구나 참여), 새 이웃(예전 이름 재사용 금지), 하루 교체(소속별 인원 유지)."""
from datetime import datetime

from poring_food import agents, characters, memory, projects, rotation
from poring_food.clock import KST


def test_town_project_open_to_rotated_in_agent():
    p = projects.create("골목 화단 만들기", "꽃길", ["장소", "모종", "심기"], "구영철", ["민재현"], "거리",
                        ["구영철", "민재현"], "모임")
    assert p
    res = projects.contribute("골목 화단", "새로온사람", "흙을 날랐다")  # 만들 때 명단에 없던 사람
    assert res and res["gain"] == projects.POINTS["other"]
    assert "새로온사람" in res["project"]["helpers"]


def test_company_project_keeps_members():
    projects.create("사내 텃밭", "옥상 텃밭", ["a", "b"], "윤채아", [], "회사", ["윤채아", "애순이"], "모임")
    assert projects.contribute("사내 텃밭", "외부인", "구경") is None


def test_project_daily_cap_and_completion():
    projects.create("벤치", "골목 벤치", ["설계", "조립"], "진상필", [], "거리", [], "모임")
    total = sum(projects.contribute("벤치", f"주민{i}", "도움")["gain"] for i in range(10))
    assert total == projects.DAILY_CAP  # 하루 상한 - 며칠에 걸쳐 된다


def test_newcomers_skip_names_used_before(fake_llm):
    memory.remember("박새로이", "옛 솥뚜껑 주점 사장의 기억", 5)  # 예전에 있었던 사람
    fake_llm(lambda s, u: {"drama": "이태원 클라쓰", "people": [
        {"name": "박새로이", "rank": "사장"}, {"name": "문하늘", "rank": "매니저"},  # 문하늘 = ZEEPS 영상콘텐츠팀 에이전트
        {"name": "길라임", "rank": "트레이너"},  # 정리된 옛 거리 사람
        {"name": "마현이", "rank": "요리사", "outer_persona": "o", "inner_truth": "i", "key_behavior": "k"}]})
    res = agents.move_in_newcomers()
    assert [r["name"] for r in res["added"]] == ["마현이"]
    assert any(c["name"] == "마현이" and c["dept"].startswith("새 이웃") for c in characters.load_roster())


def test_rotation_keeps_quotas_and_fixed(fake_llm, monkeypatch):
    """회사 두 부서는 대기 인원이 없어 고정 - 거리 사람만 하루 한 명 바뀐다."""
    fake_llm(lambda s, u: {"profile": "p", "routine": "r", "speech": "s", "sleep": [1, 7], "hangouts": []})
    monkeypatch.setattr(rotation, "now_kst", lambda: datetime(2026, 10, 12, 6, 5, tzinfo=KST))
    before = agents.load_agents()
    swaps = rotation.tick(agents._base_agents(), agents.MAX_AGENTS)
    after = agents.load_agents()
    assert len(swaps) == 1 and swaps[0][0] == "오아시스 거리" and len(after) == len(before)
    count = lambda xs, c: sum(1 for a in xs if a.get("company") == c)  # noqa: E731
    for company in rotation.COMPANIES:
        assert count(before, company) == count(after, company)
    names = {a["name"] for a in after}
    assert {"애순이", "소라", "윤채아", "이나연", "구영철"} <= names
    assert rotation.tick(agents._base_agents(), agents.MAX_AGENTS) == []  # 같은 날 두 번은 안 바꾼다


def test_newcomers_get_modern_korean_names(fake_llm):
    """애니메이션 인물이 이사 와도 이야기에는 지금의 한국 이름으로 (작품 속 이름은 motif에만)"""
    from poring_food import newcomers
    fake_llm(lambda system, user: {"drama": "전생했더니 슬라임이었던 건에 대하여", "people": [
        {"name": "베르도라", "rank": "시설 수리 기사", "outer_persona": "호탕함", "inner_truth": "외로움", "key_behavior": "큰소리"},
        {"name": "한지우", "motif": "디아블로", "rank": "비서", "outer_persona": "깍듯함", "inner_truth": "충성", "key_behavior": "미소"}]})
    res = newcomers.move_in(set())
    names = [r["name"] for r in res["added"]]
    assert "베르도라" not in names and "한지우" in names
    assert all(newcomers.korean_name(n) for n in names)
    assert {r["motif"] for r in res["added"]} == {"베르도라", "디아블로"}


def test_v4_renames_old_newcomers_and_fantasy_words():
    import json
    import os
    from poring_food import newcomers, rename_migration
    from poring_food.config import STATE_DIR
    with open(newcomers.NEWCOMERS_PATH, "w", encoding="utf-8") as f:
        json.dump({"residents": [{"name": "베르도라", "rank": "기사"}, {"name": "위즈", "rank": "가게 주인"}],
                   "moved_out": [{"name": "카이진"}]}, f, ensure_ascii=False)
    with open(os.path.join(STATE_DIR, "threads.json"), "w", encoding="utf-8") as f:
        json.dump([{"topic": "위즈을(를) 둘러싼 일: 별빛 환영극장에 상회 광고판, 마탑 개입", "who": "위즈덤 클럽"}], f, ensure_ascii=False)
    with open(os.path.join(STATE_DIR, "chronicle.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"text": "베르도라와 디아블로가 거리의 새로운 인물로 등장했다. 카이진의 대장간"}, ensure_ascii=False) + "\n")
    rename_migration._migrate_v4()
    res = newcomers._load()
    new_names = [r["name"] for r in res["residents"]] + [r["name"] for r in res["moved_out"]]
    assert all(newcomers.korean_name(n) for n in new_names)
    assert [r["motif"] for r in res["residents"]] == ["베르도라", "위즈"]
    threads = open(os.path.join(STATE_DIR, "threads.json"), encoding="utf-8").read()
    assert "위즈을" not in threads and new_names[1] + "을" in threads
    assert "위즈덤 클럽" in threads                       # 다른 낱말 속 글자는 그대로
    assert "ZEEPS 영상콘텐츠팀 시사회장" in threads and "마탑" not in threads and "상가 광고판" in threads
    chron = open(os.path.join(STATE_DIR, "chronicle.jsonl"), encoding="utf-8").read()
    assert "베르도라" not in chron and new_names[0] in chron and "디아블로" in chron   # 이사 기록이 없는 이름은 그대로
    rename_migration._migrate_v4()                         # 한 번만
    assert os.path.exists(rename_migration.MARKER_V4)
