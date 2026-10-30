"""시설 담당: 담당자 정하기(교대 중이면 같은 소속), 프롬프트, 조율 회의(담당자 발언 -> 조정자 결론)."""
from poring_food import loadshed, stewards


def _ag(name, company="오아시스 마법도서관"):
    return {"name": name, "company": company, "sleep": [2, 7], "profile": "", "speech": ""}


def test_steward_fallback(monkeypatch):
    agents = [_ag("애순이"), _ag("페른"), _ag("라오프"), _ag("소라", "별빛 환영극장")]
    monkeypatch.setattr("poring_food.agents._base_agents", lambda: agents + [_ag("슈타르크")])
    assert stewards.steward("bookoasis", agents) == "페른"
    assert stewards.steward("assistant", agents) == "라오프"      # 슈타르크가 쉬는 중 -> 같은 도서관 사람
    assert stewards.steward("plex", agents) == "소라"
    monkeypatch.setenv("PORING_STEWARDS", "bookoasis=라오프, nope=x")
    assert stewards.steward("bookoasis", agents) == "라오프"
    assert stewards.for_name("bookoasis_assistant.service") == "assistant"


def test_prompt_block(monkeypatch):
    agents = [_ag("애순이"), _ag("슈타르크"), _ag("소라", "별빛 환영극장")]
    monkeypatch.setattr(loadshed, "_load", lambda: {"hosts": {"plex01": {"stage": 4, "playing": 2, "struggling": True,
        "throttled": {"unit:bookoasis_assistant.service": {"name": "bookoasis_assistant.service"}}}}})
    b = stewards.prompt_block("슈타르크", agents)
    assert "두루마리 운반 정령" in b and "늦춰 둠" in b and "시청·독서" in b
    assert "재생 2개" in stewards.prompt_block("소라", agents) and "버벅임" in stewards.prompt_block("소라", agents)
    assert "전체 조정" in stewards.prompt_block("애순이", agents)
    assert stewards.prompt_block("없는사람", agents) == ""


def test_council(monkeypatch):
    agents = [_ag("애순이"), _ag("슈타르크"), _ag("페른"), _ag("소라", "별빛 환영극장"), _ag("일레이나", "별빛 환영극장")]
    monkeypatch.setattr("poring_food.agents.load_agents", lambda: agents)
    monkeypatch.setattr("poring_food.chronicle.record_event", lambda *a, **k: None)
    monkeypatch.setattr("poring_food.memory.remember", lambda *a, **k: None)
    posts, heard = [], []

    def speak(key, name, user):
        heard.append((key, name, user))
        return {"assistant": "대여권 놓치면 손해예요", "plex": "저녁엔 손님이 많아요", "ff": "새벽엔 새로 고침이 돌아요"}.get(key, "")

    res = stewards.council("기다무 시간대", "추천 08~18시", ["assistant", "plex", "ff", "bookoasis"], '{"apply": true}',
                           speak=speak, conclude=lambda u: {"apply": True, "start": 8, "end": 18, "say": "낮으로 하자"},
                           post=lambda n, t: posts.append((n, t)))
    assert [k for k, _, _ in heard] == ["assistant", "plex", "ff", "bookoasis"]
    assert "대여권 놓치면" in heard[1][2]                         # 앞사람 말을 듣고 말한다
    assert [line["speaker"] for line in res["lines"]] == ["슈타르크", "소라", "일레이나"]   # 빈 말은 안 올림
    assert res["decision"]["start"] == 8 and res["chair"] == "애순이"
    assert posts[-1][0] == "애순이" and "낮으로 하자" in posts[-1][1] and len(posts) == 4


def test_key_people_uses_stewards():
    agents = [_ag("슈나", "오아시스 마을"), _ag("베니마루", "오아시스 마을"), _ag("슈타르크"), _ag("소라", "별빛 환영극장"),
              _ag("애순이")]
    got = loadshed.key_people(["bookoasis_assistant.service"], agents)
    assert got[:2] == ["슈나", "베니마루"] and "슈타르크" in got and "소라" in got
    assert "애순이" in loadshed.key_people(["결계 경보"], agents)


def test_tool():
    from poring_food import tools
    assert "get_town_stewards" in {t.name for t in tools.PORING_FOOD_TOOLS}
