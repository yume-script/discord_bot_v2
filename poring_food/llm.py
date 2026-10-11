"""
포링푸드 공용 LLM 호출 - JSON 응답만 받는다 (world.py, agents.py, chronicle.py, story.py ...).

- 기본 모델(PORING_LLM_MODEL): 매시 판단/대화 한마디처럼 자주 부르는 것.
- 상위 모델(PORING_LLM_MODEL_SMART, smart=True): 모임 결론/프로젝트 계획/연대기 요약/세계 엔진 계획/작가 회의/
  주간 실적 발표처럼 하루 몇 번뿐인 중요한 판단. 무료 키는 하루 요청 수가 아주 적어서(예: 20회) 하루
  PORING_LLM_SMART_DAILY_MAX회까지만 쓰고, 넘거나 실패하면(한도 초과 429 포함) 바로 기본 모델로 다시 부른다.
- 기본 모델이 429(분당 한도)면 PORING_LLM_RETRY_SEC초 쉬고 한 번만 더 부른다 - 매시 회차에 호출이 몰려
  분당 한도에 걸린 판단이 그냥 "평소 루틴"으로 떨어지지 않게.
- 요청은 모두 ai/llm_usage.py가 센다 (출처 "포링푸드", 상위 모델은 "포링푸드(상위)").
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
SOURCE = "포링푸드"
SMART_SOURCE = "포링푸드(상위)"


def smart_used_today() -> int:
    try:
        return int(next(iter(llm_usage.by_day(1).values()), {}).get("sources", {}).get(SMART_SOURCE, 0))
    except Exception:  # noqa: BLE001
        return 0


def _post(model: str, system: str, user: str, temperature: float, timeout: int):
    return requests.post(
        API_URL,
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"},
        timeout=timeout,
    )


def _call(model: str, source: str, system: str, user: str, temperature: float, timeout: int, tag: str):
    """한 번 부른다. 반환: (dict 또는 None, HTTP 상태 코드 - 연결 실패면 0)."""
    try:
        res = _post(model, system, user, temperature, timeout)
    except requests.RequestException as e:
        llm_usage.record(source, ok=False)
        print(f"[경고] {tag} 호출 실패: {e}")
        return None, 0
    if res.status_code != 200:
        llm_usage.record(source, ok=False)
        print(f"[경고] {tag} 응답 오류: HTTP {res.status_code}" + (f" ({model})" if source == SMART_SOURCE else ""))
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
             smart: bool = False) -> dict | None:
    if not (API_URL and LITELLM_MASTER_KEY):
        return None
    if smart and SMART_MODEL and SMART_MODEL != LLM_MODEL and smart_used_today() < SMART_DAILY_MAX:
        out, _status = _call(SMART_MODEL, SMART_SOURCE, system, user, temperature, timeout, tag)
        if out is not None:
            return out
        print(f"[LLM] {tag}: 상위 모델 실패 - 기본 모델로 다시")
    out, status = _call(LLM_MODEL, SOURCE, system, user, temperature, timeout, tag)
    if out is None and status == 429 and RETRY_SEC > 0:
        time.sleep(RETRY_SEC)
        out, _status = _call(LLM_MODEL, SOURCE, system, user, temperature, timeout, f"{tag} (재시도)")
    return out
