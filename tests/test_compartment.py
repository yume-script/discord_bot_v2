"""정보 칸막이: 가게 안의 일은 그 가게 사람만 안다 (동네 사람이 책방/극장 안의 숫자 다툼을 들먹이지 않게)."""
from poring_food import agents, chronicle

BOOK, CINEMA, TOWN = "책방 오아시스", "별빛극장", "오아시스 거리"


def _by(name):
    return next(a for a in agents.load_agents() if a["name"] == name)


def test_thread_scope_filters_prompt_block():
    chronicle.open_thread("권민우의 수작: 매출 장부 조작", "책방 매출 숫자가 이상하다는 말", scope="책방")
    chronicle.open_thread("골목 월세 인상", "상가번영회가 월세를 올린다", scope="")
    chronicle.open_thread("극장 편성표 꼬임", "편성표가 엉망", scope=CINEMA)
    town = chronicle.prompt_block(TOWN)
    assert "골목 월세 인상" in town and "장부 조작" not in town and "편성표" not in town
    book = chronicle.prompt_block(BOOK)
    assert "장부 조작" in book and "골목 월세 인상" in book and "편성표" not in book
    assert "[책방 오아시스 안의 일]" in book
    god = chronicle.prompt_block()            # 세계 엔진(전지적 시점)은 다 본다
    assert "장부 조작" in god and "편성표" in god


def test_legacy_thread_scope_is_inferred():
    # scope가 없는 기록: 가게 이름 그대로나 한 가게 사람만 나오면 그 가게 일 ('극장 앞 골목'처럼 짧은 말은 동네 일)
    chronicle._save_threads([
        {"id": "a", "title": "권민우 보고서 논란", "status": "open", "since": "2026-10-10T09:00", "updates": []},
        {"id": "b", "title": "별빛극장 기획전 회의", "status": "open", "since": "2026-10-10T09:00", "updates": []},
        {"id": "c", "title": "극장 앞 골목 이름 짓기", "status": "open", "since": "2026-10-10T09:00", "updates": []},
    ])
    assert [t["title"] for t in chronicle.open_threads(TOWN)] == ["극장 앞 골목 이름 짓기"]
    assert [t["title"] for t in chronicle.open_threads(BOOK)] == ["권민우 보고서 논란", "극장 앞 골목 이름 짓기"]
    assert len(chronicle.open_threads(CINEMA)) == 2


def test_villain_scheme_thread_stays_in_shop(fake_llm):
    from poring_food import characters, villains
    from datetime import datetime

    def handler(system, user):
        if "다음 수를 꾸미는" in system and "'장대희'" in system:
            return {"target": "오동백", "title": "편성표 바꿔치기", "action": "오동백의 편성표가 다른 이름으로 투자사에 올라갔다.",
                    "rumor": "편성표 기획자가 바뀌었다는 말", "hidden": False, "truth": "장대희가 했다", "effects": {}}
        return {}

    fake_llm(handler)
    villains.tick(agents.load_agents(), characters.load_roster(), datetime(2026, 10, 12, 11, 3))
    assert any("편성표 바꿔치기" in t["title"] for t in chronicle.open_threads(CINEMA))
    assert not any("편성표 바꿔치기" in t["title"] for t in chronicle.open_threads(TOWN))
    assert not any("편성표 바꿔치기" in t["title"] for t in chronicle.open_threads(BOOK))


def test_shop_gossip_does_not_leak_to_town(monkeypatch):
    for k in ("GOSSIP_P_SAME_PLACE", "GOSSIP_P_COLLEAGUE", "GOSSIP_P_REGULAR", "GOSSIP_P_OTHER"):
        monkeypatch.setattr(agents, k, 1.0)
    monkeypatch.setattr(agents, "GOSSIP_MAX_RECIPIENTS", 100)
    t = [{"speaker": "권민우", "line": "애순이 사장님 장부 숫자 손댄 거 제가 다 압니다"}]
    got = agents._gossip(_by("권민우"), _by("최수연"), "직접", "책방 오아시스(계산대)", t, {})
    assert got and all(_by(n)["company"] == BOOK for n, _ in got)
    # 가게 밖(골목)에서 동네 사람끼리 한 얘기는 예전처럼 동네로 돈다
    got = agents._gossip(_by("부상길"), _by("홍두식"), "직접", "골목", t, {})
    assert any(_by(n)["company"] != TOWN for n, _ in got)


def test_system_has_knowledge_boundary():
    s = agents._system(_by("부상길"))
    assert "아는 것의 한계" in s and "책방 오아시스" in s and "별빛극장" in s and "사람이 아니다" in s
    assert "건물 일" in s
    s = agents._system(_by("권민우"))
    assert "나는 책방 오아시스 사람이다" in s and "별빛극장" in s


def test_day_summary_keeps_shop_section(fake_llm):
    chronicle.open_thread("권민우 보고서", "", scope=BOOK)

    def handler(system, user):
        assert "가게 안의 일" in user
        return {"summary": "하루.", "public": "골목에 비가 왔다.", "highlights": [],
                "company": {"책방 오아시스": "권민우 보고서로 계산대가 시끄러웠다.", "별빛극장": ""},
                "threads": [{"title": "심야 서고 가격 다툼", "status": "open", "note": "사장이 화냄", "scope": "책방"},
                            {"title": "골목 축제", "status": "open", "note": "준비 시작", "scope": "동네"}]}

    fake_llm(handler)
    c = chronicle._summarize_day("2026-10-12")
    assert c["company"] == {BOOK: "권민우 보고서로 계산대가 시끄러웠다."}
    chronicle._append(chronicle.CHRONICLE_PATH, c)
    town, book = chronicle.prompt_block(TOWN), chronicle.prompt_block(BOOK)
    assert "골목에 비가 왔다" in town and "계산대가 시끄러웠다" not in town and "가격 다툼" not in town
    assert "골목 축제" in town
    assert "계산대가 시끄러웠다" in book and "가격 다툼" in book


def test_shop_groups_and_one_channel(monkeypatch):
    monkeypatch.setattr(agents.bot_settings, "PORING_AGENT_CHANNEL_ID", 777)
    assert agents._groups_of(_by("애순이"))[0] == "책방 단톡방"
    assert agents._groups_of(_by("오동백")) == ["극장 단톡방"]
    assert {agents._group_channel(g) for g in ("책방 단톡방", "극장 단톡방", "동네 단톡방")} == {777}


def test_talk_header_shows_what_speaker_does():
    from datetime import datetime
    h = agents._talk_header("메신저", "", datetime(2026, 10, 14, 14, 36), "윤윤제", "양관식")
    assert h == "-# (거리 골목 이웃들 변호사) 📱 메신저 · 14:36 · 윤윤제 → 양관식"
    assert agents.role_tag("김동식") == "(책방 심야 서고 과장)"
    assert agents.role_tag("소라") == "(극장 로비 카페 사장)"
    assert agents.role_tag("없는사람") == ""
