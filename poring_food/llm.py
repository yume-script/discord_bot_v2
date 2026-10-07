"""포링푸드 공용 LLM 호출 - JSON 응답만 받는다 (world.py, agents.py)."""
from __future__ import annotations

import json

import requests

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import API_URL, LITELLM_MASTER_KEY, LLM_MODEL


def llm_json(system: str, user: str, *, temperature: float = 0.8, timeout: int = 45, tag: str = "LLM") -> dict | None:
    if not (API_URL and LITELLM_MASTER_KEY):
        return None
    try:
        res = requests.post(
            API_URL,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"},
            timeout=timeout,
        )
        if res.status_code != 200:
            print(f"[경고] {tag} 응답 오류: HTTP {res.status_code}")
            return None
        data = json.loads(res.json()["choices"][0]["message"]["content"])
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {tag} 호출 실패: {e}")
        return None
