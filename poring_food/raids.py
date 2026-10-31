"""
몬스터 침입 - 예기치 않은 습격. 환영극(Plex/YM_PLAYER 영상 재생)이 상영되는 중에 결계 부하(CPU)가 갑자기 치솟으면,
결계 마나가 극장으로 쏠려 성벽이 얇아진 틈을 타 검은 숲의 몬스터가 불시에 덮친다.

- 언제 (PORING_RAID_MODE=surge, 기본): 결계 조절(loadshed)이 매분 잰 부하 지수로 판단한다 - 지금 재생 중이고,
  최근 2분 평균이 SURGE_MIN(기본 70%) 이상이며 5~15분 전 평균보다 SURGE_RISE(기본 20%p) 넘게 뛰었으면 습격.
  습격 뒤 RAID_COOLDOWN_MIN(기본 180분)은 다시 오지 않고, 하루 최대 RAID_DAILY_MAX(기본 3)번. 봇 load_guard가 매분 확인.
  PORING_RAID_MODE=nightly면 예전처럼 매일 밤 RAID_HOUR 회차에 한 번 (agents.tick).
- 위험도(0~100, danger): 실제 수치로 정한다 - 지어내지 않는다. 습격이면 치솟은 지수/오른 폭/상영 중인 수가 크게 더한다.
  · 결계 부하 = 서버 CPU % (signals facility_numbers "cpu"와 loadshed가 잰 plex01 중 높은 쪽)
  · 결계 균열 = 멈춘 서비스 수("down") / 최근 재가동(가동 1일 미만)
  · 불 꺼진 감시탑 = facilities.py가 기록한 멈춘 컨테이너 수
  · 날씨 = 비/눈(발소리가 묻힌다), 한파 - 기상청 실측
  · 그날의 운 = 날짜로 정해진 0~25 (같은 날은 몇 번 계산해도 같다)
  매일 밤 침입한다 (PORING_RAID_THRESHOLD를 올리면 그 위험도 미만인 밤은 "조용한 밤" 한 줄 - 기본 0 = 매일).
- 몬스터: 위험도 구간별 (포링 떼 ~ 와이번), 전력 = (기본 + 그날의 운) x 마을 평균 레벨만큼 조금 더 (MONSTER_SCALE).
- 방어력 = 방어 시설(decor 판의 시설 x 상태) + 수비대(싸울 수 있는 사람 - 자는 사람은 종을 듣고 늦게 와서 절반, 체력만큼,
  전투 레벨만큼 - levels.py) + 돕는 사람(싸움꾼이 아니어도 깨어 있으면 화살/횃불/부상자 나르기 SUPPORT) + 민병 기본
  + 사기(지난 하루 환영극/도서관 방문객 알림 수).
- 경험치/레벨업 (levels.py): 싸운 사람/돕는 사람/치유사가 경험치를 얻고, 레벨이 오르면 채널 한 줄 + 기억/연대기.
- 결과 = 방어력 / 전력: 대승(>=1.3) / 격퇴(>=0.9) / 고전(>=0.6) / 성벽 돌파.
  · 시설이 부서진다(decor.damage), 전리품(몬스터 마석)은 방어 기금으로(decor.add_fund)
  · 싸운 사람은 체력이 줄고(이기면 자신감), 고전/돌파면 다친 사람이 나온다 (치유사 아쿠아가 기억한다)
  · 기억/연대기/마을 사건(받은편지함)으로 남고, 채널에 "⚔️ 몬스터 침입" 이야기(LLM 1회, 실패하면 정해진 글)
- 인물들은 판단 때 [오늘 밤 숲의 기척](예보 - 낮부터 볼 수 있어서 방어 시설을 손볼 수 있다)과 [지난밤 침입]을 본다.
- 관리자: /포링푸드침입 으로 지금 예보와 최근 기록 (지금 바로 침입시키기도 가능).
저장: storage/poring_food/raids.json {"days": {날짜: 기록}}
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .josa import j

ENABLED = os.getenv("PORING_RAIDS", "1") not in ("0", "false", "False", "")
RAID_HOUR = int(os.getenv("PORING_RAID_HOUR", "22"))
MODE = os.getenv("PORING_RAID_MODE", "surge").strip().lower()      # surge(재생 중 부하 급상승 = 불시 습격) / nightly
SURGE_MIN = float(os.getenv("PORING_RAID_SURGE_MIN", "70"))        # 최근 2분 평균 지수가 이 이상이고
SURGE_RISE = float(os.getenv("PORING_RAID_SURGE_RISE", "20"))      # 5~15분 전 평균보다 이만큼(%p) 넘게 뛰면
RAID_COOLDOWN_MIN = int(os.getenv("PORING_RAID_COOLDOWN_MIN", "180"))
RAID_DAILY_MAX = int(os.getenv("PORING_RAID_DAILY_MAX", "3"))
THRESHOLD = int(os.getenv("PORING_RAID_THRESHOLD", "0"))   # 0 = 매일 밤 침입
RAIDS_PATH = os.path.join(STATE_DIR, "raids.json")
KEEP_DAYS = 60
NAME = "⚔️ 몬스터 침입"

# (최소 위험도, 몬스터, 기본 전력)
MONSTERS = [(0, "포링 떼", 55), (35, "고블린 무리", 75), (50, "늑대 떼", 92), (65, "오크 전사단", 110),
            (78, "트롤", 130), (90, "와이번", 155)]
MONSTER_LUCK = 30      # 전력 = 기본 + 0~MONSTER_LUCK
MONSTER_SCALE = 0.07   # 마을 평균 레벨 1당 몬스터 전력 +7% (사람은 레벨당 +8%라 레벨이 오를수록 조금씩 유리해진다)
SUPPORT = 1            # 싸움꾼이 아닌 깨어 있는 사람의 힘 (돕기)
# 방어 시설 하나의 방어력 (상태 100일 때)
STRUCTURE_POWER = {"목책": 6, "감시탑": 9, "함정": 5, "결계석": 12, "마법등": 3, "횃불": 2, "투석기": 10,
                   "룬 벽화": 8, "약초밭": 2, "가시덤불": 4}
STRUCTURE_CAP = 45
# 싸울 수 있는 사람의 전력 (agents.json 이름). 없는 사람은 직업으로 짐작 (FIGHT_WORDS).
DEFENDERS = {"베니마루": 9, "카즈마": 6, "슈나": 6, "소우에이": 5, "아이젠": 5, "애순이": 4, "위벨": 4, "슈타르크": 4,
             "가비루": 4, "바닐": 3, "일레이나": 3, "카이진": 3, "쿠로베": 2, "소라": 2, "아쿠아": 2, "페른": 2,
             "라오프": 2, "자인": 2}
FIGHT_WORDS = ("기사", "궁수", "검사", "민병", "정찰", "레인저", "용병")   # 명단에 없는 사람은 이런 직업이면 2
MILITIA = 10
MORALE_CAP = 10
HEALER = "아쿠아"
# 결과: (최소 비율, 이름, 부서지는 시설 수 범위, 상태 감소, 전리품, 다치는 사람 수)
OUTCOMES = [(1.3, "대승", (0, 1), 10, 60000, 0), (0.9, "격퇴", (1, 2), 25, 30000, 0),
            (0.6, "고전", (2, 3), 40, 10000, 1), (0.0, "성벽 돌파", (3, 5), 70, 0, 2)]


def _load() -> dict:
    try:
        with open(RAIDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(RAIDS_PATH), exist_ok=True)
    tmp = RAIDS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RAIDS_PATH)


# ===================================================================== 위험도
def gather(now: datetime | None = None) -> dict:
    """위험도 계산에 쓰는 실제 수치를 모은다 (못 받은 건 빠진다)."""
    from . import facilities, kpi, loadshed, signals
    now = now or now_kst()
    fac = dict(signals.live_value("facility_numbers") or {})
    both = loadshed.max_cpu(now)    # 봇 서버(etc)와 Plex 서버(plex01) 중 높은 쪽
    if both is not None and (fac.get("cpu") is None or both > fac["cpu"]):
        fac["cpu"] = both
    towers = len([n for n in (facilities._load().get("down_since") or {}) if n in facilities.FACILITIES])
    return {"cpu": fac.get("cpu"), "down": int(fac.get("down") or 0), "uptime_days": fac.get("uptime_days"),
            "towers_down": towers, "weather": kpi._weather_kind(signals.weather_text()), "date": now.strftime("%Y-%m-%d")}


def danger(x: dict) -> tuple[int, list[str]]:
    """위험도(0~100)와 이유들. 순수 함수 (테스트용)."""
    score, why = 20, []
    cpu = x.get("cpu")
    if isinstance(cpu, (int, float)):
        add = int(cpu * 0.35)
        score += add
        if cpu >= 60:
            why.append(f"결계 부하 {cpu:g}%")
    if x.get("down"):
        score += 10 * int(x["down"])
        why.append(f"결계에 금 간 곳 {x['down']}개")
    days = x.get("uptime_days")
    if isinstance(days, (int, float)) and days < 1:
        score += 12
        why.append("결계가 최근 끊겼다 다시 세워짐")
    if x.get("towers_down"):
        score += 8 * int(x["towers_down"])
        why.append(f"불 꺼진 감시탑 {x['towers_down']}곳")
    w = x.get("weather") or ""
    if w in ("rain", "snow"):
        score += 10
        why.append("비/눈에 발소리가 묻힘" if w == "rain" else "눈 덮인 숲에서 굶주린 짐승들")
    elif w == "cold":
        score += 6
        why.append("한파에 먹이가 없는 숲")
    sg = x.get("surge")
    if sg:
        score += int(max(0.0, sg["peak"] - 60) * 0.6 + sg["rise"] * 0.5) + 5 * min(int(sg.get("playing") or 0), 4)
        why.insert(0, f"환영극 {sg.get('playing')}편 상영 중 결계 부하 {sg['base']:g}%→{sg['peak']:g}% 급등 - 마나가 극장으로 쏠려 성벽이 얇아짐")
    luck = random.Random(f"raid|{x.get('date', '')}").randint(0, 10 if sg else 25)
    score += luck
    if luck >= 20:
        why.append("숲이 유난히 술렁임")
    return max(0, min(100, score)), why


def level_word(score: int) -> str:
    return "매우 위험" if score >= 80 else "위험" if score >= 60 else "불안" if score >= 40 else "잠잠"


def monster_for(score: int, rnd: random.Random, avg_level: float = 1.0) -> tuple[str, int]:
    name, base = next((m, b) for s, m, b in reversed(MONSTERS) if score >= s)
    return name, int((base + rnd.randint(0, MONSTER_LUCK)) * (1 + MONSTER_SCALE * max(0.0, avg_level - 1)))


def tier(monster: str) -> int:
    return next((i for i, (_, m, _) in enumerate(MONSTERS) if m == monster), 0)


# ===================================================================== 방어력
def structures_power(items: list[dict]) -> int:
    return min(STRUCTURE_CAP, int(sum(STRUCTURE_POWER.get(i.get("item"), 0) * max(0, i.get("cond", 0)) / 100
                                      for i in items)))


def defender_power(agent: dict, roster_job: str = "") -> int:
    if agent["name"] in DEFENDERS:
        return DEFENDERS[agent["name"]]
    return 2 if any(w in roster_job for w in FIGHT_WORDS) else 0


def defenders(agents: list[dict], roster: list[dict], hour: int, energy_of) -> list[dict]:
    """성벽에 나온 사람 [{"name", "power"(실제 낸 힘), "asleep", "support"}]. 싸움꾼은 자다가도 나오고(절반),
    싸움꾼이 아닌 사람은 깨어 있을 때만 돕는다 (SUPPORT). 레벨만큼 힘이 커진다."""
    from . import levels
    from .agents import _asleep
    jobs = {r["name"]: f"{r.get('job', '')} {r.get('rank', '')}" for r in roster}
    out = []
    for a in agents:
        base = defender_power(a, jobs.get(a["name"], ""))
        asleep = _asleep(a, hour)
        support = not base
        if support:
            if asleep:
                continue
            base = SUPPORT
        e = float(energy_of(a["name"]))
        power = base * (0.5 if asleep else 1.0) * (0.5 + max(0.0, min(1.0, e)) / 2) * levels.multiplier(a["name"])
        out.append({"name": a["name"], "power": round(power, 1), "asleep": asleep, "support": support})
    return sorted(out, key=lambda d: -d["power"])


def morale(now: datetime) -> int:
    """지난 하루 환영극/도서관 방문객 알림 수 = 마을 사기 (최대 MORALE_CAP)."""
    from . import book_watch, plex_watch
    since = (now - timedelta(days=1)).isoformat()
    plex = sum(1 for a in plex_watch._load().get("announced") or [] if a.get("at", "") >= since)
    book = sum(1 for a in book_watch._load().get("announced") or [] if a.get("at", "") >= since and not a.get("adult"))
    return min(MORALE_CAP, plex * 2 + book)


def outcome_for(ratio: float) -> tuple:
    return next(o for o in OUTCOMES if ratio >= o[0])


# ===================================================================== 예보 / 기록
def surge(samples: list, playing: int | None) -> dict | None:
    """재생 중 부하 급상승인지 (순수 - 테스트용). samples = loadshed 매분 기록 [[시각, 지수, psi], ...] (오래된 것부터).
    반환 {"peak", "base", "rise", "playing"} 또는 None."""
    if not playing:
        return None
    vals = [float(x[1]) for x in samples if len(x) >= 2 and isinstance(x[1], (int, float))]
    if len(vals) < 7:
        return None
    cur = sum(vals[-2:]) / 2
    before = vals[-15:-5] if len(vals) >= 15 else vals[:-5]
    base = sum(before) / len(before)
    rise = cur - base
    if cur >= SURGE_MIN and rise >= SURGE_RISE:
        return {"peak": round(cur, 1), "base": round(base, 1), "rise": round(rise, 1), "playing": int(playing)}
    return None


def surge_due(now: datetime | None = None, data: dict | None = None) -> dict | None:
    """[매분, load_guard] 지금 불시 습격이 와야 하면 그 급등 정보 (쿨다운/하루 최대 안에서). 가장 크게 뛴 서버 기준."""
    if not ENABLED or MODE != "surge":
        return None
    now = now or now_kst()
    days = _load().get("days") or {}
    today = [r for d, r in days.items() if d.startswith(now.strftime("%Y-%m-%d")) and r.get("raid")]
    if len(today) >= RAID_DAILY_MAX:
        return None
    last_at = max((r.get("at", "") for r in days.values() if r.get("raid")), default="")
    if last_at and last_at > (now - timedelta(minutes=RAID_COOLDOWN_MIN)).isoformat(timespec="minutes"):
        return None
    from . import loadshed
    data = data if data is not None else loadshed._load()
    hosts = data.get("hosts") or {}
    playing = (hosts.get("plex01") or {}).get("playing")
    best = None
    for host, st in hosts.items():
        got = surge(st.get("samples") or [], playing)
        if got and (best is None or got["rise"] > best["rise"]):
            best = dict(got, host=host)
    return best


def forecast(now: datetime | None = None, x: dict | None = None) -> dict:
    now = now or now_kst()
    score, why = danger(x if x is not None else gather(now))
    return {"score": score, "why": why, "level": level_word(score)}


def last(now: datetime | None = None, hours: int = 36) -> dict | None:
    now = now or now_kst()
    since = (now - timedelta(hours=hours)).isoformat()
    recs = [r for r in (_load().get("days") or {}).values() if r.get("at", "") >= since]
    return max(recs, key=lambda r: r["at"]) if recs else None


def done_today(now: datetime) -> bool:
    return now.strftime("%Y-%m-%d") in (_load().get("days") or {})


def should_run(now: datetime) -> bool:
    """매일 밤 회차에 침입 (nightly 모드만 - surge 모드는 load_guard가 surge_due로)"""
    return ENABLED and MODE == "nightly" and now.hour == RAID_HOUR and not done_today(now)


def prompt_block(now: datetime | None = None) -> str:
    """판단용: 오늘 밤 예보(낮 12시부터, 아직 안 왔으면) + 지난밤 침입 결과 (성벽 일이라 모두가 안다)."""
    if not ENABLED:
        return ""
    now = now or now_kst()
    parts = []
    r = last(now)
    if r:
        if r.get("raid"):
            parts.append(f"[최근 몬스터 {'불시 습격' if r.get('surge') else '침입'} - {r['at'][5:16].replace('T', ' ')}] "
                         f"{r['monster']} → {r['outcome']}. {r.get('summary', '')}"
                         + (f" 다친 사람: {', '.join(r['injured'])}." if r.get("injured") else ""))
        else:
            parts.append(f"[지난밤] 조용한 밤이었다 (숲의 기척 {r['level']}).")
    if MODE == "surge":
        parts.append("[숲의 기척] 환영극이 상영되는 중에 결계 마나가 갑자기 극장으로 쏠리면(부하 급등) 성벽이 얇아져 몬스터가 "
                     "예고 없이 덮친다 - 언제 올지 모른다. 방어 시설을 손보고, 상영 중엔 성벽 근처를 비우지 마라.")
    elif 12 <= now.hour < RAID_HOUR and not done_today(now):
        f = forecast(now)
        mon = next(m for s_, m, _ in reversed(MONSTERS) if f["score"] >= s_)
        parts.append(f"[오늘 밤 숲의 기척 - {f['level']}] " + (", ".join(f["why"]) or "특별한 조짐은 없다")
                     + (f" - 오늘 밤 {mon}급이 올 것 같다 ({RAID_HOUR}시쯤). 방어 시설을 손보거나, 성벽에 설 준비를 하거나, "
                        "일찍 피난할 수 있다." if f["score"] >= THRESHOLD else ""))
    return "\n".join(parts)


# ===================================================================== 침입
def resolve(score: int, why: list[str], items: list[dict], fighters: list[dict], morale_pts: int,
            rnd: random.Random, avg_level: float = 1.0) -> dict:
    """한 밤의 결과를 숫자로만 정한다 (순수 - 테스트용). LLM/저장/알림은 run이."""
    monster, strength = monster_for(score, rnd, avg_level)
    st = structures_power(items)
    fp = int(sum(f["power"] for f in fighters))
    defense = st + fp + MILITIA + morale_pts
    ratio = defense / max(1, strength)
    _, name, (lo, hi), dmg, loot, hurt = outcome_for(ratio)
    front = [f for f in fighters if not f.get("support")] or fighters
    injured = [f["name"] for f in rnd.sample(front, min(hurt, len(front)))] if hurt else []
    return {"monster": monster, "strength": strength, "defense": defense, "structures": st, "fighters": fp,
            "militia": MILITIA, "morale": morale_pts, "ratio": round(ratio, 2), "outcome": name,
            "broken": rnd.randint(lo, hi), "damage": dmg, "loot": loot, "injured": injured,
            "score": score, "why": why}


def _story(res: dict, fighters: list[dict], hit: list[str], now: datetime, ups: list[dict] | None = None) -> str:
    from . import worldcfg
    from .llm import llm_json
    from . import levels
    names = ", ".join(f"{f['name']}({levels.tag(f['name'])})" + (" (자다 깨서 늦게)" if f["asleep"] else "")
                      + (" (돕기)" if f.get("support") else "") for f in fighters[:8]) or "민병대뿐"
    grow = ", ".join(f"{u['name']} Lv.{u['to']}" for u in ups or []) or "없음"
    out = llm_json(
        "너는 마법 판타지 변경 마을 '오아시스 마을'의 밤을 기록하는 음유시인이다. 주어진 사실만으로 짧고 생생하게 쓴다. "
        "죽은 사람은 없다. 지어낸 인물/수치를 넣지 마라.\n" + worldcfg.WORLD_RULE,
        f"[{now:%m/%d %H:%M}] 몬스터 " + ("불시 습격 (예고 없이 - 환영극 상영 중 결계 마나가 극장으로 쏠려 성벽이 얇아진 틈)"
                                          if res.get("surge") else "침입") + "\n"
        f"- 몬스터: {res['monster']} (전력 {res['strength']})\n"
        f"- 조짐: {', '.join(res['why']) or '없음'}\n- 성벽에 선 사람: {names}\n"
        f"- 방어: 시설 {res['structures']} + 수비 {res['fighters']} + 민병 {res['militia']} + 사기 {res['morale']}\n"
        f"- 결과: {res['outcome']}\n- 부서진 시설: {', '.join(hit) or '없음'}\n- 다친 사람: {', '.join(res['injured']) or '없음'}\n"
        f"- 전리품(마석 판 돈): {res['loot']:,}원\n- 이번 싸움으로 레벨이 오른 사람: {grow}\n\n"
        + ("이 불시 습격을 3~5문장으로 써라 - 갑자기 결계가 흔들린 순간과 허둥지둥 성벽으로 달려간 모습으로 시작하고, "
           "누가 무엇을 했는지 1~2명은 이름을 들어서. 상영 중인 환영극(손님)은 끝까지 지켜 냈다. " if res.get("surge") else
           "이 밤의 싸움을 3~5문장으로 써라 (누가 무엇을 했는지 1~2명은 이름을 들어서). ") +
        '반드시 JSON으로만 응답: {"story": "이야기", "summary": "한 줄 요약(40자 이내)"}',
        temperature=0.85, timeout=60, tag="몬스터 침입")
    story = str((out or {}).get("story") or "").strip()
    summ = str((out or {}).get("summary") or "").strip()
    res["summary"] = summ[:80] or f"{res['monster']}의 침입을 {res['outcome']}"
    if story:
        return story[:900]
    lead = fighters[0]["name"] if fighters else "민병대"
    return (f"검은 숲에서 {j(res['monster'], '이')} 성벽으로 몰려왔다. "
            f"{j(lead, '이')} 맨 앞에서 막아섰고, 이 밤 싸움의 결과는 {res['outcome']}."
            + (f" 부서진 것: {', '.join(hit)}." if hit else "")
            + (f" 다친 사람: {', '.join(res['injured'])}." if res["injured"] else ""))


def _award(res: dict, fighters: list[dict], agents: list[dict], now: datetime) -> list[dict]:
    """경험치 나누기 + 레벨업 반영(기억/기분/연대기). 반환: 레벨이 오른 사람들."""
    from . import chronicle, levels, life, memory
    base = levels.XP_BY_OUTCOME.get(res["outcome"], 10) + 3 * tier(res["monster"])
    front = [f for f in fighters if not f.get("support")]
    mvp = front[0]["name"] if front else ""
    res["mvp"] = mvp
    gains = {f["name"]: int(base * (levels.SUPPORT_RATIO if f.get("support") else 1)) + (levels.MVP_XP if f["name"] == mvp else 0)
             for f in fighters}
    if res["injured"] and any(a["name"] == HEALER for a in agents):
        gains[HEALER] = gains.get(HEALER, 0) + levels.HEAL_XP * len(res["injured"])
    ups = levels.gain(gains, battle=True, mvp=mvp, best=res["monster"])
    for u in ups:
        memory.remember(u["name"], f"{now:%m/%d} 밤 {res['monster']}과 싸우고 전투 레벨이 Lv.{u['to']}로 올랐다"
                        + (f" - '{u['title']}' 칭호를 얻었다" if u["title"] else ""), 7 if u["title"] else 5, [], "레벨")
        life.apply_changes(u["name"], {"confidence": 0.1, "happiness": 0.05}, reason=f"전투 레벨 Lv.{u['to']}")
        if u["title"]:
            chronicle.record_event("레벨", f"{u['name']} Lv.{u['to']} {u['title']}", f"{res['monster']} 침입 뒤", [u["name"]])
    return ups


def run(now: datetime | None = None, force: bool = False, inject=None, post=None, surge_info: dict | None = None) -> dict | None:
    """[매일 밤 RAID_HOUR, 잠금 안에서] 예보를 계산하고, 위험하면 침입을 치른다. force=True면 위험도와 상관없이 침입.
    inject(targets, detail, title), post(text) - 기본은 agents.inject_event / agents._post_as. 반환: 기록."""
    from . import agents as ag, characters, chronicle, decor, levels, life, memory
    now = now or now_kst()
    if not ENABLED or (done_today(now) and not force and not surge_info):
        return None
    inject = inject or ag.inject_event
    post = post or (lambda text: ag._post_as(NAME, text))
    x = gather(now)
    if surge_info:
        x["surge"] = surge_info
    score, why = danger(x)
    day = now.strftime("%Y-%m-%d") + (f"#{now:%H%M}" if surge_info and done_today(now) else "")
    data = _load()
    days = data.setdefault("days", {})
    rec = {"at": now.isoformat(timespec="minutes"), "score": score, "level": level_word(score), "why": why,
           "surge": surge_info}
    if score < THRESHOLD and not force and not surge_info:
        rec["raid"] = False
        days[day] = rec
        data["days"] = {d: v for d, v in days.items() if d >= (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")}
        _save(data)
        post(f"-# 🌙 {now:%H:%M} 조용한 밤 · 숲의 기척 {rec['level']}({score}) - 성벽 너머는 잠잠했다")
        print(f"[침입] 조용한 밤 (위험도 {score})")
        return rec
    rnd = random.Random(f"raid|{day}|{now.hour}|{force}")
    agents = ag.load_agents()
    roster = characters.load_roster()
    fighters = defenders(agents, roster, now.hour, lambda n: life.get(n).get("energy", 0.6))
    items = decor._state()["items"]
    avg = levels.average([f["name"] for f in fighters if not f.get("support")])
    res = resolve(score, why, items, fighters, morale(now), rnd, avg)
    hit = decor.damage(res["broken"], res["damage"], rnd, now) if res["broken"] else []
    if res["loot"]:
        decor.add_fund(res["loot"])
    ups = _award(res, fighters, agents, now)
    res["surge"] = surge_info
    story = _story(res, fighters, hit, now, ups)
    win = res["outcome"] in ("대승", "격퇴")
    for f in fighters:
        d = {"energy": -0.12 if win else -0.2, "confidence": 0.1 if win else -0.05, "stress": 0.0 if win else 0.12}
        if f["name"] in res["injured"]:
            d.update({"energy": -0.2, "happiness": -0.1, "stress": 0.15})
        life.apply_changes(f["name"], d, reason=f"몬스터 침입 {res['outcome']}")
        memory.remember(f["name"], f"{now:%m/%d} 밤 {res['monster']} 침입 때 성벽에서 싸웠다 - {res['outcome']}"
                        + (" (다쳤다)" if f["name"] in res["injured"] else ""), 7 if not win else 6, [], "침입")
    if res["injured"] and any(a["name"] == HEALER for a in agents):
        memory.remember(HEALER, f"{now:%m/%d} 밤 침입으로 다친 {j(', '.join(res['injured']), '을')} 치유원에서 돌봤다", 6,
                        res["injured"], "침입")
    title = f"몬스터 침입: {res['monster']} {res['outcome']}"
    detail = (f"{j(res['monster'], '이')} 성벽으로 쳐들어왔다 - {res['outcome']}. "
              + (f"부서진 것: {', '.join(hit)}. " if hit else "") + (f"다친 사람: {', '.join(res['injured'])}. " if res["injured"] else "")
              + (f"마석을 팔아 방어 기금 +{res['loot']:,}원." if res["loot"] else ""))
    try:
        inject([ag.world.TOWN], detail, title)
    except Exception as e:  # noqa: BLE001
        print(f"[침입] 마을 사건 전달 실패: {e}")
    chronicle.record_event("침입", title, detail, [f["name"] for f in fighters])
    icon = {"대승": "🏆", "격퇴": "🛡️", "고전": "🩹", "성벽 돌파": "🔥"}[res["outcome"]]
    post(f"-# ⚔️ {now:%H:%M} 몬스터 {'불시 습격' if surge_info else '침입'} · 숲의 기척 {rec['level']}({score})"
         + (f" · 결계 부하 {surge_info['base']:g}%→{surge_info['peak']:g}% (상영 {surge_info['playing']}편)" if surge_info else "")
         + f"\n{story}\n"
         f"-# {icon} {res['outcome']} · {res['monster']}(전력 {res['strength']}) vs 방어 {res['defense']} "
         f"(시설 {res['structures']} · 수비 {res['fighters']} · 민병 {res['militia']} · 사기 {res['morale']})"
         + (f" · 부서짐 {len(hit)}" if hit else "") + (f" · 다침 {', '.join(res['injured'])}" if res["injured"] else "")
         + (f" · 마석 +{res['loot']:,}원" if res["loot"] else "")
         + ("\n-# 🎉 레벨업: " + ", ".join(f"{u['name']} Lv.{u['to']}" + (f"({u['title']})" if u["title"] else "") for u in ups)
            if ups else ""))
    rec.update({"raid": True, "monster": res["monster"], "strength": res["strength"], "defense": res["defense"],
                "outcome": res["outcome"], "hit": hit, "injured": res["injured"], "loot": res["loot"],
                "fighters": [f["name"] for f in fighters], "summary": res.get("summary", ""),
                "level_ups": [f"{u['name']} Lv.{u['to']}" for u in ups], "mvp": res.get("mvp", "")})
    data = _load()
    days = data.setdefault("days", {})
    days[day] = rec
    data["days"] = {d: v for d, v in days.items() if d >= (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")}
    _save(data)
    print(f"[침입] {'불시 습격 ' if surge_info else ''}{title} (위험도 {score}, 방어 {res['defense']} vs {res['strength']})")
    return rec


def summary(now: datetime | None = None, limit: int = 7) -> str:
    """관리자 조회(/포링푸드침입)용: 지금 예보 + 최근 밤들."""
    now = now or now_kst()
    x = gather(now)
    f = forecast(now, x)
    lines = [f"⚔️ **몬스터 침입** ({now:%m/%d %H:%M})",
             f"오늘 밤 숲의 기척: **{f['level']}** (위험도 {f['score']}, 예상 몬스터 "
             f"{next(m for s_, m, _ in reversed(MONSTERS) if f['score'] >= s_)}) · "
             + (f"불시 습격 (재생 중 부하 {SURGE_MIN:g}% 이상 + {SURGE_RISE:g}%p 급등, {RAID_COOLDOWN_MIN}분 쿨다운, 하루 최대 {RAID_DAILY_MAX})"
                if MODE == "surge" else f"매일 {RAID_HOUR}시"),
             "조짐: " + (", ".join(f["why"]) or "없음")]
    recs = sorted((_load().get("days") or {}).items(), reverse=True)[:limit]
    if recs:
        lines.append("\n**최근 밤**")
        for d, r in recs:
            if r.get("raid"):
                lines.append(f"• {r['at'][5:16].replace('-', '/').replace('T', ' ')}{' 불시' if r.get('surge') else ''} {r['monster']} → **{r['outcome']}** (방어 {r['defense']} vs {r['strength']})"
                             + (f" · 부서짐 {len(r.get('hit') or [])}" if r.get("hit") else "")
                             + (f" · 다침 {', '.join(r['injured'])}" if r.get("injured") else ""))
            else:
                lines.append(f"• {d[5:].replace('-', '/')} 조용한 밤 (위험도 {r['score']})")
    else:
        lines.append("\n아직 침입 기록이 없어요.")
    from . import levels
    top = levels.ranking(10)
    if top:
        lines.append("\n**전투 레벨**")
        lines.append(" · ".join(f"{n} {levels.tag(n)}" + (f"(MVP {st['mvp']})" if st["mvp"] else "") for n, st in top))
    return "\n".join(lines)[:1900]

