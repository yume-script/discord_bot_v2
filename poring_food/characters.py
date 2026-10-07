"""
[신규] 심시티/심즈처럼 포링푸드 인물들이 각자 자기 상태와 기억을 갖고, 마주치면 실제로
상호작용하는 구조를 만든다. 애순이는 기존처럼 processor.py/generator.py가 상세하게
다루고(주인공이라 손으로 짠 디테일이 있음), 나머지 인물들은 이 파일이 데이터 기반(직무/
성격 텍스트를 그대로 활용)으로 가볍게(LLM 호출 없이) 스케줄을 굴린다.

로스터는 ORGANIZATION_GLOB("*_organization.json")에 걸리는 파일을 전부 자동으로 읽어서
만든다 - poring_food_organization.json, erinn_logistics_organization.json은 물론,
나중에 라이벌 회사가 늘어나도 같은 패턴의 파일만 이 폴더에 추가하면 코드 수정 없이
자동으로 인식된다.

상호작용(같은 장소에 있는 두 인물이 실제로 마주치는 이벤트)은 하루 세 번
(config.INTERACTION_HOURS)만 LLM을 호출해서 만든다 - 매시간 만들면 인물 수만큼 LLM 호출이
늘어나니, 빈도를 제한해서 비용을 관리한다.
"""
from __future__ import annotations

import glob
import json
import os
import random
import requests

from . import life
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import (
    API_URL,
    LITELLM_MASTER_KEY,
    LLM_MODEL,
    ORGANIZATION_GLOB,
    CHARACTERS_STATE_PATH,
    RELATIONSHIPS_PATH,
    HISTORY_LOG_PATH,
)

# 회사가 다른 인물끼리 같은 장소에 있을 확률은 낮게, 보통은 자기 회사 사람들끼리 마주치게
# 하고 싶어서 "회사 사무실"은 회사명을 붙여 서로 다른 장소로 취급한다. 아래 개인 활동
# 장소들은 회사 구분 없이 공용 - 라이벌 회사 인물이라도 카페/영화관 같은 데서는 마주칠 수 있다.
GENERIC_PERSONAL_ACTIVITIES = [
    ("영화관", "영화 감상", 2),
    ("동네 카페", "지인과 커피 타임", 2),
    ("서점", "책 구경", 1),
    ("헬스장/공원", "운동", 1),
    ("맛집", "맛집 탐방", 2),
    ("집", "휴식", 3),
    ("소개팅 장소", "소개팅/데이트", 1),
    ("쇼핑몰", "쇼핑", 1),
    ("친구 집", "친구 모임", 1),
    ("포장마차", "퇴근 후 술 한잔", 1),
]


def _weighted_choice(rnd: random.Random, candidates):
    """candidates: [(a, b, weight), ...] 중 하나를 가중치 랜덤으로 고른다."""
    total = sum(c[-1] for c in candidates)
    r = rnd.uniform(0, total)
    upto = 0
    for c in candidates:
        upto += c[-1]
        if upto >= r:
            return c[:-1]
    return candidates[-1][:-1]


# ============================================================= 로스터 로딩
def _make_id(company: str, name: str) -> str:
    """
    조직도에 이름이 겹치는 인물이 있을 수 있어서(예전엔 "미믹"이 포링푸드/에린 로지스틱스
    양쪽에 다 있었음) 내부 키는 회사+이름 조합으로 만든다 - 이름만 쓰면 한쪽이 조용히
    덮어써져서 사라지는 버그가 생긴다. 사람이 보는 텍스트(이야기/알림)에는 그대로 이름만 쓴다.
    """
    return f"{company}|{name}"


def load_roster() -> list[dict]:
    """*_organization.json 전부를 스캔해서 인물 목록을 만든다. 애순이도 포함된다."""
    roster = []
    for path in sorted(glob.glob(ORGANIZATION_GLOB)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[경고] 조직도 로드 실패 ({path}): {e}")
            continue

        company_name = data.get("company_name", os.path.basename(path))
        for dept in data.get("departments", []):
            for m in dept.get("members", []):
                roster.append({
                    "id": _make_id(company_name, m["name"]),
                    "name": m["name"],
                    "rank": m.get("rank", ""),
                    "prefix": m.get("prefix", ""),
                    "dept": dept.get("dept_name", ""),
                    "company": company_name,
                    "outer_persona": m.get("outer_persona", ""),
                    "inner_truth": m.get("inner_truth", ""),
                    "key_behavior": m.get("key_behavior", ""),
                })
    return roster


def roster_by_id(roster: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in roster}


# ============================================================= 개별 스케줄 (LLM 없이, 가벼움)
def get_generic_schedule(character: dict, rnd: random.Random, life_state: dict | None = None) -> tuple[str, str, str]:
    """
    애순이를 제외한 인물들의 스케줄. (location, activity, state)를 반환.
    업무 시간엔 그 인물의 key_behavior(조직도에 있는, 콤마로 구분된 2~3개 행동) 중
    하나를 그대로 활동으로 쓰고, 그 외 시간엔 공용 개인활동 풀에서 가중치 랜덤으로 고른다.
    """
    now = now_kst()
    hour = now.hour
    is_weekend = now.weekday() >= 5  # 토/일 - 애순이와 달리 다른 인물은 토요일 특근 디테일 없음

    if 0 <= hour < 7:
        return "집", "잠자는 중", "자는 중"

    # [수정] 원래 업무 시간이 9~18시였는데, 취침(0~7시)과의 사이에 7~8시가 아무 조건에도
    # 안 걸려서 "출근 시간대인데 개인활동(소개팅 등) 중"으로 나오는 오류가 있었다.
    # 취침 끝나는 시각과 바로 이어지게 7시부터 업무로 잡아서 그 공백을 없앴다.
    if not is_weekend and 7 <= hour < 18:
        behaviors = [b.strip() for b in character.get("key_behavior", "").split(",") if b.strip()]
        act = rnd.choice(behaviors) if behaviors else "업무 처리 중"
        workplace = f"{character.get('company', '회사')} 사무실"
        return workplace, act, "일하는 중"

    # [변경] 개인 시간 활동은 그 사람의 지금 상태(life.py)로 가중치를 조정한다 (스트레스↑ → 술자리 등)
    candidates = GENERIC_PERSONAL_ACTIVITIES
    if life_state:
        candidates = [(loc, act, life.bias_weight(life_state, f"{loc} {act}", w)) for loc, act, w in candidates]
    loc, act = _weighted_choice(rnd, candidates)
    return loc, act, "개인시간"


# ============================================================= 공유 상태 파일
def load_states() -> dict:
    if not os.path.exists(CHARACTERS_STATE_PATH):
        return {}
    try:
        with open(CHARACTERS_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_states(states: dict) -> None:
    os.makedirs(os.path.dirname(CHARACTERS_STATE_PATH), exist_ok=True)
    with open(CHARACTERS_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(states, f, ensure_ascii=False, indent=2)


def update_all_states(roster: list[dict], rnd: random.Random, aesun_state: dict | None = None,
                      overrides: dict[str, dict] | None = None) -> dict:
    """
    애순이를 제외한 전원의 상태를 갱신하고, 애순이 상태(main.py가 이미 계산해둔 것)도
    같이 합쳐서 하나의 공유 상태 파일로 저장한다. 이게 있어야 "같은 장소에 있는 사람끼리
    마주치기" 판단(find_colocated_pair)이 가능하다.
    키는 인물 id(회사+이름)로 저장하지만, 표시용 "name"/"company" 필드도 같이 넣어둔다.
    """
    states = {}
    now_iso = now_kst().isoformat()
    life_states = life.load_all()

    for character in roster:
        cid = character["id"]
        if character["name"] == "애순이" and aesun_state:
            states[cid] = {
                **aesun_state, "name": character["name"],
                "company": character["company"], "dept": character["dept"],
            }
            continue
        ov = (overrides or {}).get(character["name"])
        if ov:  # [신규] LLM 에이전트(소라 등)는 스스로 정한 행동을 쓴다
            loc, act, state = ov.get("location", ""), ov.get("activity", ""), ov.get("state", "개인시간")
        else:
            loc, act, state = get_generic_schedule(character, rnd, life_states.get(character["name"]))
        states[cid] = {
            "name": character["name"],
            "location": loc,
            "activity": act,
            "state": state,
            "company": character["company"],
            "dept": character["dept"],
            "updated_at": now_iso,
        }

    save_states(states)
    return states


# ============================================================= 관계
def load_relationships() -> dict:
    if not os.path.exists(RELATIONSHIPS_PATH):
        return {}
    try:
        with open(RELATIONSHIPS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_relationships(rels: dict) -> None:
    os.makedirs(os.path.dirname(RELATIONSHIPS_PATH), exist_ok=True)
    with open(RELATIONSHIPS_PATH, "w", encoding="utf-8") as f:
        json.dump(rels, f, ensure_ascii=False, indent=2)


def _rel_key(name_a: str, name_b: str) -> str:
    return "|".join(sorted([name_a, name_b]))


# ============================================================= 상호작용
# 이런 장소는 여러 명이 같은 문자열을 골라도 실제로 "마주친" 게 아니다(각자 자기 집일 뿐) -
# 상호작용 후보에서 제외한다.
_AMBIGUOUS_LOCATIONS = {"집"}


def find_colocated_pair(states: dict, rnd: random.Random) -> tuple[str, str] | None:
    """같은 장소에 2명 이상 있는 그룹을 찾아 그중 한 쌍(id, id)을 무작위로 고른다."""
    by_location: dict[str, list[str]] = {}
    for cid, info in states.items():
        loc = info.get("location")
        if not loc or loc in _AMBIGUOUS_LOCATIONS:
            continue
        by_location.setdefault(loc, []).append(cid)

    candidates = [ids for ids in by_location.values() if len(ids) >= 2]
    if not candidates:
        return None
    group = rnd.choice(candidates)
    a, b = rnd.sample(group, 2)
    return a, b


def generate_interaction(pair: tuple[str, str], states: dict, roster_map: dict[str, dict]) -> dict | None:
    """
    두 인물의 우연한 마주침 에피소드를 LLM으로 만든다. 실패하면 None.
    pair는 (id, id) - roster_map/states 조회는 id로, 텍스트 표시는 각자의 "name"으로 한다.
    반환: {"episode": str, "affinity_delta": int, "location": str, "participants": [name, name]}
    """
    id_a, id_b = pair
    char_a, char_b = roster_map.get(id_a), roster_map.get(id_b)
    if not char_a or not char_b:
        return None
    name_a, name_b = char_a["name"], char_b["name"]

    loc = states.get(id_a, {}).get("location", "어딘가")
    rels = load_relationships()
    rel_key = _rel_key(id_a, id_b)
    rel = rels.get(rel_key, {"affinity": 0, "summary": "", "count": 0})

    prompt = (
        f"{name_a}({char_a['company']} {char_a['dept']} {char_a['rank']} - 겉모습: {char_a['outer_persona']} "
        f"/ 속마음: {char_a['inner_truth']})와 {name_b}({char_b['company']} {char_b['dept']} {char_b['rank']} - "
        f"겉모습: {char_b['outer_persona']} / 속마음: {char_b['inner_truth']})가 {loc}에서 우연히 마주쳤다.\n"
        f"둘의 이전 관계 요약: {rel['summary'] or '특별한 인연 없음'} (현재 친밀도: {rel['affinity']})\n\n"
        "짧은 상호작용 에피소드를 2~3문장으로 만들어라. 겉모습과 속마음의 괴리를 살려 재미있게 쓰고, "
        "회사가 다르면(라이벌 관계면) 은근한 신경전이나 묘한 호기심을 곁들여도 좋다.\n"
        "반드시 JSON으로만 응답: {\"episode\": \"...\", \"affinity_delta\": 정수(-10~10),"
        " \"relationship_summary\": \"이번 만남을 한 줄로 요약\"}"
    )

    if not (API_URL and LITELLM_MASTER_KEY):
        return None

    try:
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": "너는 여러 캐릭터가 사는 가상의 회사 세계를 기록하는 작가다."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
            "response_format": {"type": "json_object"},
        }
        res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        if res.status_code != 200:
            return None
        data = json.loads(res.json()["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"[경고] 상호작용 생성 실패: {e}")
        return None

    delta = int(data.get("affinity_delta", 0))
    rel["affinity"] = max(-100, min(100, rel.get("affinity", 0) + delta))
    rel["summary"] = data.get("relationship_summary", rel.get("summary", ""))
    rel["count"] = rel.get("count", 0) + 1
    rel["last_interaction"] = now_kst().isoformat()
    rels[rel_key] = rel
    save_relationships(rels)

    return {"episode": data.get("episode", ""), "affinity_delta": delta, "location": loc,
            "participants": [name_a, name_b]}


def append_character_history(character: str, entry: dict) -> None:
    """다른 인물(애순이 포함)의 한 줄 기록을 공용 히스토리 로그에 추가한다."""
    record = {"character": character, **entry}
    try:
        os.makedirs(os.path.dirname(HISTORY_LOG_PATH), exist_ok=True)
        with open(HISTORY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[에러] {character} 히스토리 기록 실패: {e}")
