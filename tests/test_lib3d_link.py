"""BookOasis 3D 서재 연결: 전화 받기(ask/report)와 배달(새 것만, 성인 제외, 첫 확인은 기준점만). 가짜 LLM으로."""
import pytest

from poring_food import agents, lib3d_link


def _roster(monkeypatch):
    roster = [{"name": "애순이", "company": "ZEEPS 편집국 (대표)", "speech": "반말", "profile": "대표"},
              {"name": "윤채아", "company": "ZEEPS 편집국 (사서)", "speech": "존댓말", "profile": "사서"}]
    monkeypatch.setattr(agents, "load_agents", lambda: roster)
    monkeypatch.setattr(agents, "active", lambda name: None)
    monkeypatch.setattr(agents, "is_asleep_now", lambda name: False)
    return roster


def test_call_token(monkeypatch):
    monkeypatch.setattr(lib3d_link, "CALL_TOKEN", "abc")
    assert lib3d_link.call_token_ok("Bearer abc")
    assert not lib3d_link.call_token_ok("Bearer abd")
    assert not lib3d_link.call_token_ok("")
    monkeypatch.setattr(lib3d_link, "CALL_TOKEN", "")
    assert not lib3d_link.call_token_ok("Bearer ")


def test_ask_and_report(monkeypatch, fake_llm):
    _roster(monkeypatch)
    fake = fake_llm(lambda system, user: {"text": "어서 와! 천천히 둘러봐.", "say": "손님이네"} if "들은 말" in user
                    else {"text": "수고했어, 내일은 신간 위주로."})
    out, note = lib3d_link.answer_call({"type": "ask", "user": "철수", "question": "추천해 줘", "agent": {"name": "애순이", "job": "대표"}})
    assert out == {"text": "어서 와! 천천히 둘러봐.", "say": "손님이네"}
    assert "3D 서재 전화" in note and "철수" in note
    assert "'애순이'" in fake.calls[-1][0]   # 그 인물의 시스템 프롬프트로
    out, note = lib3d_link.answer_call({"type": "report", "from": {"name": "한서윤", "job": "사서"}, "text": "손님 3명"})
    assert out["text"].startswith("수고했어")
    assert "한서윤(사서)" in note
    lib3d_link.remember_call(note)
    inbox = agents._load_state()["애순이"]["inbox"]
    assert inbox[-1]["kind"] == "통신" and inbox[-1]["quiet"]
    with pytest.raises(ValueError):
        lib3d_link.answer_call({"type": "delete"})


def test_call_llm_down(monkeypatch, fake_llm):
    _roster(monkeypatch)
    fake_llm(lambda system, user: {})
    out, note = lib3d_link.answer_call({"type": "ask", "question": "안녕"})
    assert out == {"text": ""} and note == ""


def test_new_items_baseline_and_filter(monkeypatch):
    monkeypatch.setattr(lib3d_link, "HIDE_LIBRARIES", ["숨김"])
    data = {"books": {"general": [{"display_name": "옛 책", "latest_added": "2026-11-01"}]},
            "plex": [{"type": "movie", "title": "옛 영화", "addedAt": 100}]}
    items, st = lib3d_link.new_items(data, {})
    assert items == [] and st["books"]["general"] == "2026-11-01" and st["plex"] == 100
    data = {"books": {"general": [{"display_name": "새 책", "latest_added": "2026-11-10"},
                                  {"display_name": "새 책", "latest_added": "2026-11-09"},
                                  {"display_name": "옛 책", "latest_added": "2026-11-01"}],
                      "audiobook": [{"display_name": "새 오디오북", "latest_added": "2026-11-10"}]},
            "plex": [{"type": "episode", "grandparentTitle": "새 드라마", "title": "1화", "addedAt": 200},
                     {"type": "movie", "title": "청불 영화", "contentRating": "청소년관람불가", "addedAt": 201},
                     {"type": "movie", "title": "숨김 영화", "librarySectionTitle": "숨김", "addedAt": 202},
                     {"type": "movie", "title": "옛 영화", "addedAt": 100}]}
    items, st = lib3d_link.new_items(data, st)
    assert items == [{"title": "새 책", "kind": "book"}, {"title": "새 드라마", "kind": "video"}]   # 오디오북은 첫 확인이라 기준점만
    assert st["plex"] == 202 and st["books"]["audiobook"] == "2026-11-10"


def test_check_and_deliver(monkeypatch, fake_llm):
    _roster(monkeypatch)
    fake_llm(lambda system, user: {"text": "상자 여기 둘게요!"})
    feeds = [{"books": {"general": [{"display_name": "옛 책", "latest_added": "2026-11-01"}]}, "plex": None},
             {"books": {"general": [{"display_name": "새 책", "latest_added": "2026-11-10"}]}, "plex": None}]

    def fake_run(coro, timeout):
        coro.close()
        return feeds.pop(0)
    monkeypatch.setattr(lib3d_link.runtime, "run_on_bot_loop", fake_run)
    sent = []
    monkeypatch.setattr(lib3d_link, "send_event", lambda body: sent.append(body) or {"success": True})
    monkeypatch.setattr(lib3d_link.memory, "remember", lambda *a, **k: None)
    assert lib3d_link.check_and_deliver() == "첫 확인 - 기준점만 저장"
    assert lib3d_link.check_and_deliver() == "윤채아 - 1건 배달"   # 배달꾼: 애순이(전화 받는 사람) 말고 편집국 사람
    assert sent == [{"type": "delivery", "from": "윤채아", "items": [{"title": "새 책", "kind": "book"}], "note": "상자 여기 둘게요!"}]
    assert agents._load_state()["윤채아"]["inbox"][-1]["kind"] == "일"


def test_delivery_not_saved_when_library_down(monkeypatch, fake_llm):
    _roster(monkeypatch)
    fake_llm(lambda system, user: {})
    lib3d_link._save_state({"books": {"general": "2026-11-01"}, "plex": 0, "checked": "x"})
    monkeypatch.setattr(lib3d_link.runtime, "run_on_bot_loop",
                        lambda coro, timeout: coro.close() or {"books": {"general": [{"display_name": "새 책", "latest_added": "2026-11-10"}]}, "plex": None})

    def down(body):
        raise RuntimeError("3D 서재가 받지 않음")
    monkeypatch.setattr(lib3d_link, "send_event", down)
    with pytest.raises(RuntimeError):
        lib3d_link.check_and_deliver()
    assert lib3d_link._load_state()["books"]["general"] == "2026-11-01"   # 다음에 다시 보낸다


def test_where_people_and_push(monkeypatch):
    roster = [{"name": "애순이", "company": "ZEEPS 편집국"}, {"name": "소라", "company": "ZEEPS 영상콘텐츠팀"}, {"name": "문하늘", "company": "ZEEPS 영상콘텐츠팀"},
              {"name": "새사람", "company": "오아시스 거리"}]
    states = {"애순이": {"decision": {"location": "ZEEPS 편집국", "activity": "원고 검토", "state": "일하는 중"}},
              "소라": {"decision": {"location": "카페 오후세시", "activity": "커피 내리기", "state": "일하는 중"}},
              "문하늘": {"decision": {"location": "영상팀", "activity": "홍보 글", "state": "일하는 중"}}}
    people = lib3d_link.where_people(roster, states, {"문하늘"})
    assert people == [{"name": "애순이", "location": "ZEEPS 편집국", "activity": "원고 검토", "state": "일하는 중", "company": "ZEEPS 편집국"},
                      {"name": "소라", "location": "카페 오후세시", "activity": "커피 내리기", "state": "일하는 중", "company": "ZEEPS 영상콘텐츠팀"},
                      {"name": "문하늘", "location": "집", "activity": "잠", "state": "자는 중", "company": "ZEEPS 영상콘텐츠팀"}]   # 아직 판단이 없는 사람은 보내지 않는다
    sent = []
    monkeypatch.setattr(lib3d_link, "send_event", lambda body: sent.append(body) or {"success": True})
    monkeypatch.setattr(lib3d_link, "LIB3D_URL", "")
    assert lib3d_link.push_where(roster, states, set()) == "" and not sent          # 3D 서재 주소가 없으면 보내지 않는다
    monkeypatch.setattr(lib3d_link, "LIB3D_URL", "http://lib3d.test")
    monkeypatch.setattr(lib3d_link, "EVENT_TOKEN", "tok")
    assert lib3d_link.push_where(roster, states, {"문하늘"}) == "위치 3명 전달"
    assert sent[0]["type"] == "where" and len(sent[0]["people"]) == 3
    monkeypatch.setattr(lib3d_link, "WHERE_ON", False)
    assert lib3d_link.push_where(roster, states, set()) == ""
