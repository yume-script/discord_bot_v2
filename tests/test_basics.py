"""조사/제목 유사도/장소 묶기/배경 인물 일정 - LLM 없이 도는 규칙들."""
import random
from datetime import datetime, timedelta

from poring_food import agents, characters, chronicle
from poring_food.clock import KST
from poring_food.josa import j


def test_josa():
    assert j("성덕선", "가") == "성덕선이"
    assert j("소라", "가") == "소라가"
    assert j("이지안", "와") == "이지안과"
    assert j("애순이", "와") == "애순이와"
    assert j("동그라미", "랑") == "동그라미랑"


def test_similar_topics():
    assert chronicle.similar("마을 이름 짓기", "우리 동네 이름 짓기 최종 토론")
    assert not chronicle.similar("마을 이름 짓기", "골목 화단 만들기")
    assert not chronicle.similar("골목 화단 만들기", "골목 벤치 설치")


def test_place_label_groups():
    assert agents._place_label("회사 1층 카페 오후세시") == "☕ 카페 오후세시"
    assert agents._place_label("포장마차") == "🏮 포장마차 단밤"
    assert agents._place_label("자취방에서 쉬는 중") == "🏠 집"


def _at(weekday_offset, hour):
    base = datetime(2026, 10, 12, tzinfo=KST)  # 월요일
    return base + timedelta(days=weekday_offset, hours=hour)


def _schedule(monkeypatch, character, when):
    monkeypatch.setattr(characters, "now_kst", lambda: when)
    return characters.get_generic_schedule(character, random.Random(1), None)


def test_child_schedule(monkeypatch):
    kid = {"id": "t|아이", "name": "아이", "rank": "초등학생", "dept": "", "company": "광주 동네 상가", "key_behavior": ""}
    assert characters.life_type(kid)[0] == "child"
    assert _schedule(monkeypatch, kid, _at(0, 10))[0] == "초등학교"
    assert _schedule(monkeypatch, kid, _at(0, 22))[2] == "자는 중"
    assert "소개팅" not in _schedule(monkeypatch, kid, _at(5, 15))[1]


def test_company_people_are_office_even_with_tricky_words():
    c = {"id": "x", "name": "구동매", "rank": "부장", "dept": "품질관리부", "company": "포링푸드 (Poring Food)",
         "outer_persona": "", "inner_truth": "화가 나서가 아니라 손가락이 굵어서", "key_behavior": ""}
    assert characters.life_type(c)[0] == "office"


def test_shift_worker_has_night_and_off_days(monkeypatch):
    nurse = {"id": "n", "name": "간호사님", "rank": "간호사", "dept": "", "company": "광주 동네 상가", "key_behavior": "일"}
    states = {_schedule(monkeypatch, nurse, _at(d, 3))[2] for d in range(3)}
    assert "일하는 중" in states and "자는 중" in states


def test_facility_temp_shown_as_fahrenheit_converted(monkeypatch):
    from poring_food import mcp_signals
    status = {"cpu": {"host_percent": 30}, "temperature_c": {"max": 95.0}}
    monkeypatch.setattr(mcp_signals, "FACILITY_TEMP_AS_F", True)
    line = mcp_signals.describe_facility(status)
    assert "설비 온도 35°C" in line and "과열" not in line
    monkeypatch.setattr(mcp_signals, "FACILITY_TEMP_AS_F", False)
    assert "설비 과열 경보" in mcp_signals.describe_facility(status)


def test_facility_uses_whole_server_numbers():
    """설비 가동률/작업장 혼잡도/창고 적재율은 서버 전체 값 (CPU=머신 전체, 디스크=로컬 디스크 전체 합)."""
    from poring_food import mcp_signals
    status = {"cpu": {"host_percent": 42.0, "container_percent_of_machine": 3.0},
              "memory": {"used_percent": 55.0},
              "disks": [{"used_percent": 93.0}, {"used_percent": 20.0}],
              "disk_total": {"used_percent": 31.5}}
    nums = mcp_signals.facility_numbers(status)
    assert (nums["cpu"], nums["mem"], nums["disk"]) == (42.0, 55.0, 31.5)
    line = mcp_signals.describe_facility(status)
    assert "창고 적재율 31.5%" in line and "포화" not in line
    old = {"cpu": {"container_percent_of_machine": 3.0}, "disks": [{"used_percent": 93.0}]}  # 예전 MCP 응답
    assert mcp_signals.facility_numbers(old)["disk"] == 93.0 and mcp_signals.facility_numbers(old)["cpu"] == 3.0
