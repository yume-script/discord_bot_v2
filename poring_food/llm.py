"""
포링푸드 공용 LLM 호출 - JSON 응답만 받는다 (world.py, agents.py, chronicle.py, story.py ...).

- 기본 모델(PORING_LLM_MODEL): 매시 판단/대화 한마디처럼 자주 부르는 것.
- 상위 모델(PORING_LLM_MODEL_SMART, smart=True): 모임 결론/프로젝트 계획/연대기 요약/세계 엔진 계획/작가 회의/
  주간 실적 발표처럼 하루 몇 번뿐인 중요한 판단. 무료 키는 하루 요청 수가 아주 적어서(예: 20회) 하루
  PORING_LLM_SMART_DAILY_MAX회까지만 쓰고, 넘거나 실패하면(한도 초과 429 포함) 바로 기본 모델로 다시 부른다.
- 기본 모델이 429(분당 한도)면 PORING_LLM_RETRY_SEC초 쉬고 한 번만 더 부른다 - 매시 회차에 호출이 몰려
  분당 한도에 걸린 판단이 그냥 "평소 루틴"으로 떨어지지 않게.
- 인물 전용 모델(PORING_AGENT_MODELS, agent=이름): 정해 둔 인물의 판단/대화/모임 발언/투표는 그 모델로
  (예: "groq_free:소라,김동식,..." - 다른 두뇌라 말투와 판단이 다른 사람들과 확실히 달라지고, 기본 모델 하루 한도도
  아낀다). 추론 모델이면 생각 토큰을 줄이게 reasoning_effort(PORING_AGENT_MODEL_REASONING, 기본 low)를 붙인다.
  실패하면 그 호출만 기본 모델로 다시 부르고, 429(분당 토큰 한도)면 AGENT_MODEL_COOLDOWN_SEC 동안은 바로 기본 모델로.
- 요청은 모두 ai/llm_usage.py가 센다 (출처 "포링푸드", 상위 모델은 "포링푸드(상위)", 인물 전용은 "포링푸드(모델이름)").
"""
from __future__ import annotations

import json
import os
import time

import requests

from ai import llm_usage

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import API_URL, LITELLM_MASTER_KEY, LLM_MODEL

SMART_MODEL = os.getenv("PORING_LLM_MODEL_SMART", "gemini-free3_7").strip()
SMART_DAILY_MAX = int(os.getenv("PORING_LLM_SMART_DAILY_MAX", "15"))
RETRY_SEC = float(os.getenv("PORING_LLM_RETRY_SEC", "20"))
RETRY_COOLDOWN_SEC = 600  # 재시도해도 또 429면 이만큼은 재시도 없이 바로 실패 (하루 한도를 다 쓴 경우 회차가 늘어지지 않게)
SOURCE = "포링푸드"
SMART_SOURCE = "포링푸드(상위)"
_no_retry_until = 0.0
AGENT_MODEL_REASONING = os.getenv("PORING_AGENT_MODEL_REASONING", "low").strip()
AGENT_MODEL_COOLDOWN_SEC = 60  # 인물 전용 모델이 429를 내면 이만큼은 기본 모델로 (분당 한도가 풀릴 때까지)
AGENT_MODEL_FORBIDDEN_SEC = 1800  # 401/403(키/계정 거부)이면 오래 쉰다 - 매 호출마다 실패하고 기본 모델로 다시 부르는 낭비를 막는다
_agent_model_blocked: dict[str, float] = {}


def parse_agent_models(raw: str) -> dict[str, str]:
    """"groq_free:소라,김동식; other:조이서" -> {"소라": "groq_free", "김동식": "groq_free", "조이서": "other"}."""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        model, sep, names = part.partition(":")
        if not sep or not model.strip():
            continue
        for n in names.split(","):
            if n.strip():
                out[n.strip()] = model.strip()
    return out


AGENT_MODELS = parse_agent_models(os.getenv("PORING_AGENT_MODELS", "groq_free:소라,김동식,조이서,오상식,이지안,오동백,권민우,최수연,정명석,리정혁"))


def agent_model(name: str) -> str:
    """이 인물 전용 모델 (없으면 "")."""
    return AGENT_MODELS.get(name or "", "")
_smart_blocked_day = ""  # 상위 모델이 429를 낸 날 - 그날은 더 안 부른다


def smart_used_today() -> int:
    try:
        return int(next(iter(llm_usage.by_day(1).values()), {}).get("sources", {}).get(SMART_SOURCE, 0))
    except Exception:  # noqa: BLE001
        return 0


def _post(model: str, system: str, user: str, temperature: float, timeout: int, extra: dict | None = None):
    return requests.post(
        API_URL,
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            **(extra or {}),
        },
        headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"},
        timeout=timeout,
    )


def _reason(res) -> str:
    """오류 응답에서 이유 한 줄 (LiteLLM이 감싼 제공사 메시지 - 키는 들어 있지 않다)."""
    try:
        body = res.json()
        msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else ""
        msg = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        msg = getattr(res, "text", "") or ""
    msg = " ".join(str(msg).split())
    return msg[:200]


def _call(model: str, source: str, system: str, user: str, temperature: float, timeout: int, tag: str,
          extra: dict | None = None):
    """한 번 부른다. 반환: (dict 또는 None, HTTP 상태 코드 - 연결 실패면 0)."""
    try:
        res = _post(model, system, user, temperature, timeout, extra)
    except requests.RequestException as e:
        llm_usage.record(source, ok=False)
        print(f"[경고] {tag} 호출 실패: {e}")
        return None, 0
    if res.status_code != 200:
        llm_usage.record(source, ok=False)
        print(f"[경고] {tag} 응답 오류: HTTP {res.status_code}" + (f" ({model})" if source != SOURCE else "")
              + (f" - {_reason(res)}" if _reason(res) else ""))
        return None, res.status_code
    try:
        body = res.json()
        usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
        llm_usage.record(source, True, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        data = json.loads(body["choices"][0]["message"]["content"])
        return (data if isinstance(data, dict) else None), 200
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {tag} 응답 형식 오류: {e}")
        return None, 200


def llm_json(system: str, user: str, *, temperature: float = 0.8, timeout: int = 45, tag: str = "LLM",
             smart: bool = False, agent: str = "") -> dict | None:
    """agent: 이 호출이 그 인물의 생각/말이면 이름 (인물 전용 모델이 있으면 그 모델로)."""
    if not (API_URL and LITELLM_MASTER_KEY):
        return None
    global _no_retry_until, _smart_blocked_day
    own = agent_model(agent)
    if own and own != LLM_MODEL and time.time() >= _agent_model_blocked.get(own, 0):
        extra = {"reasoning_effort": AGENT_MODEL_REASONING} if AGENT_MODEL_REASONING else None
        out, status = _call(own, f"{SOURCE}({own})", system, user, temperature, timeout, tag, extra)
        if out is not None:
            return out
        if status == 429:
            _agent_model_blocked[own] = time.time() + AGENT_MODEL_COOLDOWN_SEC
        elif status in (401, 403):
            _agent_model_blocked[own] = time.time() + AGENT_MODEL_FORBIDDEN_SEC
            print(f"[LLM] {own}이(가) 요청을 거부함(HTTP {status}) - {AGENT_MODEL_FORBIDDEN_SEC // 60}분 동안 기본 모델로")
        print(f"[LLM] {tag}: {own} 실패 - 기본 모델로 다시")
    today = time.strftime("%Y-%m-%d")
    if (smart and SMART_MODEL and SMART_MODEL != LLM_MODEL and _smart_blocked_day != today
            and smart_used_today() < SMART_DAILY_MAX):
        out, status = _call(SMART_MODEL, SMART_SOURCE, system, user, temperature, timeout, tag)
        if out is not None:
            return out
        if status == 429:
            _smart_blocked_day = today
            print("[LLM] 상위 모델 한도 초과 - 오늘은 기본 모델만 쓴다")
        print(f"[LLM] {tag}: 상위 모델 실패 - 기본 모델로 다시")
    out, status = _call(LLM_MODEL, SOURCE, system, user, temperature, timeout, tag)
    if out is None and status == 429 and RETRY_SEC > 0 and time.time() >= _no_retry_until:
        time.sleep(RETRY_SEC)
        out, status = _call(LLM_MODEL, SOURCE, system, user, temperature, timeout, f"{tag} (재시도)")
        if out is None and status == 429:
            _no_retry_until = time.time() + RETRY_COOLDOWN_SEC
            print(f"[LLM] 기본 모델 한도 초과가 계속됨 - {RETRY_COOLDOWN_SEC // 60}분간 재시도 없이 진행 (하루 한도를 다 썼을 수 있음)")
    return out
