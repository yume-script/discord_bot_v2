"""
담당 실적(KPI) - 봇이 쓰는 MCP의 실제 수치를 "자리(부서 업무)"의 실적으로 바꾼다.

| 자리(담당)            | 소속 / 부서                 | 실제 수치 (MCP)                               | 처음 담당 |
|----------------------|----------------------------|----------------------------------------------|----------|
| 일반/웹툰 라인 입고    | 포링푸드 생산부              | 북오아시스 일반/웹툰 서재 오늘 입고 권 수        | 애순이    |
| 스페셜 라인 입고       | 포링푸드 생산부              | 북오아시스 성인/화보 서재 오늘 입고 권 수        | 김동식    |
| 영상 라인 입고         | 포링푸드 홍보마케팅부         | Plex 오늘 신규 등록 영상 수                    | 오동백    |
| 출하량               | 포링푸드 영업기획부           | Tautulli 오늘 재생 수                         | 권민우    |
| 고객 문의 처리량        | 포링푸드 영업지원부           | 대화 로그 DB 오늘 사람 메시지 수               | 최수연    |
| 설비 가동률            | 포링푸드 생산기획부           | server_status CPU %                         | 정명석    |
| 창고 적재율            | 포링푸드 물류운송부           | server_status 디스크 사용률 (낮을수록 좋다)     | 이지안    |
| 설비 안정성            | 에린 로지스틱스 시설관리부     | server_status 연속 가동일 / 멈춘 서비스 수      | 리정혁    |
| 연산 처리             | 에린 로지스틱스 기획전략부     | LiteLLM 오늘 요청 수 (못 받으면 봇 집계)        | 최택      |
| 운영 경비             | 에린 로지스틱스 데이터보안부   | LiteLLM 오늘 토큰 (평소보다 적을수록 좋다)      | 동그라미  |
| 매장 손님 수           | 카페 오후세시                | Tautulli 지금 시청 중인 스트림 수               | 소라      |

- 담당은 사람이 아니라 자리에 붙는다: 담당자가 교체(rotation.py)로 쉬러 가면 같은 소속·부서의 다른 에이전트가
  인수인계받는다 (처음 담당자가 있으면 그 사람 먼저). 부서에 에이전트가 없으면 그동안 공석.
  한 사람이 자리를 둘 이상 맡을 수도 있다 (생산부에 한 명만 남았을 때).
- 매시 회차(record)마다 자리별 오늘 점수(평소=100)를 그날 담당자 이름과 함께 남긴다. 누적 지표(입고/출하/문의)는
  오늘 값 / 최근 7일 하루 평균, 수준 지표(손님/가동률)는 오늘 평균 / 지난 기록 평균, 적재율은 낮을수록,
  안정성은 멈춘 서비스/재가동이 없을수록 높다.
- 주간 실적 발표(weekly): 월요일 아침, 지난주 점수로 소속별 순위. 포링푸드는 단톡방에 부서장이 발표
  (LLM 한 번), 1등은 기분/자신감 + 보너스, 꼴찌는 스트레스. 순위를 매길 사람이 없는 소속은 본인에게만 알린다.
- 월간 평가(monthly): 매달 1일 아침, 지난달 소속별 1등에게 "N월 우수 사원" 칭호 - 영구 기억으로 남는다.
- 날씨 장사(weather_trade): 기상청 날씨로 카페/포장마차 손님이 오늘 얼마나 달라질지 (agents.json "trade").

저장: storage/poring_food/kpi.json {"holders", "daily": {날짜: {자리: 기록}}, "weekly_done", "monthly_done",
"titles", "history"} - daily는 최근 70일만, 나머지는 지우지 않는다.
이 모듈은 agents.py를 import하지 않는다 (게시/받은편지함은 agents가 한다).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from . import metrics, signals
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

KPI_PATH = os.path.join(STATE_DIR, "kpi.json")
PF = "포링푸드 (Poring Food)"
ER = "에린 로지스틱스 (Erinn Logistics)"
CAFE = "카페 오후세시"
KEEP_DAYS = 70
WEEKLY_HOUR = 9
WEEKLY_BONUS = int(os.getenv("PORING_KPI_WEEKLY_BONUS", "300000"))
MONTHLY_BONUS = int(os.getenv("PORING_KPI_MONTHLY_BONUS", "500000"))
SCORE_CAP = 200
CPU_OVERLOAD = 80
DISK_ALERT = 85

# kind: count(하루 누적, 평소 대비) / cost(하루 누적, 적을수록 좋음) / level(그때그때 수준) / disk(낮을수록 좋음) /
#       stability(문제 없을수록 좋음)
POSTS = [
    {"id": "books", "company": PF, "dept": "생산부", "first": "애순이", "kind": "count", "metric": "books",
     "line": "일반/웹툰 라인", "verb": "입고", "unit": "권", "source": "북오아시스 일반/웹툰 서재 오늘 입고 권 수"},
    {"id": "books_adult", "company": PF, "dept": "생산부", "first": "김동식", "kind": "count", "metric": "books_adult",
     "line": "스페셜 라인", "verb": "입고", "unit": "권", "source": "북오아시스 성인/화보 서재 오늘 입고 권 수"},
    {"id": "plex", "company": PF, "dept": "홍보마케팅부", "first": "오동백", "kind": "count", "metric": "plex",
     "line": "영상 라인", "verb": "입고", "unit": "건", "source": "Plex 오늘 신규 등록 영상 수"},
    {"id": "sales", "company": PF, "dept": "영업기획부", "first": "권민우", "kind": "count", "metric": "sales",
     "line": "출하", "verb": "출하", "unit": "건", "source": "Tautulli 오늘 재생 수"},
    {"id": "orders", "company": PF, "dept": "영업지원부", "first": "최수연", "kind": "count", "metric": "orders",
     "line": "고객 문의 처리", "verb": "문의", "unit": "건", "source": "오늘 사람이 봇에게 보낸 메시지 수"},
    {"id": "facility_cpu", "company": PF, "dept": "생산기획부", "first": "정명석", "kind": "level", "metric": "cpu",
     "line": "설비 가동률", "unit": "%", "source": "server_status CPU 사용률"},
    {"id": "facility_disk", "company": PF, "dept": "물류운송부", "first": "이지안", "kind": "disk", "metric": "disk",
     "line": "창고 적재율", "unit": "%", "source": "server_status 디스크 사용률"},
    {"id": "facility_stability", "company": ER, "dept": "시설관리부", "first": "리정혁", "kind": "stability",
     "metric": "stability", "line": "설비 안정성", "unit": "점", "source": "server_status 연속 가동일 / 멈춘 서비스"},
    {"id": "llm_requests", "company": ER, "dept": "기획전략부", "first": "최택", "kind": "count", "metric": "llm_requests",
     "line": "연산 처리", "verb": "처리", "unit": "건", "source": "LiteLLM 오늘 요청 수 (프록시 전체, 못 받으면 봇 집계)"},
    {"id": "llm_cost", "company": ER, "dept": "데이터보안부", "first": "동그라미", "kind": "cost", "metric": "llm_tokens",
     "line": "운영 경비", "unit": "토큰", "source": "LiteLLM 오늘 사용 토큰 (평소보다 적을수록 좋음)"},
    {"id": "streams", "company": CAFE, "dept": "카페", "first": "소라", "kind": "level", "metric": "streams",
     "line": "매장 손님", "unit": "명", "source": "Tautulli 지금 시청 중인 스트림 수"},
]
POST_BY_ID = {p["id"]: p for p in POSTS}
_RANK_ORDER = {"사원": 0, "주임": 1, "대리": 2, "과장": 3, "차장": 4, "부장": 5, "사장": 6}


# ===================================================================== 저장
def _load() -> dict:
    try:
        with open(KPI_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    tmp = KPI_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, KPI_PATH)


# ===================================================================== 자리 배정 (인수인계)
def _dept_of(agent: dict, roster_by_name: dict) -> str:
    return agent.get("dept") or (roster_by_name.get(agent["name"]) or {}).get("dept", "")


def _rank_of(agent: dict, roster_by_name: dict) -> str:
    return agent.get("rank") or (roster_by_name.get(agent["name"]) or {}).get("rank", "")


def assign(agents: list[dict], roster: list[dict]) -> tuple[dict[str, list[dict]], list[tuple[dict, str, str]]]:
    """
    지금 에이전트들에게 자리를 배정한다. 반환: ({이름: [맡은 자리...]}, [(자리, 전 담당자, 새 담당자)] - 바뀐 것만).
    담당자가 지금도 에이전트면 그대로, 아니면 같은 소속·부서의 에이전트에게 넘긴다 (처음 담당자 > 실무자 > 부장).
    """
    data = _load()
    holders = data.setdefault("holders", {})
    roster_by_name = {c["name"]: c for c in roster}
    active = {a["name"]: a for a in agents}
    out: dict[str, list[dict]] = {}
    changes = []
    changed = False
    for post in POSTS:
        cur = holders.get(post["id"])
        if cur not in active:
            cands = [a for a in agents if a.get("company") == post["company"]
                     and _dept_of(a, roster_by_name) == post["dept"]]
            cands.sort(key=lambda a: (a["name"] != post["first"],
                                      _RANK_ORDER.get(_rank_of(a, roster_by_name), 2) >= 5, a["name"]))
            new = cands[0]["name"] if cands else None
            if new != cur:
                if post["id"] in holders:  # 처음 배정(배포 직후)은 인수인계가 아니다
                    changes.append((post, cur or "", new or ""))
                holders[post["id"]] = new
                changed = True
            cur = new
        if cur:
            out.setdefault(cur, []).append(post)
    if changed:
        _save(data)
        for post, old, new in changes:
            print(f"[실적] {post['line']} 담당: {old or '공석'} -> {new or '공석'}")
    return out, changes


def holder(post_id: str) -> str:
    return _load().get("holders", {}).get(post_id) or ""


def posts_of(name: str) -> list[dict]:
    """이 사람이 지금 맡은 자리들 (마지막 배정 기준)."""
    holders = _load().get("holders", {})
    return [p for p in POSTS if holders.get(p["id"]) == name]


# ===================================================================== 지금 수치
def reading(post: dict) -> dict | None:
    """자리 하나의 지금 수치. {"value", "avg"?, "pace"?, "saturated"?, "text", "alert"}. 집계 전이면 None."""
    kind, metric = post["kind"], post["metric"]
    if kind == "count":
        x = metrics.count(metric)
        if not x:
            return None
        text = f"오늘 {post['verb']} {x['today']}{post['unit']}{'+' if x.get('saturated') else ''}"
        extra = [f"최근 7일 하루 평균 {x['avg']:g}{post['unit']}" if x.get("avg") is not None else "", x.get("pace", "")]
        extra = [e for e in extra if e]
        fails = int(x.get("failures") or 0)
        alert = f"오늘 처리 실패 {fails}건 - 연산 라인이 자꾸 멈춘다" if fails >= max(5, x["today"] * 0.2) else ""
        return {"value": int(x["today"]), "avg": x.get("avg"), "pace": x.get("pace", ""),
                "saturated": bool(x.get("saturated")), "text": text + (f" ({', '.join(extra)})" if extra else ""),
                "alert": alert}
    if kind == "cost":
        x = metrics.count(metric)
        if not x:
            return None
        avg = x.get("avg")
        text = f"오늘 쓴 연산 경비 {x['today']:,}{post['unit']}" + (f" (최근 7일 하루 평균 {avg:,.0f}{post['unit']})" if avg else "")
        heavy = bool(avg) and metrics.pace(int(x["today"]), avg) == "평소보다 훨씬 바쁨"
        return {"value": int(x["today"]), "avg": avg, "text": text,
                "alert": "경비가 평소보다 훨씬 많이 나가는 중" if heavy else ""}
    if metric == "streams":
        n = metrics.streams()
        if n is None:
            return None
        return {"value": n, "text": f"지금 매장 손님 {n}명", "alert": ""}
    fac = signals.live_value("facility_numbers")
    if not fac:
        return None
    if metric == "cpu" and "cpu" in fac:
        v = fac["cpu"]
        return {"value": v, "text": f"지금 설비 가동률 {v:g}%",
                "alert": "설비 과부하 - 라인이 숨가쁘게 돌아감" if v >= CPU_OVERLOAD else ""}
    if metric == "disk" and "disk" in fac:
        v = fac["disk"]
        return {"value": v, "text": f"지금 창고 적재율 {v:g}% (낮을수록 여유)",
                "alert": "창고가 거의 찼다 - 재고 정리 필요" if v >= DISK_ALERT else ""}
    if metric == "stability":
        down, days = int(fac.get("down", 0)), fac.get("uptime_days")
        problems = []
        if down:
            problems.append(f"멈춘 라인 {down}개")
        if days is not None and days < 1:
            problems.append("설비가 최근 재가동됨(정비/정전 후 복구)")
        score = max(0, 100 - 30 * down - (40 if days is not None and days < 1 else 0))
        text = (f"설비 안정성 {score}점" + (f", 연속 가동 {int(days)}일" if days is not None else "")
                + (f" - {', '.join(problems)}" if problems else " (이상 없음)"))
        return {"value": score, "text": text, "alert": ", ".join(problems)}
    return None


def _hist_mean(data: dict, post_id: str, today: str) -> float | None:
    """수준 지표의 지난 기록 평균 (최근 7일, 오늘 제외)."""
    vals = []
    for d in sorted(data.get("daily", {})):
        r = data["daily"][d].get(post_id)
        if d < today and r and r.get("n"):
            vals.append(r["sum"] / r["n"])
    vals = vals[-7:]
    return sum(vals) / len(vals) if vals else None


def _score(post: dict, rec: dict, hist: float | None) -> int:
    kind = post["kind"]
    if kind == "count":
        avg = rec.get("avg")
        if rec.get("saturated"):
            return SCORE_CAP
        if avg:
            return int(min(SCORE_CAP, rec["last"] / avg * 100))
        return 100 if rec["last"] > 0 else 50
    if kind == "level":
        mean = rec["sum"] / rec["n"]
        s = mean / hist * 100 if hist else (100 if mean > 0 else 50)
        if post["metric"] == "cpu" and mean >= CPU_OVERLOAD:
            s -= (mean - CPU_OVERLOAD) * 5  # 과부하는 실적이 아니라 사고
        return int(max(0, min(SCORE_CAP, s)))
    if kind == "cost":  # 평소보다 적게 쓸수록 높다 (하루 마지막 기록이 그날 점수)
        avg = rec.get("avg")
        if not avg:
            return 100
        return int(min(SCORE_CAP, avg / max(rec["last"], 1) * 100))
    if kind == "disk":
        return int(max(0, min(SCORE_CAP, 150 - rec["sum"] / rec["n"])))
    return int(rec.get("min", rec["last"]))  # stability: 그날 가장 나빴던 때


def record(now: datetime | None = None) -> None:
    """[매시 회차] 자리별 지금 수치를 오늘 기록에 더하고 오늘 점수를 다시 낸다 (담당자 이름도 같이)."""
    now = now or now_kst()
    today = now.strftime("%Y-%m-%d")
    data = _load()
    daily = data.setdefault("daily", {})
    rows = daily.setdefault(today, {})
    holders = data.get("holders", {})
    for post in POSTS:
        x = reading(post)
        if not x:
            continue
        rec = rows.get(post["id"]) or {"sum": 0.0, "n": 0}
        v = x["value"]
        rec.update(last=v, sum=rec["sum"] + v, n=rec["n"] + 1, max=max(rec.get("max", v), v),
                   min=min(rec.get("min", v), v), holder=holders.get(post["id"]) or rec.get("holder", ""),
                   company=post["company"])
        if post["kind"] in ("count", "cost"):
            rec.update(avg=x.get("avg"), saturated=x.get("saturated", False))
        rec["score"] = _score(post, rec, _hist_mean(data, post["id"], today))
        rows[post["id"]] = rec
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data["daily"] = {d: r for d, r in daily.items() if d >= cutoff}
    _save(data)


# ===================================================================== 에이전트 지각
def titles(name: str) -> list[str]:
    return _load().get("titles", {}).get(name, [])


def prompt_block(name: str, posts: list[dict]) -> str:
    """에이전트 지각용: 내 담당 자리의 지금 수치 + 이번 주 내 점수 + 칭호."""
    lines = []
    for post in posts:
        x = reading(post)
        lines.append(f"- {post['line']}: {x['text'] if x else '오늘 집계 전'}"
                     + (f" ⚠ {x['alert']}" if x and x.get("alert") else ""))
    if not lines:
        return ""
    now = now_kst()
    week = week_scores(now - timedelta(days=now.weekday()), now + timedelta(days=1)).get(name)
    out = "[내 담당 업무 - 실제 수치가 내 실적이다. 회사 사람들도 이 숫자를 안다]\n" + "\n".join(lines)
    if week:
        out += f"\n이번 주 내 실적 점수: 평균 {week['score']}점 (평소=100, 월요일 아침에 순위 발표)"
    t = titles(name)
    if t:
        out += f"\n내가 받은 칭호: {', '.join(t[-3:])}"
    return out


def weather_trade(trade: str, weather: str | None = None) -> dict | None:
    """
    카페/포장마차의 오늘 날씨 장사. {"pct": 손님 증감 %, "why": 이유} 또는 None (날씨 모름/해당 없음).
    날씨는 기상청 실측 한 줄 ("기온 18°C, 맑음, 비 (1mm), ...").
    """
    if trade not in ("카페", "포장마차"):
        return None
    weather = signals.weather_text() if weather is None else weather
    if not weather:
        return None
    m = re.search(r"기온\s*(-?\d+(?:\.\d+)?)", weather)
    temp = float(m.group(1)) if m else None
    tokens = [t.strip() for t in re.split(r"[,/→]", weather)]
    rain = any(t.startswith(("비", "소나기", "빗방울")) for t in tokens) or "폭우" in weather
    snow = any(t.startswith(("눈", "진눈깨비")) for t in tokens)
    cafe = trade == "카페"
    if snow:
        return {"pct": 5 if cafe else 20, "why": "눈 오는 날 - " + ("창가 자리에 오래 머무는 손님" if cafe else "어묵 국물에 소주 한잔")}
    if rain:
        return {"pct": 15 if cafe else 25, "why": "비 오는 날 - " + ("따뜻한 음료 들고 비 피하는 손님" if cafe else "비 오는 날엔 파전에 막걸리")}
    if temp is not None and temp >= 30:
        return {"pct": 20 if cafe else -20, "why": "더운 날 - " + ("아이스 음료 주문 폭주" if cafe else "더워서 포장마차는 한산")}
    if temp is not None and temp <= 0:
        return {"pct": 10 if cafe else 15, "why": "추운 날 - " + ("몸 녹이러 들어오는 손님" if cafe else "따끈한 국물 찾는 손님")}
    return {"pct": 0, "why": "무난한 날씨 - 평소 장사"}


def trade_block(agent: dict) -> str:
    w = weather_trade(agent.get("trade", ""))
    if not w:
        return ""
    sign = f"+{w['pct']}" if w["pct"] > 0 else str(w["pct"])
    return f"[오늘 가게 장사 - 날씨] {w['why']} (손님 {sign}% 예상)"


# ===================================================================== 주간 / 월간 평가
def week_scores(start: datetime, end: datetime) -> dict[str, dict]:
    """[start, end) 기간 사람별 평균 점수 {이름: {"score", "company", "posts": [자리 이름], "days"}}."""
    s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    acc: dict[str, dict] = {}
    for d, rows in _load().get("daily", {}).items():
        if not (s <= d < e):
            continue
        for pid, rec in rows.items():
            who = rec.get("holder")
            if not who or pid not in POST_BY_ID:
                continue
            a = acc.setdefault(who, {"sum": 0, "n": 0, "company": rec.get("company", ""), "posts": []})
            a["sum"] += rec.get("score", 0)
            a["n"] += 1
            line = POST_BY_ID[pid]["line"]
            if line not in a["posts"]:
                a["posts"].append(line)
    return {n: {"score": round(a["sum"] / a["n"]), "company": a["company"], "posts": a["posts"], "days": a["n"]}
            for n, a in acc.items() if a["n"]}


def _ranking(scores: dict[str, dict]) -> dict[str, list[tuple[str, dict]]]:
    by_company: dict[str, list[tuple[str, dict]]] = {}
    for name, x in scores.items():
        by_company.setdefault(x["company"], []).append((name, x))
    for rows in by_company.values():
        rows.sort(key=lambda r: (-r[1]["score"], r[0]))
    return by_company


def weekly_due(now: datetime) -> bool:
    key = (now - timedelta(days=7)).strftime("%G-W%V")
    return now.weekday() == 0 and now.hour >= WEEKLY_HOUR and _load().get("weekly_done") != key


def weekly(now: datetime) -> dict:
    """지난주(월~일) 순위 {"week": 키, "label": "10/5~10/11", "companies": {소속: [(이름, 점수 정보)...]}}.
    부르면 이번 주 발표는 끝난 것으로 표시한다."""
    monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6)
    key = monday.strftime("%G-W%V")
    data = _load()
    data["weekly_done"] = key
    ranking = _ranking(week_scores(monday, monday + timedelta(days=7)))
    data.setdefault("history", []).append({"week": key, "ranking": {c: [(n, x["score"]) for n, x in rows]
                                                                    for c, rows in ranking.items()}})
    data["history"] = data["history"][-104:]
    _save(data)
    return {"week": key, "label": f"{monday.month}/{monday.day}~{sunday.month}/{sunday.day}", "companies": ranking}


def monthly_due(now: datetime) -> bool:
    prev = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return now.day == 1 and now.hour >= WEEKLY_HOUR and _load().get("monthly_done") != prev


def monthly(now: datetime) -> dict:
    """지난달 소속별 1등 {"month": "2026-09", "label": "9월", "winners": {소속: (이름, 점수 정보)}}.
    1등에게 칭호를 남긴다 (2명 이상 겨룬 소속만)."""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (first - timedelta(days=1)).replace(day=1)
    month = start.strftime("%Y-%m")
    data = _load()
    data["monthly_done"] = month
    winners = {}
    for company, rows in _ranking(week_scores(start, first)).items():
        if len(rows) >= 2:
            name, x = rows[0]
            winners[company] = (name, x)
            title = f"{start.year}년 {start.month}월 우수 사원"
            data.setdefault("titles", {}).setdefault(name, []).append(title)
    _save(data)
    return {"month": month, "label": f"{start.month}월", "winners": winners}
