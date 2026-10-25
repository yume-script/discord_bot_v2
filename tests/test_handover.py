"""자리 인수인계: 교체로 사람이 바뀌어도 그 자리의 이야기(하던 일/받은 소식/진행 중인 일)가 후임에게 이어진다."""
from datetime import datetime

from poring_food import agents, chronicle, handover, memory, villains


def test_handover_moves_story_to_successor(fake_llm):
    def handler(system, user):
        if "인수인계를 정리" in system:
            assert "기억: 바닐 부장에게 보고서 재작성 지시를 받음" in user
            return {"items": ["바닐 부장 지시 보고서 재작성 - 금요일까지"]}
        return {}

    fake_llm(handler)
    memory.remember("위벨", "바닐 부장에게 보고서 재작성 지시를 받음", 7, ["바닐"], "업무")
    chronicle.open_thread("위벨 보고서 논란", "부장이 다시 쓰라고 함")
    data = {"위벨": {"inbox": [{"at": "x", "kind": "업무", "text": "부장: 보고서 오늘 안에"}]}}
    ags = agents.load_agents()
    now = datetime(2026, 10, 14, 5, 3)
    note = handover.run("위벨", "라오프", "오아시스 마법도서관", data, ags, agents._push_inbox, now,
                        ["스페셜 라인 입고"])
    assert "보고서 재작성" in note
    texts = [m["text"] for m in data["라오프"]["inbox"]]
    assert any(t.startswith("인수인계:") and "보고서 재작성" in t for t in texts)
    assert any("전임 위벨 앞으로 와 있던 것" in t and "보고서 오늘 안에" in t for t in texts)
    assert data["위벨"]["inbox"] == []
    colleague = next(a["name"] for a in ags if a.get("company") == "오아시스 마법도서관" and a["name"] not in ("위벨", "라오프"))
    assert any("라오프 담당" in m["text"] for m in data[colleague]["inbox"])
    assert any("이어받음" in (u.get("note") or "") for t in chronicle.open_threads() if t["title"] == "위벨 보고서 논란"
               for u in t.get("updates", []))
    assert any("이어받음" in m["text"] for m in memory.recall("라오프", k=10))
    block = handover.prompt_block("라오프", "오아시스 마법도서관", now)
    assert "내 자리 인수인계" in block and "보고서 재작성" in block
    assert "위벨 → 라오프" in handover.prompt_block(colleague, "오아시스 마법도서관", now)
    # 승계: 쉬러 간 사람 자리에 지금 있는 사람
    assert handover.successor("위벨", {"라오프"}) == "라오프"
    assert handover.successor("위벨", {"위벨", "라오프"}) == "위벨"
    assert handover.successor("위벨", set()) == ""


def test_villain_retargets_successor(fake_llm):
    seen = {}

    def handler(system, user):
        if "인수인계를 정리" in system:
            return {"items": ["x"]}
        if "다음 수를 꾸미는" in system and "'덴켄'" in system:
            seen["targets"] = user.split("오늘 노릴 사람 (이 중 하나): ")[1].split("\n")[0]
        return {}

    fake_llm(handler)
    ags = [a for a in agents.load_agents() if a["name"] != "위벨"]
    handover.run("위벨", "라오프", "오아시스 마법도서관", {}, ags, agents._push_inbox, datetime(2026, 10, 14, 5))
    ags.append({"name": "라오프", "company": "오아시스 마법도서관"})
    villains.tick(ags, [], datetime(2026, 10, 14, 11, 3))
    assert "라오프" in seen["targets"] and "위벨" not in seen["targets"]


def test_staff_overview_lists_seats_jobs_and_flags():
    from poring_food import characters, kpi
    kpi.assign(agents.load_agents(), characters.load_roster())
    pages = agents.staff_overview()
    text = "\n".join(pages)
    assert "오아시스 마을 명단" in text and "📚 오아시스 마법도서관" in text and "🏰 오아시스 마을" in text and "🔮 별빛 환영극장" in text
    assert "**바닐**" in text and "😈빌런" in text                 # 빌런 표시
    assert "**애순이**" in text and "🔒계정" in text                 # 디스코드 계정 = 고정
    assert "맡은 일: 마법서 입고" in text                          # 실적 자리
    assert all(len(p) <= 2000 for p in pages)
    only = "\n".join(agents.staff_overview("극장"))
    assert "🔮 별빛 환영극장" in only and "📚 오아시스 마법도서관" not in only
    assert "🏰 오아시스 마을" in "\n".join(agents.staff_overview("마을"))
    assert agents.staff_overview("없는소속") == ["해당하는 소속에 지금 스스로 사는 사람이 없어요."]
