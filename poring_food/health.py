"""
이상 감지 - 매시 회차가 끝나면 이번 회차를 훑어보고, 문제가 이어지면 카톡방에 원인을 짚어서 알린다.

관리자가 매일 들여다보지 않아도 되게: 봇이 꺼졌으면 회차 시작/완료 알림이 안 오는 것으로 알 수 있고,
돌긴 도는데 속이 고장 난 경우(LiteLLM 다운, 무료 키 한도/거부, 판단이 다 실패, 대화가 안 만들어짐)는 여기서 알린다.

| 원인            | 조건 (이번 회차)                                   | 몇 회차 이어지면 |
|-----------------|---------------------------------------------------|-----------------|
| 회차 실패        | 회차가 예외로 끝남                                  | 2               |
| LiteLLM 연결     | LLM 연결 실패 3건 이상                               | 2               |
| LiteLLM 오류     | HTTP 5xx 3건 이상                                   | 2               |
| 기본 모델 한도    | 기본 모델 429 3건 이상                              | 2               |
| 인물 전용 모델    | 401/403 (키/권한 거부) 1건 이상                       | 1               |
| 판단 실패        | 판단 3명 이상 중 절반 넘게 실패                       | 2               |
| 대화 없음        | 깨어 있는 시간(8~23시)에 올라갈 글이 하나도 안 생김     | 3               |
|                 | (위 LLM/판단 원인이 이미 보이면 따로 안 알림)           |                 |
| 회차 지연        | 회차가 SLOW_MIN분 넘게 걸림                          | 1               |

- 같은 원인은 REPEAT_HOURS시간에 한 번만 다시 알린다. 알렸던 문제가 풀리면 "✅ 회복"을 한 번 보낸다.
- 카톡방(PORING_KAKAO_ROOM_ID)에만 보낸다. PORING_HEALTH_ALERT=0이면 끔.
- 상위 모델 429는 하루 한도가 원래 작아서(기본 모델로 대신 감) 알리지 않는다.
저장: storage/poring_food/health.json {"streaks": {원인: 이어진 회차 수}, "alerted": {원인: 마지막 알림 시각}}
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import llm, outbox
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import LLM_MODEL, STATE_DIR

HEALTH_PATH = os.path.join(STATE_DIR, "health.json")
ENABLED = os.getenv("PORING_HEALTH_ALERT", "1") not in ("0", "false", "False", "")
REPEAT_HOURS = 6
SLOW_MIN = 45
WAKE_HOURS = range(8, 24)
NEED = {"tick_fail": 2, "litellm_down": 2, "litellm_error": 2, "base_quota": 2, "agent_forbidden": 1,
        "decide_fail": 2, "silent": 3, "slow": 1}
LABEL = {"tick_fail": "회차 실패", "litellm_down": "LiteLLM 연결", "litellm_error": "LiteLLM 오류",
         "base_quota": "기본 모델 한도", "agent_forbidden": "인물 전용 모델 거부", "decide_fail": "판단 실패",
         "silent": "대화 없음", "slow": "회차 지연"}


def _load() -> dict:
    try:
        with open(HEALTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(HEALTH_PATH), exist_ok=True)
    tmp = HEALTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HEALTH_PATH)


def begin() -> None:
    """[회차 시작] 이번 회차 집계를 비운다."""
    from . import agents
    llm.ERRORS.clear()
    llm.OK.clear()
    outbox.SCHEDULED.clear()
    agents.LAST_TICK.clear()


def _errors(prefix: tuple[str, ...], model: str | None = None, exclude: str | None = None) -> int:
    n = 0
    for key, c in llm.ERRORS.items():
        status, _, m = key.partition(":")
        if status.startswith(prefix) and (model is None or m == model) and (exclude is None or m != exclude):
            n += c
    return n


def problems(seconds: float, failed: bool, now: datetime) -> dict[str, str]:
    """이번 회차에서 보인 문제 {원인: 알림 문장}."""
    from . import agents
    out: dict[str, str] = {}
    if failed:
        out["tick_fail"] = "포링푸드 회차가 예외로 끝남 - 봇 로그 확인 필요 (journalctl -u discord_bot_v2)"
    down = _errors(("0",))
    if down >= 3:
        out["litellm_down"] = f"LiteLLM 연결 안 됨 (연결 실패 {down}건) - LiteLLM 컨테이너가 살아 있는지, config.yaml 오류로 재시작을 반복하는지 확인 필요"
    err5 = _errors(("5",))
    if err5 >= 3:
        out["litellm_error"] = f"LiteLLM이 오류를 돌려줌 (HTTP 5xx {err5}건) - LiteLLM 로그 확인 필요"
    quota = _errors(("429",), model=LLM_MODEL)
    if quota >= 3:
        out["base_quota"] = f"기본 모델({LLM_MODEL}) 한도 초과가 계속됨 (429 {quota}건) - 무료 키 하루 한도 소진 가능성, LiteLLM 키 묶음 확인 필요"
    own = sorted({m for k in llm.ERRORS for st, _, m in [k.partition(":")] if st in ("401", "403") and m != LLM_MODEL
                  and m != llm.SMART_MODEL})
    if own:
        out["agent_forbidden"] = (f"인물 전용 모델({', '.join(own)})이 키/권한을 거부함 (HTTP 401/403) - 제공사 콘솔에서 "
                                  "모델 권한·키 확인 필요 (그동안 기본 모델로 대신 돌림)")
    decided, ok = int(agents.LAST_TICK.get("decided", 0)), int(agents.LAST_TICK.get("ok", 0))
    if decided >= 3 and ok * 2 < decided:
        out["decide_fail"] = f"판단 실패가 많음 ({decided}명 중 {ok}명만 성공) - 대부분 '평소 루틴'으로 대신함, 위 LLM 상태 확인 필요"
    made = sum(outbox.SCHEDULED.get(k, 0) for k in ("agent", "discord"))
    explained = any(c in out for c in ("tick_fail", "litellm_down", "litellm_error", "base_quota", "decide_fail"))
    if now.hour in WAKE_HOURS and made == 0 and not explained:  # 원인이 이미 위에 나왔으면 따로 안 알린다
        out["silent"] = "대화/글이 하나도 안 만들어짐 - 판단이 전부 '가만히'거나 LLM이 응답을 못 하는 중"
    if seconds >= SLOW_MIN * 60:
        out["slow"] = f"회차가 {int(seconds) // 60}분 걸림 - 다음 회차와 겹칠 수 있음 (LLM 응답 지연/재시도 확인 필요)"
    return out


def check(seconds: float, failed: bool = False, now: datetime | None = None, send=None) -> str:
    """[회차 끝] 문제가 이어지면 카톡으로 알린다. 보낸 글을 돌려준다 (없으면 "")."""
    now = now or now_kst()
    found = problems(seconds, failed, now)
    data = _load()
    streaks, alerted = data.setdefault("streaks", {}), data.setdefault("alerted", {})
    alerts, recovered = [], []
    for cause in NEED:
        if cause in found:
            streaks[cause] = streaks.get(cause, 0) + 1
            last = alerted.get(cause)
            stale = not last or (now - datetime.fromisoformat(last)).total_seconds() >= REPEAT_HOURS * 3600
            if streaks[cause] >= NEED[cause] and stale:
                n = streaks[cause]
                alerts.append(f"- {found[cause]}" + (f" [{n}회차 연속]" if n > 1 else ""))
                alerted[cause] = now.isoformat(timespec="seconds")
        else:
            streaks.pop(cause, None)
            if alerted.pop(cause, None):
                recovered.append(LABEL[cause])
    _save(data)
    lines = []
    if alerts:
        lines.append(f"🚨 {now:%H:%M} 포링푸드 이상 감지\n" + "\n".join(alerts))
    if recovered:
        lines.append(f"✅ {now:%H:%M} 포링푸드 회복: {', '.join(recovered)}")
    text = "\n\n".join(lines)
    if text:
        print(f"[이상 감지] {text}")
        if ENABLED:
            if send is None:
                from . import notifier
                send = notifier.send_to_local_bot
            send(text)
    return text
