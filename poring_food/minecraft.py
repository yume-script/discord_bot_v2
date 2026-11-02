"""숲 너머 개척지 (마인크래프트 마을) - 인물(LLM 에이전트)들이 실제 마인크래프트(Java) 서버에 봇으로 들어가 나무를 베고
돌을 캐고, 건물을 지어 개척지의 문명을 한 시대씩 발전시킨다. 서버와 봇은 scripts/minecraft/docker-compose.yml.

세계 모드 (PORING_WORLD_MODE, 기본 minecraft):
- minecraft: 오아시스 마을 매시 회차·실시간 대화·상영/도서관/거리 이야기를 멈추고, 이야기는 이 개척지 중심으로 돈다.
  슈나의 💠 결계 조절 알림(서버 부하 단계)은 그대로 - 결계 사정이 개척지 일에 영향을 준다(높으면 모두 철수).
- oasis: 예전처럼 오아시스 마을 (개척지는 그 옆 이야기).

문명 (ERAS): 개척 캠프 → 통나무 마을 → 돌담 마을 → 성채 도시 → 왕도. 시간이 지나서가 아니라 '문제를 풀어야' 오른다:
시대마다 지어야 할 건물 + 풀어야 할 조건(식량 자급 = 창고 식량, 석탄·철 생산 시작 = 지금까지 캔 양)이 있다.
자원: 통나무(gather), 돌(mine), 식량(farm - 지은 밭의 다 익은 밀 수확), 석탄·철(ore - 광석 캐기). 건물값은 창고에서 뺀다.
식량은 일하는 사람이 바퀴마다 먹는다(EAT) - 바닥나면 '식량 부족' 문제가 생기고 누군가 밭 일을 맡는다.

자율 운영 고리 (문제 발견 → 목표 → 작업 배정 → 결과 평가 → 교훈):
- problems(): 장부에서 지금 문제(식량 부족, 다음 시대에 모자란 자원/조건, 되풀이되는 실패)를 규칙으로 찾는다
- 매시 회의 뒤 의장(stewards chair)이 다음 한 시간 목표(focus + 목표치)를 정하고, 다음 회의 때 실제 장부 변화로 평가한다
  (달성/미달을 채널에 올리고, 미달이면 교훈으로 남긴다)
- 교훈(lessons): 실패한 일과 원인(봇이 보낸 오류, 빈손으로 돌아옴)을 세어 두고 일 정하기 프롬프트에 넣는다
- 직업 분화(roles): 사람마다 성공한 일 수를 세어 3번 이상 한 일이 그 사람 직업(나무꾼/광부/농부/광산꾼/건축가/경비)이 된다.
  직업에 맞는 일을 맡으면 더 많이 해 온다(SPECIALIST_COUNT)
- 연대기(history): 개척 N일째에 실제로 확인된 사건만 남긴다 (처음 지은 건물 종류, 새 시대, 식량 부족/해결, 첫 석탄·철, 목표 평가).
  짓기는 봇 쪽에서 세운 뒤 블록을 다시 확인해서 실제로 서 있을 때만 'done'이 온다

- 봇(cogs/poring_food.py minecraft_tick)이 TICK_MIN분마다 tick():
  1) 지난 일 결과(이벤트) → 마을 장부 (창고, 지은 건물, 잡은 몬스터, 쓰러짐) - 시대가 오르면 개척지 회의
  2) 결계 경보 4단계 이상, 3단계에 영상 재생 중, 영상이 버벅이면 모두 철수 (서버는 비면 스스로 멈춤 - 시청·독서가 먼저).
     3단계(아무도 안 볼 때)는 1명만 마을 안 일만 - 철수 뒤 REJOIN_MIN분은 다시 안 들어간다
  3) 깨어 있는 인물 MAX_BOTS명 접속 (이미 있던 사람 먼저, 밤이면 싸움꾼 먼저)
  4) 할 일이 없는 사람들의 일을 LLM 한 번으로 (이번 시대 목표, 창고, 서버 사정을 보고) - 규칙으로 바로잡는다:
     못 짓는 건물은 모자란 자원 모으기로, 밤엔 가장 센 사람이 순찰
  5) 채널(⛏️ 개척지)에 그 인물 이름으로 한마디 + 끝난 일 소식, 기억/연대기
- 매시(minecraft 모드의 회차): hourly() - 최근 개척지에서 일한 사람들이 시대 목표·창고·서버 사정을 두고 짧게 회의
  (한 줄에 LLM 한 번, 채널 + 게임 안에 있는 사람은 게임 채팅으로도)
- 결계 경보: 2단계까지 평소대로, 3단계는 가벼운 개척(영상 재생 중이면 철수), 4단계부터 철수 (MAX_STAGE/LIGHT_STAGE 주석)
- 자가 점검 (checkup, 매시 회의 전 + 봇 서비스가 두 바퀴 연속 응답 없으면 바로): 개척지 담당(stewards minecraft, 카이진)이
  스스로 고장을 찾아 정해진 방법으로 고치고, 못 고치는 것만 ⚠️로 사람에게 알린다 (같은 알림은 ALERT_H시간에 한 번).
  · 봇 서비스가 죽음/응답 없음 -> docker restart mc-agents (한 시간에 한 번, 하루 RESTART_MAX번) / 계속 죽음 -> 알림
  · RCON 비밀번호 틀림 -> 알림 (고칠 명령까지) / 일시 끊김은 기록만
  · 장부가 한 시간 넘게 그대로 -> 이유를 가린다 (결계 철수 / 아무도 없음 / 되풀이되는 실패) - 실패면 그 일을 잠시 쉬게 하고
    (avoid - 다른 일로 바꿔 맡김), 밭·짓기 실패면 개척지를 다시 둘러보게 한다 / STALL_ALERT_H시간 넘게 그대로면 알림
  · 매일 REPORT_HOUR시 이후 첫 점검 때 '개척 N일째 보고' (오늘 늘어난 것, 막힌 것)
- 몬스터 불시 습격(raids surge - 재생 중 부하 급등)은 개척지 둘레에 실제 몬스터가 나타나는 것으로 (surge_raid)
- 마인크래프트 이름은 영문 (MC_NAMES, 없으면 V+해시)
- 켜기: .env PORING_MINECRAFT=1 (서버를 띄운 뒤). API 주소 PORING_MC_API (기본 http://127.0.0.1:3010).
- 시험: 디스코드 /포링푸드마크 지금:True (봇 안에서 돌아야 채널에 올라간다 - 터미널에서 직접 부르면 로그에만 찍힌다)
저장: storage/poring_food/minecraft.json
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime

from ._log import pf_print as print  # print()를 봇 로그로
from .clock import now_kst
from .config import STATE_DIR

ENABLED = os.getenv("PORING_MINECRAFT", "0") not in ("0", "false", "False", "")
WORLD_MODE = os.getenv("PORING_WORLD_MODE", "minecraft").strip().lower()
API = os.getenv("PORING_MC_API", "http://127.0.0.1:3010").rstrip("/")
MAX_BOTS = int(os.getenv("PORING_MC_MAX_BOTS", "3"))
TICK_MIN = 10
STATE_PATH = os.path.join(STATE_DIR, "minecraft.json")
NAME = "⛏️ 개척지"
# 결계 경보(loadshed 단계, 모든 서버 중 가장 높은 단계)에 따라:
#   ~ MAX_STAGE(기본 2)        평소대로
#   ~ LIGHT_STAGE(기본 3)      가벼운 개척 - 아무도 영상을 안 볼 때만, 1명만, 멀리 나가지 않는 일만: 밭/짓기/순찰 그대로,
#                               나무·돌은 마을 둘레 32칸 안에서만(near - 멀리 걸어가 새 청크를 불러오지 않게), 광석은 돌로.
#                               (예전엔 이 일들을 '쉬기'로 바꿔서 밭이 없을 때 3단계면 아무것도 안 했다). 영상 재생 중이면 철수
#   LIGHT_STAGE 초과(4단계~)    모두 철수 (짐꾼까지 늦추는 단계 - 시청·독서가 먼저)
# 철수한 뒤에는 REJOIN_MIN분 동안 다시 들어가지 않는다 (접속할 때 스폰 청크를 불러오는 부하로 단계가 들썩이지 않게)
MAX_STAGE = int(os.getenv("PORING_MC_MAX_STAGE", "2"))
LIGHT_STAGE = int(os.getenv("PORING_MC_LIGHT_STAGE", "3"))
LIGHT_TASKS = ("farm", "build", "patrol", "rest", "audit", "gather", "mine")   # gather/mine은 near로
REJOIN_MIN = int(os.getenv("PORING_MC_REJOIN_MIN", "20"))
TALK_LINES = 5         # 매시 회의 발언 수

# 건물: 이름, 값 {자원: 개수}
BLUEPRINTS = {
    "hut": ("오두막", {"logs": 20}),
    "farm": ("밭", {"logs": 8}),
    "well": ("우물", {"stone": 10}),
    "wall": ("성벽 한 칸", {"stone": 12}),
    "tower": ("감시탑", {"stone": 24, "logs": 6}),
    "smithy": ("대장간", {"logs": 16, "stone": 16, "coal": 4}),
    "hall": ("마을회관", {"logs": 40, "stone": 40, "iron": 6}),
}
RESOURCES = {"logs": "통나무", "stone": "돌", "food": "식량", "coal": "석탄", "iron": "철"}
TASKS = {"gather": "나무 베기", "mine": "돌 캐기", "farm": "밭 수확", "ore": "광석 캐기", "build": "짓기",
         "patrol": "몬스터 순찰", "rest": "광장에서 쉬기", "audit": "개척지 둘러보기"}
TASK_RES = {"gather": "logs", "mine": "stone", "farm": "food", "ore": "iron"}   # 일 -> 주로 얻는 자원
RES_TASK = {"logs": "gather", "stone": "mine", "food": "farm", "coal": "ore", "iron": "ore"}
ROLES = {"gather": "나무꾼", "mine": "광부", "farm": "농부", "ore": "광산꾼", "build": "건축가", "patrol": "경비"}
ROLE_AFTER = 3            # (직업이 없을 때) 이만큼 해내면 그 일이 직업이 된다
SPECIALIST_COUNT = 18     # 직업에 맞는 일을 맡으면 (보통 12)
# 직업 - 사람마다 하나. 처음 개척지에 올 때 프로필(말/설정)을 보고 정하고(없으면 모자란 직업으로), 자기 직업 일을 되풀이한다.
#   농부: 밭을 일구고(없으면 짓고) 다 익은 밀을 거둔다 - 마을 식량 / 나무꾼: 나무 / 광부: 돌, 시대가 석탄·철을 원하면 광석 /
#   건축가: 재료가 되면 이번 시대 건물을 짓고, 모자라면 그 재료를 모은다 / 경비: 밤에 순찰, 낮엔 나무를 돕는다.
# 배가 고프면 마을 창고의 식량(농부가 거둔 밀)을 먹는다 (MEAL) - 창고가 비면 굶는다(봇 서비스에 배급 끊김).
# 매시 회의 뒤 의장이 한 사람의 직업을 바꿀 수 있다 (job_change - 식량이 모자라면 농부 하나 더 등).
JOBS = {"farm": "농부", "gather": "나무꾼", "mine": "광부", "build": "건축가", "patrol": "경비"}
JOB_SHARE = {"farm": 0.3, "gather": 0.3, "mine": 0.2, "build": 0.1, "patrol": 0.1}   # 직업이 없는 사람 나눌 때 목표 비율
JOB_WORDS = {"farm": ("농", "요리", "밭", "약초", "식", "주방", "빵", "엘프"),
             "mine": ("드워프", "대장", "광", "채굴", "철", "광산", "석공"),
             "build": ("건축", "목수", "장인", "기술", "설계", "공방"),
             "patrol": ("기사", "검", "전사", "궁수", "용병", "경비", "민병", "정찰", "무사", "레인저"),
             "gather": ("나무", "숲", "사냥", "벌목", "목재")}
MEAL = 3                  # 한 번 먹을 때 쓰는 마을 식량 (밀 3 = 빵 1)
START_FOOD = 24           # 처음 들고 온 식량
EAT = 1                   # 한 바퀴(TICK_MIN분)에 일하는 사람 한 명이 먹는 식량
# 시대: 이름, 다음 시대로 가려면 지어야 할 것(누적 개수), 풀어야 할 조건 {"stock": 창고에 있어야 할 양, "made": 지금까지 만든 양}
ERAS = [
    ("개척 캠프", {"hut": 2, "farm": 1}, {}),
    ("통나무 마을", {"hut": 4, "farm": 2, "well": 1}, {"stock": {"food": 20}}),       # 식량 자급
    ("돌담 마을", {"wall": 4, "tower": 1, "smithy": 1}, {"made": {"coal": 8}}),        # 석탄 캐기 시작
    ("성채 도시", {"tower": 3, "wall": 8, "hall": 1}, {"made": {"iron": 12}, "stock": {"food": 40}}),
    ("왕도", {}, {}),
]
FOCUS = {"food": "식량 늘리기", "logs": "통나무 모으기", "stone": "돌 모으기", "ore": "석탄·철 캐기",
         "build": "건물 짓기", "defense": "몬스터 막기"}

MC_NAMES = {
    "애순이": "Aesun", "소라": "Sora", "위벨": "Ubel", "슈타르크": "Stark", "페른": "Fern", "일레이나": "Elaina",
    "덴켄": "Denken", "크리스": "Chris", "메구밍": "Megumin", "베니마루": "Benimaru", "바닐": "Vanir", "칸네": "Kanne",
    "세나": "Sena", "라오프": "Laufen", "프랑": "Fran", "고브타": "Gobta", "소우에이": "Souei", "슈나": "Shuna",
    "리리나": "Ririna", "카즈마": "Kazuma", "아쿠아": "Aqua", "프리렌": "Frieren", "팔린": "Falin", "리그루드": "Rigurd",
    "다크니스": "Darkness", "가비루": "Gabiru", "자인": "Sein", "하쿠로": "Hakuro", "센시": "Senshi", "라이오스": "Laios",
    "카이진": "Kaijin", "나마리": "Namari", "이즈츠미": "Izutsumi", "게르도": "Geld", "알다프": "Aldaf",
    "쿠로베": "Kurobe", "루나": "Luna", "마르실": "Marcille", "칠착": "Chilchuck", "라비네": "Lawine",
    "아이젠": "Eisen", "카브루": "Kabru", "김선영": "SeonYoung", "강단이": "Dani",
}


def mc_world() -> bool:
    """이야기가 개척지 중심인지 (오아시스 마을 회차/대화를 멈춘다)"""
    return WORLD_MODE == "minecraft"


def mc_name(name: str) -> str:
    return MC_NAMES.get(name) or "V" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            st = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        st = {}
    res = st.setdefault("res", {})
    if "stock" in st:                       # 예전 장부(통나무만) -> 자원별
        res["logs"] = res.get("logs", 0) + int(st.pop("stock") or 0)
    if not st.get("deaths_v2"):             # 예전 봇은 한 번 쓰러질 때 여러 번 셌다 - 한 번 비운다
        st["deaths"] = 0
        st["lessons"] = [x for x in st.get("lessons") or [] if "쓰러짐" not in x.get("text", "")]
        st["deaths_v2"] = 1
    return st


def _save(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# ===================================================================== 문명
def era(st: dict) -> int:
    return min(int(st.get("era", 0)), len(ERAS) - 1)


def era_goal(st: dict) -> dict[str, tuple[int, int]]:
    """이번 시대 목표 {건물: (지은 수, 필요 수)}"""
    built = st.get("built") or {}
    return {b: (built.get(b, 0), n) for b, n in ERAS[era(st)][1].items()}


def allowed(st: dict) -> list[str]:
    """지금까지 시대에서 지을 수 있는 건물 (지금 시대 목표 포함)"""
    out = []
    for _, goal, *_ in ERAS[:era(st) + 1]:
        out += [b for b in goal if b not in out]
    return out or ["hut", "farm"]


def needed(st: dict) -> list[str]:
    """이번 시대에 아직 모자란 건물 (먼저 지을 것)"""
    return [b for b, (have, need) in era_goal(st).items() if have < need]


def conditions(st: dict) -> list[tuple[str, str, int, int]]:
    """이번 시대 조건 [(종류 stock|made, 자원, 지금, 필요)]"""
    res, made = st.get("res") or {}, st.get("made") or {}
    out = []
    for kind, need in (ERAS[era(st)][2] if len(ERAS[era(st)]) > 2 else {}).items():
        for k, n in need.items():
            out.append((kind, k, int((res if kind == "stock" else made).get(k, 0)), n))
    return out


def unmet(st: dict) -> list[str]:
    """다음 시대로 가는 길에 아직 남은 것 (건물 + 조건) - 사람이 읽는 말"""
    out = [f"{BLUEPRINTS[b][0]} {h}/{n}" for b, (h, n) in era_goal(st).items() if h < n]
    for kind, k, have, need in conditions(st):
        if have < need:
            out.append(f"{RESOURCES[k]} {'창고' if kind == 'stock' else '지금까지 캔 양'} {max(have, 0)}/{need}")
    return out


def can_afford(st: dict, bp: str) -> bool:
    res = st.get("res") or {}
    return all(res.get(k, 0) >= v for k, v in BLUEPRINTS[bp][1].items())


def short_of(st: dict, bp: str) -> str:
    """그 건물에 가장 모자란 자원 (logs/stone)"""
    res = st.get("res") or {}
    gaps = {k: v - res.get(k, 0) for k, v in BLUEPRINTS[bp][1].items()}
    return max(gaps, key=gaps.get)


def advance(st: dict) -> str | None:
    """이번 시대 목표를 다 채웠으면 다음 시대로. 반환: 새 시대 이름"""
    if era(st) < len(ERAS) - 1 and not unmet(st):
        st["era"] = era(st) + 1
        return ERAS[st["era"]][0]
    return None


def ledger(st: dict) -> str:
    res = st.get("res") or {}
    goal = ", ".join(f"{BLUEPRINTS[b][0]} {h}/{n}" for b, (h, n) in era_goal(st).items())
    goal = ", ".join([g for g in [goal] if g] + [f"{RESOURCES[k]} {'창고' if kd == 'stock' else '누적'} {max(h, 0)}/{n}"
                                                 for kd, k, h, n in conditions(st)]) or "없음 (마지막 시대)"
    built = ", ".join(f"{BLUEPRINTS[b][0]} {n}" for b, n in (st.get("built") or {}).items()) or "아직 없음"
    return (f"시대: {ERAS[era(st)][0]} (다음 시대까지: {goal}) / 창고: "
            + ", ".join(f"{RESOURCES[k]} {res.get(k, 0)}" for k in RESOURCES if k in res or k in ("logs", "stone"))
            + f" / 지은 것: {built} / 잡은 몬스터 {st.get('kills', 0)}")


# ===================================================================== 자율 운영 (문제, 직업, 교훈, 목표, 연대기)
def role(st: dict, name: str) -> str:
    """그 사람 직업 일(task 키) - 정해진 직업(jobs)이 먼저, 없으면 가장 많이 해낸 일이 ROLE_AFTER번 이상일 때. 없으면 ''"""
    if (st.get("jobs") or {}).get(name) in JOBS:
        return st["jobs"][name]
    sk = (st.get("skill") or {}).get(name) or {}
    best = max(sk, key=sk.get, default="")
    return best if best in ROLES and sk.get(best, 0) >= ROLE_AFTER else ""


def give_job(st: dict, agent: dict, now: datetime | None = None) -> tuple[str, str | None]:
    """직업이 없으면 정한다 (프로필 말 -> 없으면 모자란 직업). 반환: (직업, 새로 정했으면 연대기 말)"""
    from . import raids
    jobs = st.setdefault("jobs", {})
    name = agent["name"]
    if jobs.get(name) in JOBS:
        return jobs[name], None
    text = f"{agent.get('profile', '')} {agent.get('speech', '')}"
    hits = [j for j, words in JOB_WORDS.items() if any(w in text for w in words)]
    if not hits and raids.DEFENDERS.get(name, 0) >= 4:
        hits = ["patrol"]
    count = {j: sum(1 for v in jobs.values() if v == j) for j in JOBS}
    total = max(1, len(jobs))

    def lack(j):              # 목표 비율보다 얼마나 모자란지 (클수록 먼저)
        return JOB_SHARE[j] - count[j] / total
    job = max(hits, key=lack) if hits else max(JOBS, key=lack)
    jobs[name] = job
    return job, history(st, now or now_kst(), f"{name}이(가) 개척지의 {JOBS[job]}이(가) 되었다")


def change_job(st: dict, name: str, job: str, now: datetime, why: str = "") -> str | None:
    """직업 바꾸기 (의장 결정). 반환: 연대기 말"""
    jobs = st.setdefault("jobs", {})
    if job not in JOBS or jobs.get(name) == job:
        return None
    old = jobs.get(name)
    jobs[name] = job
    return history(st, now, f"{name}이(가) {JOBS.get(old, '일꾼')}에서 {JOBS[job]}(으)로 바뀌었다" + (f" - {why}" if why else ""))


def lesson(st: dict, text: str) -> None:
    """교훈 한 줄 (같은 말이면 횟수만 늘린다, 최근 8개)"""
    ls = st.setdefault("lessons", [])
    for x in ls:
        if x["text"] == text:
            x["n"] += 1
            ls.remove(x)
            ls.append(x)
            return
    ls.append({"text": text, "n": 1})
    del ls[:-8]


def day_no(st: dict, now: datetime) -> int:
    """개척 N일째 (처음 일한 날이 1일째)"""
    st.setdefault("founded", f"{now:%Y-%m-%d}")
    try:
        return (now.date() - datetime.strptime(st["founded"], "%Y-%m-%d").date()).days + 1
    except ValueError:
        return 1


def history(st: dict, now: datetime, text: str) -> str:
    """연대기 한 줄 (실제 장부/이벤트로 확인된 것만 부른다). 반환: 채널에 올릴 말"""
    line = f"개척 {day_no(st, now)}일째 · {text}"
    st["history"] = (st.get("history") or [])[-49:] + [{"at": f"{now:%Y-%m-%d %H:%M}", "text": line}]
    return f"📜 {line}"


def problems(st: dict, people: int | None = None) -> list[str]:
    """지금 개척지 문제 (규칙으로 찾는다 - LLM 아님). 급한 것부터"""
    res = st.get("res") or {}
    built = st.get("built") or {}
    out = []
    eaters = max(1, people if people is not None else len(st.get("online") or []) or MAX_BOTS)
    food = res.get("food", START_FOOD)
    if food <= 0:
        out.append("식량이 바닥났다 - 일하는 사람들이 굶고 있다" + ("" if built.get("farm") else " (밭도 없다)"))
    elif food < eaters * EAT * 6:
        out.append(f"식량이 {food}뿐이다 - 한 시간이면 바닥난다" + ("" if built.get("farm") else " (밭부터 지어야 한다)"))
    for kind, k, have, need in conditions(st):
        if have < need:
            out.append(f"다음 시대 조건: {RESOURCES[k]} {'창고' if kind == 'stock' else '지금까지 캔 양'} "
                       f"{max(have, 0)}/{need}")
    for b in needed(st)[:2]:
        gaps = {k: v - res.get(k, 0) for k, v in BLUEPRINTS[b][1].items() if res.get(k, 0) < v}
        if gaps:
            out.append(f"{BLUEPRINTS[b][0]}을(를) 지으려면 " + ", ".join(f"{RESOURCES[k]} {v}" for k, v in gaps.items())
                       + " 더 필요")
    for x in (st.get("lessons") or [])[::-1]:
        if x["n"] >= 3:
            out.append(f"되풀이되는 실패: {x['text']} ({x['n']}번)")
            break
    return out


def focus_metric(st: dict, focus: str) -> int:
    """목표 평가용 숫자 - 지금까지 만든 양/지은 수/잡은 수 (창고 잔고가 아니라 실제로 해낸 양)"""
    made = st.get("made") or {}
    if focus in ("food", "logs", "stone"):
        return int(made.get(focus, 0))
    if focus == "ore":
        return int(made.get("coal", 0)) + int(made.get("iron", 0))
    if focus == "build":
        return sum((st.get("built") or {}).values())
    if focus == "defense":
        return int(st.get("kills", 0))
    return 0


def evaluate_plan(st: dict, now: datetime) -> str | None:
    """지난 목표를 실제 장부 변화로 평가. 반환: 채널에 올릴 말 (목표가 없으면 None)"""
    plan = st.pop("plan", None)
    if not plan or plan.get("focus") not in FOCUS:
        return None
    got = focus_metric(st, plan["focus"]) - int(plan.get("base", 0))
    ok = got >= int(plan.get("target", 1))
    st.setdefault("plans", {"ok": 0, "miss": 0})["ok" if ok else "miss"] += 1
    text = (f"지난 목표 '{FOCUS[plan['focus']]} +{plan.get('target')}' → 실제 +{got} "
            + ("✅ 달성" if ok else "❌ 미달"))
    if not ok:
        lesson(st, f"목표 '{FOCUS[plan['focus']]}' 미달 (+{got}/{plan.get('target')})")
    return history(st, now, text)


PLAN_SYSTEM = """너는 숲 너머 개척지(마인크래프트 마을)의 회의 의장이다. 장부, 지금 문제, 교훈, 방금 회의 내용을 보고
다음 한 시간 동안 마을이 집중할 목표 하나를 정한다. focus: food(밭 수확으로 식량) / logs / stone / ore(석탄·철) /
build(건물) / defense(몬스터). target은 한 시간 동안 늘릴 양(식량·자원은 개수, build는 채 수, defense는 마리 수) -
지난 결과와 교훈을 보고 해낼 만큼만. 급한 문제(식량 부족)가 있으면 그것부터.
[직업]을 보고, 문제를 풀려면 한 사람의 직업을 바꿀 수 있다(예: 식량이 계속 모자라면 나무꾼 하나를 농부로). 바꿀 필요 없으면 비운다.
JSON으로만: {"focus": "food|logs|stone|ore|build|defense", "target": 숫자, "why": "한 문장",
 "job_change": {"name": "이름 또는 빈칸", "job": "farm|gather|mine|build|patrol", "why": "한 문장"}}"""


def set_plan(st: dict, now: datetime, meeting: list[dict] | None = None, llm=None) -> dict | None:
    """의장이 다음 한 시간 목표를 정한다 (LLM 한 번, 실패하면 규칙으로)"""
    probs = problems(st)
    facts = (f"[장부] {ledger(st)}\n[지금 문제] " + (" / ".join(probs) or "없음")
             + "\n[교훈] " + (" / ".join(f"{x['text']}({x['n']})" for x in (st.get("lessons") or [])[-5:]) or "없음")
             + f"\n[지난 목표 성적] {st.get('plans') or '처음'}"
             + "\n[직업] " + (", ".join(f"{n}={JOBS[j]}" for n, j in (st.get("jobs") or {}).items() if j in JOBS) or "아직 없음")
             + "\n[방금 회의] " + (" / ".join(f"{x['speaker']}: {x['line'][:60]}" for x in (meeting or [])) or "없음"))
    if llm is None:
        from .llm import llm_json
        llm = lambda s, u: llm_json(s, u, temperature=0.3, timeout=45, tag="개척지 목표")  # noqa: E731
    try:
        out = llm(PLAN_SYSTEM, facts) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 목표 정하기 실패: {e}")
        out = {}
    focus = out.get("focus") if out.get("focus") in FOCUS else None
    if not focus:     # 규칙: 식량 문제 > 다음 시대 건물 자원 > 통나무
        p = " ".join(probs)
        focus = "food" if "식량" in p else "ore" if ("석탄" in p or "철" in p) else "build" if needed(st) else "logs"
    try:
        target = min(200, max(0, int(out.get("target") or 0)))
    except (TypeError, ValueError):
        target = 0
    target = target or {"food": 12, "logs": 24, "stone": 24, "ore": 6, "build": 1, "defense": 3}[focus]
    st["plan"] = {"focus": focus, "target": target, "why": str(out.get("why") or "")[:120],
                  "base": focus_metric(st, focus), "at": f"{now:%Y-%m-%d %H:%M}"}
    jc = out.get("job_change") if isinstance(out.get("job_change"), dict) else {}
    if jc.get("name") in (st.get("jobs") or {}):
        line = change_job(st, jc["name"], str(jc.get("job")), now, str(jc.get("why") or "")[:60])
        if line:
            st["plan"]["job_change"] = line
    return st["plan"]


# ===================================================================== HTTP (mc-agents)
class Api:
    def __init__(self, base: str = API):
        self.base = base

    def get(self, path: str) -> dict:
        import requests
        r = requests.get(self.base + path, timeout=5)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        """POST. /raid 말고는 실패해도 예외를 던지지 않는다 - 봇 서비스가 잠깐 굼떠도(접속/나가기 응답 지연) 한 바퀴 전체가
        멈추지 않게 (예전엔 /leave 하나가 15초 넘게 걸려 그 바퀴의 일 나눠 주기가 통째로 날아갔다). 반환에 "error"가 생긴다"""
        import requests
        try:
            r = requests.post(self.base + path, json=body, timeout=75 if path == "/task" else 30)
            if r.status_code >= 500:
                r.raise_for_status()
            return r.json() if r.content else {}
        except Exception as e:  # noqa: BLE001
            if path == "/raid":
                raise
            print(f"[개척지] 봇 서비스 {path} 실패: {str(e)[:120]}")
            return {"error": str(e)[:200]}


# ===================================================================== 서버 상황 (이야기에 섞는다)
def server_line() -> str:
    """지금 실제 서버 사정을 마을 말로 한 줄 - 결계 단계, 환영극(영상) 손님, 최근 몬스터 습격"""
    from . import loadshed, raids
    try:
        stage = loadshed.stage()
        p = (loadshed._load().get("hosts") or {}).get("plex01") or {}
        bits = [f"결계 경보 {stage}단계({loadshed.STAGE_NAMES.get(stage, '')})"]
        if p.get("playing"):
            bits.append(f"환영극장에 손님 {p['playing']}명이 상영 중" + (" - 화면이 버벅임" if p.get("struggling") else ""))
        r = raids.last()
        if r and r.get("raid"):
            bits.append(f"최근 몬스터 습격({r['at'][11:16]}): {r['monster']} {r['outcome']}")
        return " / ".join(bits)
    except Exception:  # noqa: BLE001
        return ""


def _remember_line(st: dict, name: str, line: str, now: datetime) -> None:
    rec = st.setdefault("recent", {})
    rec[name] = (rec.get(name) or [])[-4:] + [f"{now:%m/%d %H:%M} {line}"]
    st["log"] = (st.get("log") or [])[-19:] + [f"{now:%H:%M} {name}: {line}"]


def prompt_block(name: str, now: datetime | None = None) -> str:
    """인물 판단 프롬프트용 (oasis 모드) - 개척지 사정 + 내가 거기서 한 일 + 서버 사정과 엮어 말하라는 안내"""
    if not ENABLED:
        return ""
    st = _load()
    if not st.get("built") and not (st.get("res") or {}) and not st.get("recent"):
        return ""
    online = [k for k, v in MC_NAMES.items() if v in (st.get("online") or [])]
    mine = (st.get("recent") or {}).get(name) or []
    lines = [f"[숲 너머 개척지 (마인크래프트 마을)] {ledger(st)} · 지금 나가 있는 사람: {', '.join(online) or '없음'}"
             + (f" · 개척 쉬는 중({st['why'][6:]})" if str(st.get('why', '')).startswith("쉬는 중") else "")]
    if mine:
        lines.append("내가 개척지에서 한 일: " + " / ".join(mine[-3:]))
    lines.append("개척지 일은 결계가 높거나 환영극 손님이 버벅이면 멈춘다 - 서버 사정(결계, 환영극 손님, 몬스터 습격)과 "
                 "개척지 일(나무, 돌, 건물, 밤 순찰, 다음 시대)을 엮어서 얘기해도 된다.")
    return "\n".join(lines)


# ===================================================================== 개척지 회의 (대화)
TALK_SYSTEM_TAIL = """
[지금 너는 숲 너머 개척지(마인크래프트 세계)에서 동료들과 개척지 문명을 키우고 있다.]
개척지 일(나무 베기, 돌 캐기, 건물 짓기, 밤 순찰, 다음 시대로 가기)과 마을 서버 사정(결계 경보 - 높으면 다 같이 철수해야 한다,
환영극장 손님 - 손님이 있으면 조용히 일해야 한다, 몬스터 습격)을 엮어서 말한다. 숫자는 주어진 것만 쓴다."""


def talk(topic: str, people: list[str], st: dict | None = None, now: datetime | None = None, speak=None,
         post=None, api: Api | None = None, lines: int = TALK_LINES) -> list[dict]:
    """개척지 회의 - people이 돌아가며 한 줄씩 (각자 LLM). 채널 + 게임 안에 있으면 게임 채팅으로도. 반환 [{"speaker","line"}]"""
    from . import agents as ag, memory
    now = now or now_kst()
    st = st if st is not None else _load()
    agents = {a["name"]: a for a in ag.load_agents()}
    people = [p for p in dict.fromkeys(people) if p in agents][:4]
    if len(people) < 2:
        return []
    facts = (f"[개척지 장부] {ledger(st)}\n[지금 문제] " + (" / ".join(problems(st)) or "없음")
             + (f"\n[이번 시간 목표] {FOCUS[pl['focus']]} +{pl['target']} ({pl.get('why', '')})"
                if (pl := st.get("plan")) and pl.get("focus") in FOCUS else "")
             + "\n[최근 개척지 소식] " + (" / ".join((st.get("log") or [])[-6:]) or "없음")
             + (f"\n[마을 서버 사정] {s}" if (s := server_line()) else ""))
    if post is None:
        def post(n, t):
            ag._post(agents[n], t)
    online = set(st.get("online") or [])
    out: list[dict] = []
    for i in range(lines):
        who = people[i % len(people)]
        a = agents[who]
        talk_so_far = "\n".join(f"{x['speaker']}: {x['line']}" for x in out) or "(아직 아무도 말하지 않음)"
        user = (f"[개척지 회의 주제] {topic}\n{facts}\n[같이 있는 사람] {', '.join(p for p in people if p != who)}\n"
                f"[지금까지 오간 말]\n{talk_so_far}\n\n{who}로서 다음에 할 말 한두 문장. 다음에 뭘 지을지, 누가 뭘 할지, "
                "자원이 뭐가 모자란지, 서버 사정 때문에 어떡할지 - 내 성격대로 제안하거나 반대하거나 딴지 걸어도 된다.\n"
                + ag._voice(a) + '\nJSON으로만: {"say": "할 말"}')
        if speak is not None:
            say = speak(who, user)
        else:
            from .llm import llm_json
            say = str((llm_json(ag._system(a) + TALK_SYSTEM_TAIL, user, temperature=0.9, timeout=45,
                                tag=f"개척지 {who}", agent=who) or {}).get("say") or "").strip()
        say = str(say or "").strip()[:280]
        if not say:
            continue
        out.append({"speaker": who, "line": say})
        post(who, f"-# {NAME} · {now:%H:%M} · {topic[:30]}\n{say}")
        if api is not None and mc_name(who) in online:
            try:
                api.post("/say", {"name": mc_name(who), "text": say[:180]})
            except Exception:  # noqa: BLE001
                pass
    if out:
        summary = f"개척지 회의 '{topic}': " + " / ".join(f"{x['speaker']}: {x['line'][:40]}" for x in out[-3:])
        for p in people:
            try:
                memory.remember(p, summary, 4, [q for q in people if q != p], "개척지")
            except Exception as e:  # noqa: BLE001
                print(f"[개척지] 기억 실패: {e}")
    return out


def _react(detail: str, title: str, people: list[str], react=None, st: dict | None = None) -> None:
    """사건에 관련 인물이 바로 반응 - 개척지 모드면 개척지 회의 한 판, oasis 모드면 마을 사건(react_now)"""
    try:
        if react is not None:
            react(detail, title, people, kind="개척지")
        elif mc_world():
            talk(f"{title} - {detail}", people, st=st, lines=3)
        else:
            from . import agents as ag
            ag.react_now(detail, title, people, kind="개척지")
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 반응 실패: {e}")


def hourly(now: datetime | None = None, speak=None, post=None, llm=None) -> list[dict]:
    """[minecraft 모드의 매시 회차] 최근 개척지에서 일한 사람들(+ 깨어 있는 사람)이 이번 시대 목표를 두고 회의"""
    if not ENABLED:
        return []
    from . import agents as ag
    now = now or now_kst()
    st = _load()
    agents = ag.load_agents()
    awake = [a["name"] for a in agents if not ag._asleep(a, now.hour)]
    recent = [n for n in (st.get("recent") or {}) if n in awake]
    rnd = random.Random(f"{now:%Y%m%d%H}")
    rnd.shuffle(awake)
    people = list(dict.fromkeys(recent[-2:] + awake))[:3]
    if post is None:
        by = {a["name"]: a for a in agents}

        def post(n, t):
            (ag._post(by[n], t) if n in by else ag._post_as(n, t))
    try:
        checkup(now, post=post)                # 자가 점검 (고칠 수 있는 건 고치고, 못 고치면 알림)
        st = _load()
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 자가 점검 실패: {e}")
    review = evaluate_plan(st, now)            # 지난 한 시간 목표를 실제 장부로 평가
    if review:
        post(NAME, f"-# {NAME} · {now:%H:%M} · 목표 평가\n{review}")
    need = unmet(st)
    probs = problems(st)
    topic = (f"{ERAS[era(st)][0]} - " + (f"급한 문제: {probs[0]}" if probs and "식량" in probs[0]
                                         else "다음 시대로 가려면 " + ", ".join(need) if need else "다음엔 뭘 할까")
             + (f" / {review[2:]}" if review else ""))
    try:
        api = Api() if st.get("online") else None
    except Exception:  # noqa: BLE001
        api = None
    out = talk(topic, people, st=st, now=now, speak=speak, post=post, api=api)
    plan = set_plan(st, now, out, llm=llm)
    if plan:
        try:
            from . import stewards
            chair = stewards.steward("chair", agents)
        except Exception:  # noqa: BLE001
            chair = NAME
        post(chair, f"-# {NAME} · {now:%H:%M} · 다음 한 시간 목표\n"
             f"🎯 {FOCUS[plan['focus']]} +{plan['target']}" + (f" - {plan['why']}" if plan.get("why") else "")
             + (f"\n{plan['job_change']}" if plan.get("job_change") else ""))
    _save(st)
    print(f"[개척지] 매시 회의 {len(out)}마디 ({', '.join(people)}) · 목표 {plan and plan['focus']}")
    return out


# ===================================================================== 판단 재료
def _playing() -> dict:
    from . import loadshed
    return (loadshed._load().get("hosts") or {}).get("plex01") or {}


def busy() -> str | None:
    """지금 개척지 일을 쉬어야 하는 이유 (시청·독서가 먼저)"""
    from . import loadshed
    stage = loadshed.stage()
    if stage > LIGHT_STAGE:
        return f"결계 경보 {stage}단계"
    p = _playing()
    if p.get("playing") and p.get("struggling"):
        return "환영극(영상)이 버벅이는 중"
    if stage > MAX_STAGE and p.get("playing"):
        return f"결계 경보 {stage}단계에 환영극 상영 중"
    return None


def light() -> bool:
    """가벼운 개척 (결계 경보가 MAX_STAGE를 넘었지만 LIGHT_STAGE 이하 - busy()가 None일 때만 의미)"""
    from . import loadshed
    return loadshed.stage() > MAX_STAGE


def apply_events(st: dict, events: list[dict], by_mc: dict[str, str], now: datetime | None = None) -> list[tuple[str, str]]:
    """이벤트 -> 장부 (+ 일 성적, 직업 경험, 교훈, 연대기). 반환 [(인물, 소식 한 줄)] (알릴 만한 것만)"""
    now = now or now_kst()
    out = []
    pend = st.setdefault("pending", {})
    built = st.setdefault("built", {})
    res = st.setdefault("res", {})
    made = st.setdefault("made", {})
    stats = st.setdefault("stats", {})
    for e in events:
        who = by_mc.get(e.get("name", ""), e.get("name", ""))
        kind = e.get("kind")
        task = e.get("task")
        r = e.get("result") or {}
        if task in TASKS and kind in ("done", "failed"):
            stats.setdefault(task, {"done": 0, "failed": 0, "empty": 0})
        gained = {}
        if kind == "done" and task in ("gather", "mine", "farm", "ore"):
            for key in RESOURCES:
                n = int(r.get(key) or 0)
                if n > 0:
                    gained[key] = n
                    res[key] = res.get(key, 0) + n
                    if not made.get(key) and key in ("coal", "iron"):
                        out.append((NAME, history(st, now, f"{who}이(가) 처음으로 {RESOURCES[key]}을(를) 캐 왔다")))
                    made[key] = made.get(key, 0) + n
            if task == "farm" and r.get("rebuilt"):
                out.append((who, f"망가진(또는 장부에만 있던) 밭 {r['rebuilt']}곳을 다시 일구고 밀을 심었다"))
            if gained:
                stats[task]["done"] += 1
                out.append((who, ", ".join(f"{RESOURCES[k]} {n}개" for k, n in gained.items())
                            + "를 마을 창고에 쌓았다 (창고 " + ", ".join(f"{RESOURCES[k]} {res[k]}" for k in gained) + ")"))
            else:
                stats[task]["empty"] += 1
                if task == "farm" and r.get("rebuilt"):
                    pass
                elif task == "farm" and "crops" in r and not r.get("crops"):
                    lesson(st, "밭 수확: 밭에 밀이 한 포기도 없음 (밭이 망가졌거나 자리를 못 찾음 - 밭을 새로 지어야)")
                else:
                    lesson(st, f"{TASKS[task]}: 빈손으로 돌아옴" + (" (아직 덜 익음 - 기다리거나 밭을 늘려야)" if task == "farm"
                                                             else " (근처에 없음)"))
        elif kind == "done" and task == "build":
            bp = r.get("blueprint") or (pend.get(e.get("name")) or {}).get("blueprint")
            if bp in BLUEPRINTS:
                stats["build"]["done"] += 1
                first = not built.get(bp)
                built[bp] = built.get(bp, 0) + 1
                gained = {"build": 1}
                out.append((who, f"개척지에 {BLUEPRINTS[bp][0]}을(를) 새로 지었다 (모두 {built[bp]}채)"))
                if first:
                    out.append((NAME, history(st, now, f"{who}이(가) 개척지 첫 {BLUEPRINTS[bp][0]}을(를) 세웠다")))
        elif kind == "failed":
            if task == "build":
                for k, v in ((pend.get(e.get("name")) or {}).get("cost") or {}).items():   # 못 지었으면 자원을 되돌린다
                    res[k] = res.get(k, 0) + int(v)
            if task in TASKS:
                stats[task]["failed"] += 1
                lesson(st, f"{TASKS[task]} 실패: {str(e.get('error') or '이유 모름')[:60]}")
        elif kind == "done" and task == "audit":
            # 둘러보기: 장부에만 있고 실제로는 없는 건물을 장부에서 뺀다 (예전에 명령 권한이 없어 실패했는데 지어졌다고 적힌 것)
            st["audited"] = f"{now:%Y-%m-%d %H:%M}"
            fixed = []
            for bp, n in (r.get("found") or {}).items():
                if bp in BLUEPRINTS and int(n) < built.get(bp, 0):
                    fixed.append(f"{BLUEPRINTS[bp][0]} {built[bp]}→{int(n)}")
                    built[bp] = int(n)
                    if not built[bp]:
                        built.pop(bp)
            if fixed:
                st["slots"] = {bp: built.get(bp, 0) for bp in BLUEPRINTS if built.get(bp)}   # 빈 자리부터 다시 짓게
                out.append((NAME, history(st, now, f"{who}이(가) 개척지를 둘러보니 장부와 달랐다 - " + ", ".join(fixed)
                                          + " (실제로 서 있는 것만 남김)")))
            else:
                out.append((who, f"개척지를 한 바퀴 둘러봤다 - 건물 {int(r.get('checked') or 0)}곳이 장부대로 서 있다"))
        elif kind == "done" and task == "patrol":
            stats["patrol"]["done"] += 1
            k = int(r.get("kills") or 0)
            st["kills"] = st.get("kills", 0) + k
            if k:
                gained = {"kills": k}
                out.append((who, f"개척지 둘레를 돌며 몬스터 {k}마리를 쓰러뜨렸다 (모두 {st['kills']}마리)"))
        elif kind == "ate":                     # 배고파서 마을 식량을 먹음 (농부가 거둔 밀)
            res["food"] = max(0, res.get("food", START_FOOD) - MEAL)
        elif kind == "died":
            st["deaths"] = st.get("deaths", 0) + 1
            out.append((who, "몬스터에게 당해 쓰러졌다가 광장에서 다시 일어났다"))
            lesson(st, "밤에 몬스터에게 쓰러짐 - 순찰을 늘리거나 감시탑/성벽이 필요")
        if gained and task in ROLES:          # 해낸 일 -> 그 사람 경험 (직업 분화)
            sk = st.setdefault("skill", {}).setdefault(who, {})
            before = role(st, who)
            sk[task] = sk.get(task, 0) + 1
            if role(st, who) and role(st, who) != before:
                out.append((NAME, history(st, now, f"{who}이(가) 개척지의 {ROLES[role(st, who)]}이(가) 되었다")))
        if kind in ("done", "failed", "left"):
            pend.pop(e.get("name"), None)
        st["last_event"] = max(st.get("last_event", 0), int(e.get("id") or 0))
    return out


GROW_MIN = 3      # 밀이 한 단계 자라는 데 걸리는 시간(분) - 7단계면 약 20분에 다 익는다


def grow_stages(st: dict, now: datetime) -> int:
    """지난 수확 뒤 흐른 시간만큼 밀이 자란 단계 (밭 청크는 사람이 근처에 없으면 시간이 안 흘러서 봇 쪽에서 올려 준다)"""
    try:
        since = (now - datetime.strptime(st["farm_at"], "%Y-%m-%d %H:%M")).total_seconds() / 60
    except (KeyError, ValueError):
        return 7                       # 처음 - 지어 둔 뒤로 충분히 지났다고 본다
    return max(0, min(7, int(since // GROW_MIN)))


def eat(st: dict, people: int, now: datetime) -> str | None:
    """식량 사정 (먹는 건 봇이 배고플 때 'ate' 이벤트로 MEAL씩 - apply_events). 반환: 부족이 시작되거나 풀렸을 때 연대기 말"""
    res = st.setdefault("res", {})
    res.setdefault("food", START_FOOD)
    if res["food"] <= 0 and not st.get("hungry"):
        st["hungry"] = f"{now:%Y-%m-%d %H:%M}"
        lesson(st, "식량이 바닥남 - 밭을 늘리고 수확을 맡겨야")
        return history(st, now, "식량이 바닥났다 - 개척지에 첫 굶주림")
    if st.get("hungry") and res["food"] >= max(6, people * EAT * 6):
        st.pop("hungry")
        return history(st, now, f"밭에서 거둔 밀로 식량 부족을 넘겼다 (창고 식량 {res['food']})")
    return None


def needed_jobs(st: dict, day: bool | None) -> list[str]:
    """지금 개척지에 먼저 필요한 직업 순서 (식량 > 지을 수 있는 건물 > 모자란 재료 > 밤 순찰 > 나무/돌)"""
    res = st.get("res") or {}
    out = []
    if res.get("food", START_FOOD) < MAX_BOTS * MEAL * 3 or not (st.get("built") or {}).get("farm"):
        out.append("farm")
    if day is False:
        out.append("patrol")
    need = needed(st)
    if any(can_afford(st, b) for b in need):
        out.append("build")
    for b in need:
        j = RES_TASK.get(short_of(st, b), "gather")
        out.append("mine" if j == "ore" else j)
    if any(h < n and k in ("coal", "iron") for kd, k, h, n in conditions(st)):
        out.append("mine")
    return list(dict.fromkeys(out + ["gather", "mine", "farm", "build", "patrol"]))


def pick(agents: list[dict], online: list[str], day: bool | None, k: int = MAX_BOTS, rnd=None,
         st: dict | None = None) -> list[dict]:
    """접속할 사람 (깨어 있는 사람 중). 이미 들어가 있던 사람은 되도록 그대로 두고, 빈자리는 지금 필요한 직업 사람으로 채운다
    (가장 급한 직업이 아무도 없으면 한 사람을 바꾼다). 직업 장부(st)가 없으면 예전처럼 들어가 있던 사람 먼저, 밤엔 싸움꾼 먼저"""
    from . import raids
    from .agents import _asleep
    rnd = rnd or random.Random()
    hour = now_kst().hour
    awake = [a for a in agents if not _asleep(a, hour)]
    rnd.shuffle(awake)

    def rank(a):
        fighter = a["name"] in raids.DEFENDERS
        return (0 if mc_name(a["name"]) in online else 1, 0 if (fighter and day is False) else 1)
    pool = sorted(awake, key=rank)
    if st is None:
        return pool[:k]
    job = lambda a: role(st, a["name"]) or give_job(st, a)[0]  # noqa: E731
    need = needed_jobs(st, day)
    chosen = [a for a in pool if mc_name(a["name"]) in online][:k]
    if chosen and len(chosen) >= k and need[0] not in {job(a) for a in chosen}:
        # 가장 급한 직업(예: 식량이 없는데 농부가 없음)이 비었다 - 직업이 겹치거나 덜 급한 사람 하나를 바꾼다
        prio = {j: i for i, j in enumerate(need)}
        drop = max(chosen, key=lambda a: (sum(job(b) == job(a) for b in chosen), prio.get(job(a), 99)))
        if any(job(a) == need[0] for a in pool if a not in chosen):
            chosen.remove(drop)
    for j in need:
        if len(chosen) >= k:
            break
        if j in {job(a) for a in chosen}:
            continue
        c = next((a for a in pool if a not in chosen and job(a) == j), None)
        if c:
            chosen.append(c)
    for a in pool:
        if len(chosen) >= k:
            break
        if a not in chosen:
            chosen.append(a)
    return chosen[:k]


DECIDE_SYSTEM = """너는 숲 너머 개척지(마인크래프트 세계)의 일꾼들이 각자 직업 일을 하러 나가며 게임 채팅에 칠 한마디를 쓴다.
사람마다 [할 일]이 정해져 있다 - 그 일에 맞게, 그 사람 말투로 20자 안팎 (실제 서버/사람 이름 금지). [지금 문제]나 마을 서버 사정
(결계 경보, 환영극 손님, 몬스터 습격)이 있으면 자연스럽게 엮어도 된다 - 예: 밀이 바닥났대, 내가 얼른 거둘게.
JSON으로만: {"says": [{"name": "이름", "say": "한마디"}]}"""


def _gather_for(sim: dict, bp: str) -> str:
    """그 건물에 가장 모자란 자원을 모으는 일"""
    return RES_TASK.get(short_of(sim, bp), "gather")


def job_task(st: dict, name: str, sim: dict, day: bool | None) -> tuple[str, str]:
    """그 사람이 직업대로 지금 할 일 (task, blueprint) - sim은 이번 바퀴에 예약한 재료를 뺀 장부"""
    job = role(st, name) or "gather"
    farms = (sim.get("built") or {}).get("farm", 0)
    if job == "farm":
        if farms:
            return "farm", ""
        if can_afford(sim, "farm"):
            return "build", "farm"
        return "gather", ""                      # 밭 지을 통나무부터
    if job == "build":
        for b in needed(sim) or allowed(sim):
            if can_afford(sim, b):
                return "build", b
        need = needed(sim) or ["hut"]
        return _gather_for(sim, need[0]), ""     # 재료가 모자라면 그 재료를 모은다
    if job == "mine":
        want_ore = any(h < n and k in ("coal", "iron") for kd, k, h, n in conditions(sim)) or any(
            short_of(sim, b) in ("coal", "iron") for b in needed(sim) if not can_afford(sim, b))
        return ("ore" if want_ore else "mine"), ""
    if job == "patrol":
        return ("patrol" if day is False else "gather"), ""
    return "gather", ""


def decide(people: list[dict], st: dict, day: bool | None, llm=None, now: datetime | None = None) -> list[dict]:
    """할 일 정하기 - 각자 직업대로 (job_task), 재료는 이번 바퀴에 예약한 만큼 미리 뺀 장부(sim)로 계산.
    규칙으로 바로잡기: 굶는데 밭 일을 하는 사람이 없으면 한 명이 거든다 / 목표 일을 아무도 안 하면 한 명이 거든다 /
    자가 점검이 쉬게 한 일은 다른 일로 / 밤엔 가장 센 사람이 순찰. 한마디(say)만 LLM 한 번"""
    from . import raids
    if not people:
        return []
    for p in people:
        give_job(st, p, now)
    plan = st.get("plan") if (st.get("plan") or {}).get("focus") in FOCUS else None
    sim = {"res": dict(st.get("res") or {}), "built": dict(st.get("built") or {}), "era": st.get("era", 0),
           "made": dict(st.get("made") or {})}
    farms = sim["built"].get("farm", 0)       # 이번 바퀴 전에 이미 있는 밭 (짓는 중인 밭은 아직 수확 못 함)
    tasks = []
    for p in people:
        task, bp = job_task(st, p["name"], sim, day)
        if task == "build":
            for k, v in BLUEPRINTS[bp][1].items():
                sim["res"][k] = sim["res"].get(k, 0) - v
            sim["built"][bp] = sim["built"].get(bp, 0) + 1          # 같은 건물을 둘이 짓지 않게
        tasks.append({"name": p["name"], "task": task, "blueprint": bp, "say": "", "job": role(st, p["name"])})

    avoid = avoided(st, now)
    for t in tasks:          # 자가 점검이 잠시 쉬게 한 일은 다른 일로
        if t["task"] in avoid:
            t.update(task=next(x for x in ("mine", "gather", "farm" if farms else "rest", "rest") if x not in avoid),
                     blueprint="")

    def assign(task: str, bp: str = "") -> None:
        """그 일을 아무도 안 하면 한 명이 거든다 (그 직업 사람이 아니면 직업이 덜 급한 사람, 순찰 중인 사람은 빼고)"""
        if any(t["task"] == task for t in tasks):
            return
        cand = [t for t in tasks if t["task"] not in ("patrol", "build")] or [t for t in tasks if t["task"] != "patrol"]
        if not cand:
            return
        cand[-1].update(task=task, blueprint=bp)

    hungry = (st.get("res") or {}).get("food", START_FOOD) < len(people) * MEAL * 2
    if hungry or (plan and plan["focus"] == "food"):
        if farms:
            assign("farm")
        elif can_afford(sim, "farm") and not any(t["blueprint"] == "farm" for t in tasks):
            assign("build", "farm")
    elif plan and plan["focus"] in ("logs", "stone", "ore"):
        assign(RES_TASK.get(plan["focus"], "ore"))
    if day is False and not any(t["task"] == "patrol" for t in tasks):
        # 경비 > (짓기·밭이 아닌) 나무/돌 하던 사람 > 가장 센 사람
        fighter = max(tasks, key=lambda t: (t["job"] == "patrol", t["task"] in ("gather", "mine", "ore", "rest"),
                                            raids.DEFENDERS.get(t["name"], 0)))
        fighter.update(task="patrol", blueprint="")

    # 한마디 (LLM 한 번 - 실패해도 일은 그대로)
    facts = ((f"[마을 서버 사정] {server}\n" if (server := server_line()) else "")
             + f"지금: {'낮' if day else '밤' if day is False else '모름'}\n[개척지 장부] {ledger(st)}\n"
             + "[지금 문제] " + (" / ".join(problems(st, len(people))) or "없음") + "\n"
             + (f"[이번 시간 목표] {FOCUS[plan['focus']]} +{plan['target']}\n" if plan else "")
             + "[할 일]\n" + "\n".join(
                 f"- {t['name']} ({JOBS.get(t['job'], '일꾼')}): {TASKS[t['task']]}"
                 + (f" {BLUEPRINTS[t['blueprint']][0]}" if t["blueprint"] else "")
                 + f" / {str(next((p.get('profile', '') for p in people if p['name'] == t['name']), ''))[:60]}"
                 for t in tasks))
    if llm is None:
        from .llm import llm_json
        llm = lambda s, u: llm_json(s, u, temperature=0.8, timeout=60, tag="개척지")  # noqa: E731
    try:
        says = {str(x.get("name")): str(x.get("say") or "").strip()[:60]
                for x in ((llm(DECIDE_SYSTEM, facts) or {}).get("says") or []) if isinstance(x, dict)}
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 한마디 실패: {e}")
        says = {}
    for t in tasks:
        t["say"] = says.get(t["name"], "")
    return tasks


# ===================================================================== 자가 점검
ALERT_H = 6            # 같은 알림은 이 시간에 한 번
RESTART_MAX = 3        # 봇 서비스 자동 재시작 하루 최대
STALL_ALERT_H = 3      # 장부가 이만큼 그대로면 (결계 철수 말고) 사람에게
REPORT_HOUR = 21       # 하루 보고 시각 (이후 첫 점검 때)
AVOID_MIN = 60         # 되풀이되는 실패가 난 일을 쉬게 하는 시간


def avoided(st: dict, now: datetime | None = None) -> set[str]:
    now = now or now_kst()
    return {k for k, until in (st.get("avoid") or {}).items() if str(until) > f"{now:%Y-%m-%d %H:%M}"}


def progress(st: dict) -> int:
    """장부 진척 숫자 (만든 자원 + 지은 건물 + 잡은 몬스터) - 늘지 않으면 멈춘 것"""
    return sum(int(v) for v in (st.get("made") or {}).values()) + sum((st.get("built") or {}).values()) + int(st.get("kills", 0))


def _restart_agents() -> str:
    """docker restart mc-agents (봇이 도는 etc에서). 반환: '' 성공, 아니면 오류"""
    import subprocess
    try:
        r = subprocess.run(["docker", "restart", "mc-agents"], capture_output=True, text=True, timeout=120)
        return "" if r.returncode == 0 else (r.stderr or r.stdout or "실패").strip()[:200]
    except Exception as e:  # noqa: BLE001
        return str(e)[:200]


def _steward() -> str:
    try:
        from . import stewards
        return stewards.steward("minecraft")
    except Exception:  # noqa: BLE001
        return NAME


def _alert(st: dict, now: datetime, post, key: str, what: str, todo: str) -> bool:
    """사람에게 알림 (같은 key는 ALERT_H시간에 한 번). 반환: 올렸는지"""
    alerts = st.setdefault("alerts", {})
    last = alerts.get(key, "")
    try:
        if last and (now - datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() < ALERT_H * 3600:
            return False
    except ValueError:
        pass
    alerts[key] = f"{now:%Y-%m-%d %H:%M}"
    post(_steward(), f"-# {NAME} · {now:%H:%M} · 자가 점검\n⚠️ **사람 손이 필요해요** - {what}\n> 할 일: {todo}")
    print(f"[개척지] 자가 점검 알림: {what} / {todo}")
    return True


def heal_agents(st: dict, now: datetime, post, restart=None, why: str = "") -> str:
    """봇 서비스 다시 띄우기 (한 시간에 한 번, 하루 RESTART_MAX번). 반환: 한 일 한 줄"""
    ck = st.setdefault("check", {})
    day = f"{now:%Y-%m-%d}"
    if ck.get("restart_day") != day:
        ck.update(restart_day=day, restarts=0)
    last = ck.get("restart_at", "")
    try:
        recent = last and (now - datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() < 3600
    except ValueError:
        recent = False
    if recent:
        return ""
    if ck["restarts"] >= RESTART_MAX:
        _alert(st, now, post, "agents_down", f"개척지 봇 서비스(mc-agents)가 오늘 {ck['restarts']}번 다시 띄워도 또 멈췄어요 ({why})",
               "etc에서 `docker logs --tail 50 mc-agents` 결과를 개발자에게 전해 주세요")
        return ""
    err = (restart or _restart_agents)()
    ck["restart_at"] = f"{now:%Y-%m-%d %H:%M}"
    ck["restarts"] += 1
    if err:
        _alert(st, now, post, "agents_restart", f"봇 서비스(mc-agents)를 다시 띄우지 못했어요: {err[:120]}",
               "etc에서 `cd /mnt/discord_bot_v2/scripts/minecraft && docker compose up -d mc-agents`")
        return ""
    line = f"개척지 작업장(봇 서비스)이 멈춰 있어 다시 열었다 ({why})"
    post(_steward(), f"-# {NAME} · {now:%H:%M} · 자가 점검\n🔧 {line}")
    return line


REMEDIES = [   # (교훈에 들어 있는 말, 쉬게 할 일, 다시 둘러보기, 이야기)
    ("밭에 밀이 한 포기도", "", True, "밭이 사라졌다 - 개척지를 다시 둘러보고 밭을 다시 일구게 한다"),
    ("짓기 실패", "build", True, "짓기가 자꾸 실패한다 - 한동안 짓기를 쉬고 개척지를 다시 둘러본다"),
    ("나무 베기: 빈손", "gather", False, "숲에서 나무를 못 찾는다 - 한동안 돌을 캐게 한다"),
    ("돌 캐기: 빈손", "mine", False, "근처에 돌이 없다 - 한동안 나무를 베게 한다"),
    ("광석 캐기", "ore", False, "광석 캐기가 안 풀린다 - 한동안 쉬게 한다"),
    ("나무 베기 실패", "gather", False, "나무 베기가 자꾸 실패한다 - 한동안 다른 일을 맡긴다"),
    ("돌 캐기 실패", "mine", False, "돌 캐기가 자꾸 실패한다 - 한동안 다른 일을 맡긴다"),
]


def checkup(now: datetime | None = None, api: Api | None = None, post=None, restart=None) -> dict:
    """[매시] 자가 점검 - 스스로 고칠 수 있는 건 고치고 못 고치는 것만 알린다. 반환 {"actions": [...], "alerts": [...]}"""
    if not ENABLED:
        return {}
    from . import agents as ag
    now = now or now_kst()
    api = api or Api()
    st = _load()
    if post is None:
        def post(n, t):
            ag._post_as(n, t)
    ck = st.setdefault("check", {})
    acts: list[str] = []
    before = dict(st.get("alerts") or {})
    try:
        health = api.get("/health")
    except Exception as e:  # noqa: BLE001
        health = None
        line = heal_agents(st, now, post, restart, f"응답 없음: {str(e)[:60]}")
        if line:
            acts.append(line)
    if health:
        started = health.get("started")
        if ck.get("started") and started and started != ck["started"]:
            ck["crashes"] = (ck.get("crashes", 0) + 1) if ck.get("crash_day") == f"{now:%Y-%m-%d}" else 1
            ck["crash_day"] = f"{now:%Y-%m-%d}"
            acts.append(f"봇 서비스가 지난 점검 뒤 다시 시작됐다 (오늘 {ck['crashes']}번, 메모리 {health.get('heapMB', '?')}MB)")
            if ck["crashes"] >= 3:
                _alert(st, now, post, "agents_crash", f"봇 서비스(mc-agents)가 오늘 {ck['crashes']}번 죽었다 살아났어요",
                       "etc에서 `docker logs --tail 80 mc-agents | grep -iE 'error|heap'` 결과를 개발자에게 전해 주세요")
        ck["started"] = started
        rc = health.get("rcon") or {}
        if rc.get("ok") is False and "비밀번호" in str(rc.get("error", "")):
            _alert(st, now, post, "rcon_pass", "봇 서비스가 서버 명령(RCON) 비밀번호가 틀렸다고 해요 - 짓기·밭·도구가 안 돼요",
                   "etc에서 `docker exec mc-village printenv RCON_PASSWORD` 값으로 "
                   "`cd /mnt/discord_bot_v2/scripts/minecraft && MC_RCON_PASSWORD=그값 docker compose up -d mc-agents`")
        elif rc.get("ok") is False:
            acts.append(f"서버 명령(RCON)이 잠시 끊겼다 ({rc.get('error', '')[:40]}) - 다시 붙는 중")
    # 진척
    metric = progress(st)
    if metric != ck.get("metric") or not ck.get("metric_at"):
        ck.update(metric=metric, metric_at=f"{now:%Y-%m-%d %H:%M}")
    try:
        stalled_h = (now - datetime.strptime(ck["metric_at"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except ValueError:
        stalled_h = 0
    resting = str(st.get("why", "")).startswith("쉬는 중")
    if stalled_h >= 1 and not resting and health:
        avoid = st.setdefault("avoid", {})
        until = f"{datetime.fromtimestamp(now.timestamp() + AVOID_MIN * 60):%Y-%m-%d %H:%M}"
        for x in (st.get("lessons") or [])[::-1]:
            if x["n"] < 3:
                continue
            hit = next((r for r in REMEDIES if r[0] in x["text"]), None)
            if not hit or x.get("healed"):
                continue
            key, task, reaudit, story = hit
            if task:
                avoid[task] = until
            if reaudit:
                st.pop("audited", None)
            x["healed"] = f"{now:%Y-%m-%d %H:%M}"
            acts.append(story)
            break
        if stalled_h >= STALL_ALERT_H:
            _alert(st, now, post, "stalled",
                   f"개척지 장부가 {int(stalled_h)}시간째 그대로예요 (접속 {len((health or {}).get('bots') or [])}명, "
                   f"최근 교훈: {((st.get('lessons') or [{}])[-1]).get('text', '없음')[:60]})",
                   "etc에서 `docker logs --since 1h mc-agents | tail -40` 결과를 개발자에게 전해 주세요")
    for a in acts:
        post(_steward(), f"-# {NAME} · {now:%H:%M} · 자가 점검\n🔧 {a}")
    # 하루 보고
    today = f"{now:%Y-%m-%d}"
    if now.hour >= REPORT_HOUR and ck.get("report_day") != today:
        snap = ck.get("report_snap") or {}
        made, built = st.get("made") or {}, st.get("built") or {}
        grew = [f"{RESOURCES[k]} +{int(v) - int(snap.get('made', {}).get(k, 0))}" for k, v in made.items()
                if k in RESOURCES and int(v) > int(snap.get("made", {}).get(k, 0))]
        newb = [f"{BLUEPRINTS[b][0]} +{n - int(snap.get('built', {}).get(b, 0))}" for b, n in built.items()
                if b in BLUEPRINTS and n > int(snap.get("built", {}).get(b, 0))]
        probs = problems(st)
        post(_steward(), f"-# {NAME} · 개척 {day_no(st, now)}일째 보고\n📋 **{ERAS[era(st)][0]}** · 다음 시대까지: "
             + (", ".join(unmet(st)) or "없음") + "\n오늘 늘어난 것: " + (", ".join(grew + newb) or "없음")
             + "\n막힌 것: " + (" / ".join(probs[:3]) or "없음")
             + (f"\n오늘 사람에게 알린 것: {len([k for k, v in (st.get('alerts') or {}).items() if v.startswith(today)])}건"))
        ck.update(report_day=today, report_snap={"made": dict(made), "built": dict(built)})
    _save(st)
    alerts = [k for k, v in (st.get("alerts") or {}).items() if before.get(k) != v]
    print(f"[개척지] 자가 점검 · 조치 {len(acts)} · 알림 {alerts or '없음'} · 멈춤 {stalled_h:.1f}시간")
    return {"actions": acts, "alerts": alerts, "stalled_h": round(stalled_h, 1)}


# ===================================================================== 한 바퀴
def _note(st: dict, why: str) -> None:
    """쉬거나 못 붙는 이유가 바뀔 때만 로그 한 줄 (매번 찍지 않게)"""
    if why != st.get("why", ""):
        print(f"[개척지] {why or '다시 움직임'}")
        st["why"] = why
        _save(st)


def tick(now: datetime | None = None, api: Api | None = None, llm=None, post=None, agents: list[dict] | None = None,
         force: bool = False, react=None) -> dict:
    """[봇, TICK_MIN분마다, 스레드에서] 반환 {"busy"|"down"|"assigned": ...}"""
    if not ENABLED:
        return {}
    from . import agents as ag, chronicle, memory
    now = now or now_kst()
    api = api or Api()
    st = _load()
    try:
        health = api.get("/health")
    except Exception as e:  # noqa: BLE001
        _note(st, f"봇 서비스(mc-agents)에 연결 못 함: {str(e)[:100]}")
        st["down_ticks"] = st.get("down_ticks", 0) + 1
        if st["down_ticks"] >= 2:
            heal_agents(st, now, post or (lambda n, t: ag._post_as(n, t)), None, f"{st['down_ticks']}바퀴째 응답 없음")
        _save(st)
        return {"down": str(e)[:120]}
    st["down_ticks"] = 0
    center = health.get("center")
    if center and center != st.get("center"):
        # 마을 중심이 정해졌거나 바뀜 (봇 서비스가 월드 스폰을 마을 중심으로 쓴다). 예전 중심(0,0)은 스폰에서 멀어 청크가
        # 불러와지지 않아 아무것도 실제로 지어지지 않았다 - 장부의 건물은 비우고 새 자리에서 다시 시작한다 (자원은 그대로)
        if st.get("built") or st.get("center"):
            old = ", ".join(f"{BLUEPRINTS[b][0]} {n}" for b, n in (st.get("built") or {}).items() if b in BLUEPRINTS)
            st["built"], st["slots"], st["pending"] = {}, {}, {}
            st["audited"] = f"{now:%Y-%m-%d %H:%M}"
            line = history(st, now, f"개척지 자리를 월드 스폰 ({center['x']}, {center['z']}) 둘레로 옮겼다"
                           + (f" - 예전 자리는 너무 멀어 장부의 건물({old})이 실제로는 서지 못했다" if old else ""))
            post_later = line
        else:
            post_later = None
        st["center"] = center
        _save(st)
    else:
        post_later = None
    agents = agents if agents is not None else ag.load_agents()
    by_name = {a["name"]: a for a in agents}
    by_mc = {mc_name(a["name"]): a["name"] for a in agents}
    if post is None:
        def post(name, text):
            (ag._post(by_name[name], text) if name in by_name else ag._post_as(name, text))
    if post_later:
        post(NAME, f"-# {NAME} · {now:%H:%M}\n{post_later}")
    try:
        evs = api.get(f"/events?after={st.get('last_event', 0)}").get("events") or []
    except Exception:  # noqa: BLE001
        evs = []
    builders = []
    for who, line in apply_events(st, evs, by_mc, now):
        post(who, f"-# {NAME} · {now:%H:%M}\n{line}")
        if who == NAME:               # 연대기 줄
            continue
        _remember_line(st, who, line, now)
        try:
            memory.remember(who, f"숲 너머 개척지에서 {line}", 4, [], "개척지")
            if "지었다" in line:
                chronicle.record_event("개척지", f"{who}: {line}", line, [who])
                builders.append(who)
        except Exception as e:  # noqa: BLE001
            print(f"[개척지] 기록 실패: {e}")
    new_era = advance(st)
    if new_era:
        history(st, now, f"개척지가 '{new_era}' 시대로 올라섰다")
        _save(st)
        post(NAME, f"-# {NAME} · {now:%H:%M}\n🎉 **개척지가 '{new_era}' 시대로 올라섰다!** (개척 {day_no(st, now)}일째) "
             "다음 목표: " + (", ".join(unmet(st)) or "없음"))
        try:
            chronicle.record_event("개척지", f"새 시대: {new_era}", ledger(st), builders)
        except Exception as e:  # noqa: BLE001
            print(f"[개척지] 기록 실패: {e}")
        _react(f"개척지가 '{new_era}' 시대로 올라섰다", "새 시대", (builders + list(st.get("recent") or {}))[-3:], react, st)
    elif builders:
        _react(f"{builders[-1]}이(가) 개척지에 새 건물을 세웠다", "새 건물", [builders[-1], "리그루드"], react, st)
    online = list(health.get("bots") or [])
    hunger = eat(st, len(online), now)       # 지난 바퀴 동안 일한 사람들이 먹은 식량
    if hunger:
        post(NAME, f"-# {NAME} · {now:%H:%M}\n{hunger}")
    why = None if force else busy()      # force: 관리자 시험 (/포링푸드마크 지금)
    _note(st, f"쉬는 중 - {why}" if why else "")
    if why:
        if online:
            api.post("/leave", {"name": "*"})
            post(NAME, f"-# {NAME} · {now:%H:%M}\n{why} - 개척지에 나가 있던 사람들이 일을 멈추고 마을로 돌아왔다")
            back = [by_mc.get(n, n) for n in online]
            from . import stewards
            _react(f"{why} - 개척지에서 일하던 {', '.join(back)}이(가) 일을 멈추고 돌아왔다", "개척지 철수",
                   [stewards.steward("loadshed")] + back[:2], react, st)
            st["left_at"] = f"{now:%Y-%m-%d %H:%M}"
        st["online"] = []
        _save(st)
        print(f"[개척지] 바퀴: 쉬는 중 - {why}")
        return {"busy": why}
    if not online and not force and st.get("left_at"):
        try:
            rest = REJOIN_MIN - (now - datetime.strptime(st["left_at"], "%Y-%m-%d %H:%M")).total_seconds() / 60
        except ValueError:
            rest = 0
        if rest > 0:
            _note(st, f"쉬는 중 - 철수 뒤 숨 고르기 ({int(rest) + 1}분 남음)")
            print(f"[개척지] 바퀴: 철수 뒤 숨 고르기 ({int(rest) + 1}분 남음)")
            return {"busy": "철수 뒤 숨 고르기"}
    lite = not force and light()
    try:
        day = api.get("/state").get("day") if online else None
    except Exception:  # noqa: BLE001
        day = None
    hist_before = len(st.get("history") or [])
    chosen = pick(agents, online, day, k=1 if lite else MAX_BOTS, st=st)
    if lite and (not st.get("lite")):
        post(NAME, f"-# {NAME} · {now:%H:%M}\n결계가 높아 개척지는 한 사람만 남아 마을 둘레 일(밭, 짓기, 가까운 나무·돌, 순찰)만 한다")
    st["lite"] = lite
    want = {mc_name(a["name"]) for a in chosen}
    for n in online:
        if n not in want:
            api.post("/leave", {"name": n})
    for n in want - set(online):
        api.post("/join", {"name": n})
    try:
        working = {b["name"] for b in (api.get("/state").get("bots") or []) if b.get("task")}
    except Exception:  # noqa: BLE001
        working = set()
    idle = [a for a in chosen if mc_name(a["name"]) not in working]
    plan = decide(idle, st, day, llm, now)
    for h in (st.get("history") or [])[hist_before:]:     # 새로 정해진 직업 (연대기)
        if "되었다" in h["text"]:
            post(NAME, f"-# {NAME} · {now:%H:%M}\n📜 {h['text']}")
    try:
        api.post("/rations", {"ok": (st.get("res") or {}).get("food", START_FOOD) >= MEAL})   # 창고가 비면 배급 끊김
    except Exception:  # noqa: BLE001
        pass
    if plan and not st.get("audited") and (st.get("built") or {}):
        # 한 번: 장부의 건물이 실제로 서 있는지 둘러보기 (명령이 RCON으로 바뀌기 전엔 권한이 없어 장부에만 지어진 건물이 있다)
        t = plan[-1]
        t.update(task="audit", blueprint="", say=t.get("say") or "장부랑 실제가 맞나 한 바퀴 돌아볼게")
    if lite:                               # 멀리 나가는 일 대신 마을 안 일 (광석은 돌로, 나무·돌은 near)
        for t in plan:
            if t["task"] not in LIGHT_TASKS:
                t.update(task="mine", blueprint="")
    slots = st.setdefault("slots", {})
    pend = st.setdefault("pending", {})
    res = st.setdefault("res", {})
    for t in plan:
        mc = mc_name(t["name"])
        args = {}
        if t["task"] in ("gather", "mine", "ore"):
            # repeat: 다음 바퀴까지 이어서 (끝날 때마다 done이 와서 창고에 쌓인다) - 10분 중 놀던 시간을 없앤다
            args = {"count": SPECIALIST_COUNT if role(st, t["name"]) == t["task"] else 12, "repeat": True}
            if lite:
                args["near"] = True                                  # 결계가 높으면 마을 둘레에서만
            if t["task"] == "ore":
                args["count"] = max(4, args["count"] // 3)          # 광석은 드물다
        elif t["task"] == "farm":
            args = {"plots": min(8, max(slots.get("farm", 0), (st.get("built") or {}).get("farm", 0))),
                    "grow": grow_stages(st, now), "repeat": True, "grow_min": GROW_MIN}
            st["farm_at"] = f"{now:%Y-%m-%d %H:%M}"
        elif t["task"] == "build":
            cost = BLUEPRINTS[t["blueprint"]][1]
            for k, v in cost.items():
                res[k] = res.get(k, 0) - v
            args = {"blueprint": t["blueprint"], "slot": slots.get(t["blueprint"], 0)}
            slots[t["blueprint"]] = slots.get(t["blueprint"], 0) + 1
            pend[mc] = {"task": "build", "blueprint": t["blueprint"], "cost": cost}
        elif t["task"] == "patrol":
            args = {"seconds": TICK_MIN * 60 - 30}
        elif t["task"] == "audit":
            args = {"slots": {bp: max(n, slots.get(bp, 0)) for bp, n in (st.get("built") or {}).items()}}
        sent = api.post("/task", {"name": mc, "kind": t["task"], "args": args, "say": t["say"]})
        if sent.get("error"):                  # 못 맡겼으면 예약한 재료/자리를 되돌리고 다음 바퀴에
            if t["task"] == "build":
                for k, v in BLUEPRINTS[t["blueprint"]][1].items():
                    res[k] = res.get(k, 0) + v
                slots[t["blueprint"]] = max(0, slots.get(t["blueprint"], 1) - 1)
                pend.pop(mc, None)
            t["task"] = "-"
            continue
        what = (f"{JOBS[t['job']]} · " if t.get("job") in JOBS else "") + TASKS[t["task"]] + (
            f" ({BLUEPRINTS[t['blueprint']][0]})" if t["blueprint"] else "")
        post(t["name"], f"-# {NAME} · {now:%H:%M} · {what}\n{t['say'] or '…'}")
        _remember_line(st, t["name"], f"{what} 하러 감", now)
    st["online"] = sorted(want)
    _save(st)
    print(f"[개척지] 접속 {', '.join(sorted(want)) or '없음'} · 일 {[(t['name'], t['task']) for t in plan]}")
    return {"assigned": plan}


def surge_raid(surge: dict, api: Api | None = None, post=None) -> dict | None:
    """[minecraft 모드] 몬스터 불시 습격 = 개척지 둘레에 실제 몬스터. 개척지에 아무도 없으면 None (raids 이야기로)"""
    if not ENABLED:
        return None
    from . import agents as ag
    api = api or Api()
    st = _load()
    if not st.get("online"):
        return None
    n = max(3, min(10, int(surge.get("rise", 20) // 8) + 2 * int(surge.get("playing") or 1)))
    mob = "husk" if surge.get("peak", 0) >= 90 else "zombie"
    try:
        api.post("/raid", {"count": n, "mob": mob})
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 습격 소환 실패: {e}")
        return None
    text = (f"-# {NAME} · {now_kst():%H:%M} · ⚔️ 불시 습격\n환영극장에 손님이 몰려 결계 부하가 {surge.get('base', 0):g}%→"
            f"{surge.get('peak', 0):g}%로 치솟자, 얇아진 결계를 뚫고 개척지 둘레에 몬스터 {n}마리가 나타났다!")
    (post or (lambda t: ag._post_as(NAME, t)))(text)
    st.setdefault("raids", 0)
    st["raids"] += 1
    _remember_line(st, "개척지", f"몬스터 {n}마리 습격", now_kst())
    _save(st)
    return {"count": n, "mob": mob}


def summary() -> str:
    st = _load()
    roles = ", ".join(f"{n}({JOBS.get(r) or ROLES[r]})"
                      for n in dict.fromkeys(list(st.get("jobs") or {}) + list(st.get("skill") or {})) if (r := role(st, n)))
    pl = st.get("plan") if (st.get("plan") or {}).get("focus") in FOCUS else None
    return (f"{NAME}: {ledger(st)} · 쓰러짐 {st.get('deaths', 0)} · "
            f"지금 접속 {', '.join(st.get('online') or []) or '없음'}"
            + (f" · {st['why']}" if st.get("why") else "")
            + "\n지금 문제: " + (" / ".join(problems(st)) or "없음")
            + (f"\n이번 목표: {FOCUS[pl['focus']]} +{pl['target']} (지금 +{focus_metric(st, pl['focus']) - pl['base']})"
               if pl else "")
            + (f"\n직업: {roles}" if roles else "")
            + (f"\n목표 성적: 달성 {st['plans']['ok']} / 미달 {st['plans']['miss']}" if st.get("plans") else "")
            + "\n교훈: " + (" / ".join(f"{x['text']}({x['n']})" for x in (st.get("lessons") or [])[-3:]) or "없음")
            + "\n연대기:\n" + ("\n".join(h["text"] for h in (st.get("history") or [])[-5:]) or "아직 없음"))
