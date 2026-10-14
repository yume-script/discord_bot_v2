"""자리 인수인계: 교체로 사람이 바뀌어도 그 자리의 이야기(하던 일/받은 소식/진행 중인 일)가 후임에게 이어진다."""
from datetime import datetime

from poring_food import agents, chronicle, handover, memory, villains


def test_handover_moves_story_to_successor(fake_llm):
    def handler(system, user):
        if "인수인계를 정리" in system:
            assert "기억: 장대희 부장에게 보고서 재작성 지시를 받음" in user
            return {"items": ["장대희 부장 지시 보고서 재작성 - 금요일까지"]}
        return {}

    fake_llm(handler)
    memory.remember("김동식", "장대희 부장에게 보고서 재작성 지시를 받음", 7, ["장대희"], "업무")
    chronicle.open_thread("김동식 보고서 논란", "부장이 다시 쓰라고 함")
    data = {"김동식": {"inbox": [{"at": "x", "kind": "업무", "text": "부장: 보고서 오늘 안에"}]}}
    ags = agents.load_agents()
    now = datetime(2026, 10, 14, 5, 3)
    note = handover.run("김동식", "한석율", "포링푸드 (Poring Food)", data, ags, agents._push_inbox, now,
                        ["스페셜 라인 입고"])
    assert "보고서 재작성" in note
    texts = [m["text"] for m in data["한석율"]["inbox"]]
    assert any(t.startswith("인수인계:") and "보고서 재작성" in t for t in texts)
    assert any("전임 김동식 앞으로 와 있던 것" in t and "보고서 오늘 안에" in t for t in texts)
    assert data["김동식"]["inbox"] == []
    colleague = next(a["name"] for a in ags if a.get("company") == "포링푸드 (Poring Food)" and a["name"] not in ("김동식", "한석율"))
    assert any("한석율 담당" in m["text"] for m in data[colleague]["inbox"])
    assert any("이어받음" in (u.get("note") or "") for t in chronicle.open_threads() if t["title"] == "김동식 보고서 논란"
               for u in t.get("updates", []))
    assert any("이어받음" in m["text"] for m in memory.recall("한석율", k=10))
    block = handover.prompt_block("한석율", "포링푸드 (Poring Food)", now)
    assert "내 자리 인수인계" in block and "보고서 재작성" in block
    assert "김동식 → 한석율" in handover.prompt_block(colleague, "포링푸드 (Poring Food)", now)
    # 승계: 쉬러 간 사람 자리에 지금 있는 사람
    assert handover.successor("김동식", {"한석율"}) == "한석율"
    assert handover.successor("김동식", {"김동식", "한석율"}) == "김동식"
    assert handover.successor("김동식", set()) == ""


def test_villain_retargets_successor(fake_llm):
    seen = {}

    def handler(system, user):
        if "인수인계를 정리" in system:
            return {"items": ["x"]}
        if "다음 수를 꾸미는" in system and "'권민우'" in system:
            seen["targets"] = user.split("오늘 노릴 사람 (이 중 하나): ")[1].split("\n")[0]
        return {}

    fake_llm(handler)
    ags = [a for a in agents.load_agents() if a["name"] != "김동식"]
    handover.run("김동식", "한석율", "포링푸드 (Poring Food)", {}, ags, agents._push_inbox, datetime(2026, 10, 14, 5))
    ags.append({"name": "한석율", "company": "포링푸드 (Poring Food)"})
    villains.tick(ags, [], datetime(2026, 10, 14, 11, 3))
    assert "한석율" in seen["targets"] and "김동식" not in seen["targets"]
