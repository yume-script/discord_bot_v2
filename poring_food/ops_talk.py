"""
서버 관리 대화 - 이슈가 생길 때마다 역할을 맡은 담당자들이 서로의 역할을 추론하며 대화하고, 그 결론대로 서버를 관리한다.
정해진 시각의 회의는 없다 (이슈가 없으면 조용하다).

이슈 (매 2분 확인, LLM 없이 숫자로):
  upload_throttle  텔레그램이 업로드를 늦춤 (BOA tgup_status throttle_warnings)
  upload_backlog   텔레그램 올리기 대기열이 UPLOAD_BACKLOG개 이상
  gdw_backlog      원고 입고 대기열이 밀리거나 워커가 멈춤 (gdw_queue)
  wtm_trouble      웹툰 다운로더 오류·차단(429/503)·쿠키 만료 흔적 (wtm_status)
  waitfree_window  기다무 충전 시간대를 옮기면 더 받을 수 있음 (waitfree 추천 ≠ 지금, 적용 간격·재생·단계 안전할 때)
  retro            지난 결정 뒤 RETRO_MIN분이 지났고 결과가 나빠짐 (판단착오 회고)
  (서버 경보가 오를 때의 부하 대책 회의와 새 버전 정비 회의는 원래대로 이슈가 생길 때 loadshed·updater가 연다)

대화 (stewards.council - 담당자들이 돌아가며 ROUNDS바퀴):
- 참석자는 이슈에 걸린 시스템의 담당자 + 조정자(애순이). 각자 자기 역할과 다른 참석자의 역할(지키려는 것)을 보고,
  상대가 왜 그렇게 말하는지 추론하며 숫자로 따지거나 양보한다.
- 판단착오(실수): MISTAKE_P 확률로 한 사람이 자기 시스템만 보고 숫자를 잘못 읽거나 과하게 요구하는 실수를 하고,
  다른 사람들은 숫자를 확인해 잡아낸다 (빌런 대신). 결론은 바로잡힌 쪽으로.
- 조정자의 결론(JSON) → 코드의 안전장치를 다시 통과해야 실행:
    download_agent  다운로드 에이전트(boa_agent)에게 결론을 지시로 넘겨 BOA 설정·구독·작업을 바꾸게 (안전 범위 그대로)
    waitfree        기다무 충전 시간대 옮기기 (waitfree.apply - 적용 간격·재생·단계 검사)
    none            지켜보기
- 실제 실수 회고: 결정할 때의 숫자(서버 단계·올리기 대기·입고 대기)를 남겨 두고 RETRO_MIN분 뒤 다시 잰다.
  나빠졌으면 '판단착오'로 기록하고(그 결정에 참여한 담당자의 기억에도) 회고 대화를 연다 - 되돌릴지 정한다.
저장: storage/poring_food/ops_talk.json {"last": {이슈: 시각}, "talks": [...], "mistakes": [...]}
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime

from . import gdw_queue
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로
from .config import STATE_DIR

ENABLED = os.getenv("PORING_OPS_TALK", "1") not in ("0", "false", "False", "")
STATE_PATH = os.path.join(STATE_DIR, "ops_talk.json")
COOLDOWN_MIN = int(os.getenv("PORING_OPS_TALK_COOLDOWN_MIN", "180"))   # 같은 이슈로 다시 모이기까지
MAX_PER_DAY = int(os.getenv("PORING_OPS_TALK_MAX_PER_DAY", "24"))
UPLOAD_BACKLOG = int(os.getenv("PORING_OPS_UPLOAD_BACKLOG", "40"))
MISTAKE_P = float(os.getenv("PORING_OPS_MISTAKE_P", "0.25"))
RETRO_MIN = int(os.getenv("PORING_OPS_RETRO_MIN", "120"))
ROUNDS = 2
KEEP = 60

ISSUES = {   # 이슈 -> (제목, 참석 시스템, 할 수 있는 조치)
    "upload_throttle": ("텔레그램 업로드 속도 제한", ["upload", "assistant", "plex", "loadshed"], ["download_agent", "none"]),
    "upload_backlog": ("텔레그램 올리기 대기열 밀림", ["upload", "assistant", "loadshed", "plex"], ["download_agent", "none"]),
    "gdw_backlog": ("원고 입고 대기열 밀림", ["assistant", "bookoasis", "loadshed"], ["download_agent", "none"]),
    "wtm_trouble": ("웹툰 다운로더 오류·차단", ["assistant", "bookoasis", "loadshed"], ["download_agent", "none"]),
    "waitfree_window": ("기다무 충전 시간대 (웹툰 받는 시간)", ["assistant", "plex", "ff", "bookoasis", "loadshed"],
                        ["waitfree", "download_agent", "none"]),
    "retro": ("지난 결정 회고 (판단착오?)", [], ["download_agent", "waitfree", "none"]),
}
_TROUBLE_WORDS = ("429", "503", "차단", "blocked", "쿠키 만료", "로그인 안 됨", "Too Many Requests")


def enabled() -> bool:
    return ENABLED


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


# ===================================================================== 지금 숫자
def _call(name: str, args: dict | None = None):
    try:
        return gdw_queue.call_tool(name, args or {}, timeout=20) if gdw_queue.enabled() else None
    except Exception:  # noqa: BLE001
        return None


def metrics(now: datetime | None = None) -> dict:
    """결정 전·후 비교에 쓰는 숫자 (모르면 None)"""
    from . import loadshed
    tg = _call("tgup_status", {"limit": 5}) or {}
    gd = gdw_queue.digest(gdw_queue.fetch()) if gdw_queue.enabled() else None
    return {"stage": loadshed.stage(now), "upload_queue": tg.get("queue_total"),
            "upload_limited": len(tg.get("throttle_warnings") or []), "gdw_pending": (gd or {}).get("pending")}


# ===================================================================== 이슈 찾기 (LLM 없이)
def detect(now: datetime | None = None, call=None, gdw=None, waitfree_issue=None) -> list[dict]:
    """지금 걸려 있는 이슈들 [{key, facts}] (쿨다운 중인 것도 포함 - 고르는 건 next_issue)"""
    now = now or now_kst()
    call = call or _call
    out = []
    tg = call("tgup_status", {"limit": 10}) or {}
    if tg.get("throttle_warnings"):
        out.append({"key": "upload_throttle", "facts": "텔레그램이 업로드를 늦춤: " + " / ".join(tg["throttle_warnings"])[:600]})
    if (tg.get("queue_total") or 0) >= UPLOAD_BACKLOG:
        out.append({"key": "upload_backlog", "facts": f"텔레그램 올리기 대기 {tg['queue_total']}개 (기준 {UPLOAD_BACKLOG}), "
                                                       f"올리는 중 {len(tg.get('running') or [])}개, 시간대 밖 {bool(tg.get('off_hours'))}"})
    d = gdw if gdw is not None else (gdw_queue.digest(gdw_queue.fetch()) if gdw_queue.enabled() else None)
    if d and (d.get("level", 0) >= 1 or not d.get("alive", True)):
        out.append({"key": "gdw_backlog", "facts": gdw_queue.line(d)})
    wtm = call("wtm_status") or {}
    job = wtm.get("job") or {}
    logs = " ".join(str(x) for x in (wtm.get("log_tail") or [])[-30:])
    hits = [w for w in _TROUBLE_WORDS if w in logs]
    if job.get("last_error") or hits:
        out.append({"key": "wtm_trouble", "facts": (f"최근 오류: {str(job.get('last_error'))[:200]} " if job.get("last_error") else "")
                    + (f"로그에 {', '.join(hits)}" if hits else "")})
    wf = waitfree_issue() if waitfree_issue else _waitfree_issue(now)
    if wf:
        out.append({"key": "waitfree_window", "facts": wf})
    return out


def _waitfree_issue(now: datetime) -> str:
    try:
        from . import waitfree
        prof = waitfree.profile()
        s, e, why = waitfree.best_window(10, prof)
        if "기본값" in why:
            return ""
        applied = (waitfree._state().get("applied") or [])[-1:]
        cur = (applied[0]["start"], applied[0]["end"]) if applied else None
        if cur == (s, e) or waitfree.guard(now):
            return ""
        return f"추천 충전 시간대 {s:02d}~{e:02d}시 ({why}) - 지금 {f'{cur[0]:02d}~{cur[1]:02d}시' if cur else '맞춘 적 없음'}"
    except Exception:  # noqa: BLE001
        return ""


def _retro_due(state: dict, now: datetime, cur: dict) -> dict | None:
    """회고할 지난 결정 (RETRO_MIN분 지났고 아직 안 본 것). 숫자가 나빠졌으면 이슈"""
    for t in reversed(state.get("talks") or []):
        if t.get("retro_done") or not t.get("acted"):
            continue
        at = datetime.fromisoformat(t["at"])
        if (now.replace(tzinfo=None) - at.replace(tzinfo=None)).total_seconds() < RETRO_MIN * 60:
            continue
        t["retro_done"] = now.isoformat(timespec="minutes")
        worse = worse_than(t.get("before") or {}, cur)
        t["retro"] = worse or "괜찮음"
        if worse:
            return {"key": "retro", "facts": f"{t['at'][5:16].replace('T', ' ')} '{t['title']}' 결정 ({t.get('summary', '')[:200]}) 뒤 "
                                             f"{RETRO_MIN}분: {worse}", "talk": t}
    return None


def worse_than(before: dict, after: dict) -> str:
    """결정 뒤 나빠진 것 (없으면 '')"""
    bits = []
    if before.get("stage") is not None and after.get("stage") is not None and after["stage"] >= before["stage"] + 2:
        bits.append(f"서버 경보 {before['stage']}→{after['stage']}단계")
    for k, label in (("upload_queue", "텔레그램 올리기 대기"), ("gdw_pending", "원고 입고 대기")):
        b, a = before.get(k), after.get(k)
        if b is not None and a is not None and a >= max(b * 1.5, b + 10):
            bits.append(f"{label} {b}→{a}개")
    if (after.get("upload_limited") or 0) > (before.get("upload_limited") or 0):
        bits.append("텔레그램 업로드 제한이 새로 걸림")
    return ", ".join(bits)


def next_issue(now: datetime | None = None, detected: list[dict] | None = None, cur: dict | None = None) -> dict | None:
    """지금 대화할 이슈 하나 (회고가 먼저, 쿨다운·하루 상한 지킴). 회고 확인 기록은 저장한다"""
    now = now or now_kst()
    state = _load()
    day = now.strftime("%Y-%m-%d")
    if sum(1 for t in state.get("talks") or [] if t.get("at", "").startswith(day)) >= MAX_PER_DAY:
        return None
    if any(not t.get("retro_done") and t.get("acted") for t in state.get("talks") or []):
        retro = _retro_due(state, now, cur if cur is not None else metrics(now))
        _save(state)
        if retro:
            return retro
    last = state.get("last") or {}
    for it in detected if detected is not None else detect(now):
        prev = last.get(it["key"])
        if prev and (now.replace(tzinfo=None) - datetime.fromisoformat(prev).replace(tzinfo=None)).total_seconds() < COOLDOWN_MIN * 60:
            continue
        return it
    return None


# ===================================================================== 대화
def _roles_text(parties: list[str], agents) -> str:
    from . import stewards
    return "\n".join(f"- {stewards.steward(k, agents)}: {stewards.SYSTEMS[k]['label']} - 지키려는 것: {stewards.SYSTEMS[k]['wants']}"
                     for k in parties if k in stewards.SYSTEMS)


def _decision_spec(actions: list[str]) -> str:
    opts = {"download_agent": '{"do": "download_agent", "note": "다운로드 에이전트에게 줄 지시 1~2문장 (무엇을 늘리고/줄일지와 까닭)"}',
            "waitfree": '{"do": "waitfree", "start": 시작시, "end": 끝시}  (기다무 충전 시간대, 길이 4~16시간)',
            "none": '{"do": "none"}  (지켜보기)'}
    return ("JSON으로만: {\"actions\": [아래 중 필요한 것], \"reason\": \"숫자 근거 한두 문장\", "
            "\"mistake\": \"누군가 숫자를 잘못 읽었거나 과하게 요구했다면 누가 무엇을 (없으면 빈 글)\", "
            "\"say\": \"내 말투로 결론을 알리는 1~2문장\"}\n할 수 있는 조치:\n" + "\n".join(opts[a] for a in actions))


def talk(issue: dict, now: datetime | None = None, council=None, execute=None, cur: dict | None = None,
         rng: random.Random | None = None) -> dict:
    """[스레드에서, 잠금 안에서] 이슈 하나로 담당자 대화 → 결론 실행 → 기록"""
    from . import stewards
    from . import agents as ag
    from .llm import llm_json
    now = now or now_kst()
    rng = rng or random.Random()
    title, parties, actions = ISSUES[issue["key"]]
    if issue["key"] == "retro":
        prev = issue.get("talk") or {}
        parties = prev.get("parties") or ["assistant", "loadshed"]
        title = f"회고: {prev.get('title', '')}"
    agents = ag.load_agents()
    by_name = {a["name"]: a for a in agents}
    seat_keys = [k for k in dict.fromkeys(parties) if k in stewards.SYSTEMS and stewards.steward(k, agents) in by_name]
    roles = _roles_text(seat_keys + ["chair"], agents)
    # 판단착오: 한 사람(조정자 말고)이 이번에 실수를 한다 - 다른 사람이 잡아내야 한다
    slip = None
    cands = [stewards.steward(k, agents) for k in seat_keys if stewards.steward(k, agents) != stewards.CHAIR]
    if cands and rng.random() < MISTAKE_P:
        slip = rng.choice(cands)
    spoken = {"n": 0}

    def speak(key, name, user):
        spoken["n"] += 1
        extra = ("\n[참석자와 역할]\n" + roles + "\n이번 대화의 목표: 위 이슈를 풀되 시청·독서 > 서버 안정 > 자료 등록 순서를 지킨다. "
                 "다른 담당자가 왜 그렇게 말하는지 그 사람의 역할로 추론하고, 그 사람의 숫자가 [실제 상황]과 맞는지 확인해 "
                 "틀렸으면 짚는다. 내 역할을 지키되 목표를 위해 필요한 양보는 한다.")
        if slip == name and spoken["n"] <= len(seat_keys):
            extra += ("\n[이번엔 너의 판단착오] 너는 지금 바빠서 자기 시스템 쪽만 보고 숫자 하나를 잘못 읽거나(예: 대기 개수·단계를 "
                      "실제보다 크게/작게) 자기 쪽에 과하게 유리한 요구를 한다. 일부러 티 내지 말고 그럴듯하게 말한다. "
                      "누가 짚으면 다음 차례에 인정하고 바로잡는다.")
        out = llm_json(ag._system(by_name[name]), user + extra, temperature=0.8, timeout=45, tag=f"서버 관리 대화 {name}",
                       agent=name) or {}
        return str(out.get("say") or "").strip()[:300]

    facts = issue["facts"] + "\n" + _numbers_text(cur if cur is not None else metrics(now))
    res = (council or stewards.council)(title, facts, seat_keys, _decision_spec(actions), rounds=ROUNDS, speak=speak)
    d = (res or {}).get("decision") or {}
    results = (execute or run_actions)(d.get("actions") or [], actions, title, d.get("reason") or "")
    acted = any(r.get("ok") for r in results)
    state = _load()
    rec = {"at": now.isoformat(timespec="minutes"), "issue": issue["key"], "title": title, "parties": seat_keys,
           "summary": str(d.get("say") or d.get("reason") or "")[:300], "reason": str(d.get("reason") or "")[:300],
           "mistake": str(d.get("mistake") or "")[:200], "slip": slip or "", "results": results, "acted": acted,
           "before": cur if cur is not None else metrics(now)}
    state.setdefault("last", {})[issue["key"]] = rec["at"]
    state["talks"] = (state.get("talks") or [])[-(KEEP - 1):] + [rec]
    if issue["key"] == "retro":
        prev = issue.get("talk") or {}
        state["mistakes"] = (state.get("mistakes") or [])[-(KEEP - 1):] + [
            {"at": rec["at"], "decision_at": prev.get("at"), "title": prev.get("title"), "worse": issue["facts"],
             "who": prev.get("parties"), "retro": rec["summary"]}]
        _remember_mistake(prev, issue["facts"], agents)
    elif rec["mistake"]:
        state["mistakes"] = (state.get("mistakes") or [])[-(KEEP - 1):] + [
            {"at": rec["at"], "title": title, "caught": rec["mistake"], "slip": slip or ""}]
    _save(state)
    print(f"[서버 관리 대화] {title} - {rec['summary'][:120]} / 조치 {[r.get('do') for r in results if r.get('ok')]}"
          + (f" / 판단착오: {rec['mistake']}" if rec["mistake"] else ""))
    return rec


def _numbers_text(m: dict) -> str:
    return (f"[지금 숫자] 서버 경보 최고 {m.get('stage')}단계 · 텔레그램 올리기 대기 {m.get('upload_queue', '모름')}개"
            f"{' · 업로드 제한 걸린 계정 ' + str(m['upload_limited']) + '개' if m.get('upload_limited') else ''}"
            f" · 원고 입고 대기 {m.get('gdw_pending', '모름')}건")


def _remember_mistake(prev: dict, worse: str, agents) -> None:
    from . import memory, stewards
    try:
        for k in prev.get("parties") or []:
            n = stewards.steward(k, agents)
            memory.remember(n, f"판단착오: '{prev.get('title')}' 결정 뒤 {worse} - 다음엔 숫자를 더 확인하자", 7, [], "실수")
    except Exception as e:  # noqa: BLE001
        print(f"[서버 관리 대화] 실수 기억 실패: {e}")


def run_actions(acts: list, allowed: list[str], title: str, reason: str) -> list[dict]:
    """결론의 조치를 실행 - 각자 안전장치를 그대로 통과해야 한다. [{do, ok, result}]"""
    out = []
    for a in acts[:3]:
        if not isinstance(a, dict):
            continue
        do = a.get("do")
        if do not in allowed or do == "none":
            continue
        try:
            if do == "download_agent":
                from . import boa_agent
                if not boa_agent.enabled():
                    out.append({"do": do, "ok": False, "result": "다운로드 에이전트 꺼짐"})
                    continue
                r = boa_agent.run(note=f"[담당자 회의 '{title}' 결론] {str(a.get('note') or reason)[:400]}")
                out.append({"do": do, "ok": True, "result": f"바꾼 것 {len(r['changes'])}개 · 제안 {len(r['proposals'])}개"})
            elif do == "waitfree":
                from . import waitfree
                msg = waitfree.apply(int(a.get("start")), int(a.get("end")), by="담당자 회의")
                ok = not msg.startswith(("시간대가 이상", "지금은 적용하지", "적용 실패", "시험 모드"))
                out.append({"do": do, "ok": ok, "result": msg[:200]})
        except Exception as e:  # noqa: BLE001
            out.append({"do": do, "ok": False, "result": f"{type(e).__name__}: {e}"[:200]})
    return out


def step(now: datetime | None = None) -> dict | None:
    """[2분마다, 스레드에서, 잠금 안에서] 이슈가 있으면 대화 하나"""
    if not ENABLED:
        return None
    now = now or now_kst()
    cur = metrics(now)
    issue = next_issue(now, cur=cur)
    if not issue:
        return None
    return talk(issue, now, cur=cur)


def recent_text(n: int = 5) -> str:
    st = _load()
    talks = (st.get("talks") or [])[-n:]
    if not talks:
        return "🛡️ 서버 관리 대화: 아직 없습니다 (이슈가 생기면 담당자들이 모입니다)."
    out = ["🛡️ 서버 관리 대화 (최근)"]
    for t in reversed(talks):
        acts = ", ".join(f"{r['do']}({'됨' if r.get('ok') else '안 됨'}: {r.get('result', '')[:60]})" for r in t.get("results") or [])
        out.append(f"**{t['at'][5:16].replace('T', ' ')} {t['title']}** - {t.get('summary', '')[:200]}"
                   + (f"\n  조치: {acts}" if acts else "") + (f"\n  🙈 판단착오: {t['mistake']}" if t.get("mistake") else "")
                   + (f"\n  회고: {t['retro']}" if t.get("retro") else ""))
    return "\n".join(out)
