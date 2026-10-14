"""
[신규] 심시티/심즈처럼 오아시스 거리 인물들이 각자 자기 상태와 기억을 갖고, 마주치면 실제로
상호작용하는 구조를 만든다. 애순이는 기존처럼 processor.py/generator.py가 상세하게
다루고(주인공이라 손으로 짠 디테일이 있음), 나머지 인물들은 이 파일이 데이터 기반(직무/
성격 텍스트를 그대로 활용)으로 가볍게(LLM 호출 없이) 스케줄을 굴린다.

로스터는 ORGANIZATION_GLOB("*_organization.json")에 걸리는 파일(+ 주 1회 이사 온 새 이웃, newcomers.py)을 전부 자동으로 읽어서
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

from . import life, worldcfg
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
    # 주 1회 이사 온 드라마 인물들 (newcomers.py) - 동네 배경 인물로
    from . import newcomers
    have = {c["name"] for c in roster}
    for m in newcomers.residents():
        if m["name"] in have:
            continue
        roster.append({
            "id": _make_id(newcomers.COMPANY, m["name"]), "name": m["name"], "rank": m.get("rank", ""), "prefix": "",
            "dept": f"{newcomers.DEPT} ({m.get('drama', '')})", "company": newcomers.COMPANY,
            "outer_persona": m.get("outer_persona", ""), "inner_truth": m.get("inner_truth", ""),
            "key_behavior": m.get("key_behavior", ""),
        })
    return roster


def roster_by_id(roster: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in roster}


# ============================================================= 개별 스케줄 (LLM 없이, 가벼움)
# 배경 인물마다 나이대/직업을 조직도(직책/부서/겉모습/속마음/회사)에서 짐작해서 그에 맞는 하루를 산다.
# 초등학생은 학교-학원-놀이터, 할머니는 새벽 산책-경로당-시장, 포장마차 사장은 저녁부터 새벽까지 장사,
# 간호사/순경은 교대 근무... (예전엔 모두가 회사원처럼 7~18시 사무실, 저녁엔 소개팅/술자리였다)
KID_ACTIVITIES = [("놀이터", "친구들과 놀기", 3), ("집", "숙제", 2), ("집", "만화 보기", 2), ("골목", "자전거 타기", 1),
                  ("동네 편의점", "군것질", 1), ("학원", "피아노 학원", 1)]
TEEN_ACTIVITIES = [("동네 편의점", "친구들과 컵라면", 2), ("집", "게임", 2), ("PC방", "게임", 1), ("집", "숙제", 2),
                   ("골목", "친구들과 수다", 1), ("노래방", "친구들과 노래방", 1)]
YOUNG_ACTIVITIES = [("동네 카페", "과제/공부", 2), ("집", "유튜브 보기", 2), ("동네 편의점", "야식 사기", 1),
                    ("포장마차", "친구들과 한잔", 1), ("헬스장/공원", "운동", 1), ("영화관", "영화 감상", 1), ("집", "휴식", 2)]
ELDER_ACTIVITIES = [("경로당", "화투 치기", 3), ("공원", "산책", 2), ("시장", "장 구경", 2), ("집", "TV 드라마 보기", 3),
                    ("골목 평상", "이웃과 수다", 2), ("텃밭", "텃밭 가꾸기", 1), ("동네 병원", "물리치료", 1)]
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
PERSONAL_BY_TYPE = {"child": KID_ACTIVITIES, "teen": TEEN_ACTIVITIES, "student": YOUNG_ACTIVITIES,
                    "elder": ELDER_ACTIVITIES}

# (유형, 찾을 말들) - 앞에 있는 게 먼저
_TYPE_RULES = [
    ("child", ("초등학생", "꼬마", "어린이")),
    ("teen", ("고등학생", "중학생")),
    ("student", ("대학생", "재수생", "수험생", "장수생", "대학원생", "취준생")),
    ("elder", ("할머니", "할아버지", "은퇴", "노인", "경로당", "70대", "80대")),
    ("nurse", ("간호사",)),
    ("medic", ("의사", "전공의", "원장", "치위생사", "약사")),
    ("police", ("순경", "경찰")),
    ("public", ("동장", "통장", "공무원", "주민센터")),
    ("shop", ("사장", "주방장", "미용실", "반점", "포장마차", "생선가게", "반찬가게", "횟집", "가게")),
    ("freelance", ("작가", "PD", "화가", "강사", "변호사", "정비공", "택배", "기사", "가수")),
]
_WORKPLACES = [  # (찾을 말, 일하는 곳)
    ("미용실", "골목 미용실"), ("반점", "남숙반점"), ("중국집", "남숙반점"), ("포장마차", "포장마차 단밤"),
    ("생선", "시장 생선가게"), ("반찬", "시장 반찬가게"), ("횟집", "화정 횟집"), ("LP", "골목 LP바"),
    ("배달", "배달대행 사무실"), ("치위생사", "동네 치과"), ("치과", "동네 치과"), ("약사", "동네 약국"),
    ("간호사", "대학병원"), ("전공의", "대학병원"), ("의사", "대학병원"), ("순경", "지구대"), ("동장", "주민센터"),
    ("통장", "주민센터"), ("방송작가", "방송국 작업실"), ("작가", "작업실"), ("PD", "촬영 현장"), ("화가", "미술학원"),
    ("강사", "학원"), ("변호사", "법률사무소"), ("정비공", "카센터"), ("택배", "골목 배송 중"), ("기사", "골목 배송 중"),
    ("가수", "골목 LP바"), ("대학원생", "대학 연구실"), ("대학생", "대학교"), ("고등학생", "고등학교"),
    ("초등학생", "초등학교"), ("재수생", "독서실"), ("수험생", "독서실"), ("장수생", "독서실"),
]
# 유형별 하루: sleep=(잠드는 시각, 일어나는 시각), work=[(시작, 끝)...] (끝 미포함), weekend=주말에도 일하나
_DAY = {
    "child":     {"sleep": (21, 7), "work": [(8, 14)], "weekend": False, "after": [(14, 16, "학원", "영어 학원")]},
    "teen":      {"sleep": (0, 7), "work": [(8, 16)], "weekend": False, "after": [(17, 22, "학원", "학원 수업")]},
    "student":   {"sleep": (2, 9), "work": [(10, 18)], "weekend": False},
    "elder":     {"sleep": (21, 5), "work": [], "weekend": False, "after": [(5, 7, "공원", "새벽 산책")]},
    "nurse":     {"shift": True},
    "medic":     {"sleep": (0, 7), "work": [(8, 19)], "weekend": False},
    "police":    {"shift": True},
    "public":    {"sleep": (0, 7), "work": [(9, 18)], "weekend": False},
    "freelance": {"sleep": (2, 9), "work": [(11, 20)], "weekend": False},
    "office":    {"sleep": (0, 7), "work": [(7, 18)], "weekend": False},
}
_SHOP_HOURS = {"포장마차 단밤": ([(17, 24), (0, 2)], (3, 11)), "골목 LP바": ([(18, 24), (0, 2)], (3, 11)),
               "시장 생선가게": ([(5, 18)], (21, 4)), "시장 반찬가게": ([(6, 19)], (21, 5)),
               "골목 미용실": ([(10, 20)], (0, 8)), "배달대행 사무실": ([(11, 23)], (2, 9)),
               "남숙반점": ([(10, 22)], (1, 8))}
_TYPE_CACHE: dict[str, tuple[str, str]] = {}


def life_type(character: dict) -> tuple[str, str]:
    """(유형, 일하는 곳) - 조직도 직책/부서/설정/회사에서 짐작한다."""
    key = character.get("id") or character.get("name", "")
    if key in _TYPE_CACHE:
        return _TYPE_CACHE[key]
    head = f"{character.get('rank', '')} {character.get('dept', '')}"
    text = f"{head} {character.get('outer_persona', '')} {character.get('inner_truth', '')}"
    company = character.get("company", "")
    kind = next((t for t, words in _TYPE_RULES if any(w in head for w in words)), None)
    if not kind and company in worldcfg.OFFICES:
        kind = "office"  # 가게(책방/극장) 사람은 설정 문장보다 소속이 먼저 ("화가 나서" 같은 말에 걸리지 않게)
    kind = kind or next((t for t, words in _TYPE_RULES if any(w in text for w in words)), None) or "freelance"
    place = next((p for w, p in _WORKPLACES if w in head), None) or next((p for w, p in _WORKPLACES if w in text), None)
    if kind == "office" or not place:
        place = {"office": company if company in worldcfg.OFFICES else f"{company or '회사'} 사무실", "shop": "골목 가게", "elder": "경로당", "public": "주민센터",
                 "medic": "동네 병원", "child": "초등학교", "teen": "고등학교", "student": "대학교"}.get(kind, "동네 작업실")
    _TYPE_CACHE[key] = (kind, place)
    return kind, place


def _in(hour: int, ranges) -> bool:
    return any(a <= hour < b for a, b in ranges)


def _asleep(hour: int, sleep: tuple[int, int]) -> bool:
    s, e = sleep
    return s <= hour < e if s <= e else (hour >= s or hour < e)


def get_generic_schedule(character: dict, rnd: random.Random, life_state: dict | None = None) -> tuple[str, str, str]:
    """
    배경 인물의 (location, activity, state). 나이대/직업 유형(life_type)에 맞는 하루를 산다 -
    잠, 일(학교/가게/병원/사무실...), 그 밖의 시간은 유형에 맞는 개인 활동(life.py 상태로 가중치 조정).
    """
    now = now_kst()
    hour = now.hour
    weekend = now.weekday() >= 5
    kind, place = life_type(character)
    day = _DAY.get(kind, _DAY["office"])
    work, sleep, weekend_work = day.get("work", []), day.get("sleep", (0, 7)), day.get("weekend", False)
    if kind == "shop":
        work, sleep = _SHOP_HOURS.get(place, ([(10, 21)], (1, 8)))
        weekend_work = True
    if day.get("shift"):  # 교대 근무: 주간 / 야간 / 비번이 날마다 돈다 (사람마다 어긋나게)
        turn = (now.toordinal() + sum(map(ord, character.get("name", "")))) % 3
        work, sleep = ([(7, 19)], (23, 6)) if turn == 0 else ([(19, 24), (0, 7)], (8, 15)) if turn == 1 else ([], (0, 8))
        weekend_work = True
    if _asleep(hour, sleep):
        return "집", "잠자는 중", "자는 중"
    if _in(hour, work) and (weekend_work or not weekend):
        behaviors = [b.strip() for b in character.get("key_behavior", "").split(",") if b.strip()]
        default = {"child": "수업 듣기", "teen": "수업 듣기", "student": "공부"}.get(kind, "일하는 중")
        act = rnd.choice(behaviors) if behaviors and kind not in ("child", "teen", "student") else default
        return place, act, "일하는 중"
    if hour < 10 and kind != "elder" and not day.get("shift"):  # 일 나가기 전 아침 (주말엔 늦잠/느긋한 아침)
        if weekend:
            return "집", "주말 늦잠 뒤 느긋한 아침", "개인시간"
        return "집", ("등교 준비" if kind in ("child", "teen") else "아침 먹고 나갈 준비"), "개인시간"
    if hour >= 22 and rnd.random() < 0.7:  # 밤늦게는 대부분 집에서 하루 마무리
        return "집", "하루 마무리하며 쉬기", "개인시간"
    for a, b, loc, act in day.get("after", []):
        if a <= hour < b and not (weekend and kind in ("child", "teen")):
            return loc, act, "개인시간"
    candidates = PERSONAL_BY_TYPE.get(kind, GENERIC_PERSONAL_ACTIVITIES)
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
        "책방 사람과 극장 사람이면(손님을 두고 겨루는 이웃 가게) 은근한 신경전이나 묘한 호기심을 곁들여도 좋다.\n"
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
                {"role": "system", "content": "너는 여러 캐릭터가 사는 골목 '오아시스 거리'(책방/극장/동네 가게)를 기록하는 작가다."},
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
