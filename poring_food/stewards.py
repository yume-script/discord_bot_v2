"""시스템 담당자 - 인물 한 명이 실제 프로그램 하나를 책임지고, 자기 시스템이 기능을 잃지 않게 다른 담당자와 아웅다웅 협상한다.

| 시스템 (실제)                              | 마을의 시설                     | 담당 (기본)  | 지키려는 것                                   |
|-------------------------------------------|--------------------------------|-------------|----------------------------------------------|
| 전체 조정                                   | 오아시스 마법도서관 관장실        | 애순이       | 목표 순서: 시청·독서 > 서버 95% > 자료 등록       |
| Plex 시청 (Plex Media Server)              | 별빛 환영극장 상영관              | 소라         | 지금 보는 손님이 버퍼링 없이                     |
| flaskfarm (plex_mate rclone 새로 고침, GDS) | 환영극장 이야기 배급소            | 일레이나     | 새 영상이 Plex에 빨리 등록                       |
| 북오아시스 (웹 서가 + MariaDB + 스캐너)      | 도서관 서가 감시탑               | 페른         | 책 열람이 빠르고, 새 책이 서가에 제때 등록         |
| bookoasis_assistant (웹툰/웹소설 다운로드)   | 도서관 두루마리 운반 정령          | 슈타르크     | 기다무 대여권을 놓치지 않고 최대한 받기             |
| YM_PLAYER (Plex/텔레그램/IPTV 재생)         | 환영극장 영사 수정구              | 프랑         | 텔레그램/IPTV 시청이 끊기지 않게                  |
| Tautulli (시청 집계)                        | 환영극장 관객 세는 수정구          | 크리스       | 관객 집계가 멈추지 않게                           |
| 결계 조절 (부하 단계)                        | 결계석                          | 슈나         | 부하 지수 95% 아래                                |
| LiteLLM                                    | 수비대 마나 중계탑               | 고브타       | 마을 사람들의 주문(LLM)이 막히지 않게               |
| vaultwarden                                | 마을 열쇠지기 탑                 | 소우에이     | 열쇠 금고가 늘 열려 있게                          |
| docker-warproxy                            | 성문 전령 초소                   | 가비루       | 바깥 전령이 오갈 수 있게                          |
| ClamAV (호스트 zeeps)                       | 방역관의 정화 의식               | 팔린         | 정화(바이러스 검사)를 거르지 않게                  |
| rclone zeeps_member 업로드 (스테이징)         | 왕도로 가는 두루마리 짐마차        | 카즈마       | 로컬에 쌓인 짐을 제때 왕도로                       |

- 담당은 .env PORING_STEWARDS="ff=일레이나,bookoasis=페른,..."로 바꾼다. 담당자가 교대(rotation)로 쉬는 중이면 같은 소속의
  다른 인물이 대신 맡는다(없으면 조정자).
- prompt_block(이름): 매시 판단 프롬프트에 "내가 맡은 시스템"과 실제 상태가 들어간다 - 그래서 담당자는 자기 시스템을
  걱정하고, 다른 담당자에게 따지거나 부탁한다.
- council(): 실제 결정을 내려야 할 때(기다무 시간대, 앱 업데이트) 관련 담당자들이 돌아가며 자기 시스템 입장을 말하고,
  조정자(애순이)가 목표 순서에 맞게 결론을 낸다. 결론은 JSON으로 받아 코드의 안전장치를 다시 통과해야 실제로 실행된다.
"""
from __future__ import annotations

import os

from . import loadshed
from ._log import pf_print as print  # print()를 봇 로그로
from .clock import now_kst

CHAIR = "애순이"

SYSTEMS = {
    "chair": {"label": "전체 조정", "facility": "도서관 관장실", "steward": CHAIR,
              "wants": "모든 시설이 최대한 살아 있게 조율하되 목표 순서(시청·독서 > 서버 95% > 자료 등록)를 지킨다"},
    "plex": {"label": "Plex 시청", "facility": "환영극장 상영관", "steward": "소라",
             "wants": "지금 영상을 보는 손님이 버퍼링 없이 끝까지 보게 한다 - 시청 중엔 다른 일이 양보해야 한다"},
    "ff": {"label": "flaskfarm (plex_mate rclone 새로 고침·GDS 감시)", "facility": "환영극장 이야기 배급소", "steward": "일레이나",
           "wants": "새 영상이 올라오면 rclone 새로 고침으로 Plex에 빨리 등록되게 한다 (새벽 정기 새로 고침 포함)"},
    "bookoasis": {"label": "북오아시스 (웹 서가·MariaDB·스캐너)", "facility": "도서관 서가 감시탑", "steward": "페른",
                  "wants": "책 열람이 빠르고, 새로 들어온 책이 스캐너로 제때 서가(DB)에 등록되게 한다"},
    "assistant": {"label": "bookoasis_assistant (웹툰/웹소설 다운로드)", "facility": "도서관 두루마리 운반 정령",
                  "steward": "슈타르크",
                  "wants": "기다무 대여권을 놓치지 않고 웹툰/웹소설을 최대한 많이 받아 북오아시스에 등록 요청한다"},
    "ym_player": {"label": "YM_PLAYER (Plex/텔레그램/IPTV 재생)", "facility": "환영극장 영사 수정구", "steward": "프랑",
                  "wants": "텔레그램·IPTV·라디오 중계와 Plex 재생이 끊기지 않게 한다"},
    "tautulli": {"label": "Tautulli (시청 집계)", "facility": "환영극장 관객 세는 수정구", "steward": "크리스",
                 "wants": "관객 집계와 재생 알림이 멈추지 않게 한다"},
    "loadshed": {"label": "결계 조절 (부하 단계)", "facility": "결계석", "steward": "슈나",
                 "wants": "부하 지수를 95% 아래로 지키고, 꼭 필요한 만큼만 늦추고 바로 되돌린다"},
    "litellm": {"label": "LiteLLM", "facility": "수비대 마나 중계탑", "steward": "고브타",
                "wants": "마을 사람들의 주문(LLM 요청)이 막히지 않게 한다"},
    "vaultwarden": {"label": "vaultwarden", "facility": "마을 열쇠지기 탑", "steward": "소우에이",
                    "wants": "열쇠 금고가 늘 열려 있게 한다 (절대 끄지 않음)"},
    "warproxy": {"label": "docker-warproxy", "facility": "성문 전령 초소", "steward": "가비루",
                 "wants": "바깥으로 나가는 전령이 필요할 때 오갈 수 있게 한다 (놀 때는 쉬어도 됨)"},
    "clamav": {"label": "ClamAV (호스트 바이러스 검사)", "facility": "방역관의 정화 의식", "steward": "팔린",
               "wants": "정화 의식(정기 검사)을 너무 오래 거르지 않게 한다"},
    "upload": {"label": "rclone zeeps_member 업로드 (로컬 스테이징)", "facility": "왕도로 가는 두루마리 짐마차",
               "steward": "카즈마", "wants": "로컬에 쌓인 짐을 제때 왕도(드라이브)로 올려 창고가 넘치지 않게 한다"},
}

# loadshed/facilities가 쓰는 실제 이름 -> 시스템
NAME_TO_SYSTEM = {"bookoasis_assistant.service": "assistant", "upload": "upload", "docker-warproxy": "warproxy",
                  "clamav": "clamav", "clamav-risk-scan.service": "clamav", "clamav-deep-scan.service": "clamav",
                  "bookoasis": "bookoasis", "bookoasis_mariadb": "bookoasis", "bookoasis_redis": "bookoasis",
                  "tautulli": "tautulli", "flaskfarm": "ff", "litellm-proxy": "litellm", "litellm-db": "litellm",
                  "vaultwarden": "vaultwarden", "결계 경보": "loadshed"}


def _overrides() -> dict[str, str]:
    out = {}
    for part in os.getenv("PORING_STEWARDS", "").split(","):
        if "=" in part:
            k, v = (x.strip() for x in part.split("=", 1))
            if k in SYSTEMS and v:
                out[k] = v
    return out


def steward(key: str, agents: list[dict] | None = None) -> str:
    """지금 그 시스템을 맡은 사람 (담당자가 쉬는 중이면 같은 소속의 다른 인물, 없으면 조정자)."""
    want = _overrides().get(key) or SYSTEMS[key]["steward"]
    if agents is None:
        try:
            from .agents import load_agents
            agents = load_agents()
        except Exception:  # noqa: BLE001
            return want
    names = {a["name"]: a for a in agents}
    if want in names:
        return want
    try:
        from .agents import _base_agents
        company = next((a.get("company") for a in _base_agents() if a["name"] == want), "")
    except Exception:  # noqa: BLE001
        company = ""
    taken = {_overrides().get(k) or s["steward"] for k, s in SYSTEMS.items()}
    alt = [a["name"] for a in agents if company and a.get("company") == company and a["name"] not in taken]
    alt = alt or [a["name"] for a in agents if company and a.get("company") == company]
    return alt[0] if alt else (CHAIR if CHAIR in names else want)


def owned_by(name: str, agents: list[dict] | None = None) -> list[str]:
    return [k for k in SYSTEMS if k != "chair" and steward(k, agents) == name]


def for_name(real: str) -> str | None:
    """loadshed/facilities의 실제 이름 -> 시스템 키"""
    return NAME_TO_SYSTEM.get(real)


# ===================================================================== 상태 (숫자만, 제목·계정 없음)
def status_line(key: str, data: dict | None = None) -> str:
    data = data if data is not None else loadshed._load()
    hosts = data.get("hosts") or {}
    plex01 = hosts.get("plex01") or {}
    if key == "plex":
        p = plex01.get("playing")
        return ("지금 재생 " + ("모름" if p is None else f"{p}개") + (" · 버벅임 있음" if plex01.get("struggling") else ""))
    if key == "loadshed":
        return " · ".join(f"{h} {int(st.get('stage', 0))}단계 지수 {st.get('last_cpu', '?')}%" for h, st in hosts.items())
    if key in ("assistant", "upload", "warproxy"):
        name = {"assistant": "bookoasis_assistant.service", "upload": "upload", "warproxy": "docker-warproxy"}[key]
        slowed = [t for st in hosts.values() for t in (st.get("throttled") or {}).values() if t.get("name") == name]
        shed = [h for h, st in hosts.items() if name in (st.get("shed") or {})]
        bits = (["지금 결계 조절이 늦춰 둠"] if slowed else []) + (["지금 결계 조절이 쉬게 함"] if shed else [])
        if key == "assistant":
            try:
                from . import waitfree
                last = (waitfree._state().get("applied") or [])[-1:]
                if last:
                    bits.append(f"기다무 시간대 {last[0]['start']:02d}~{last[0]['end']:02d}시")
            except Exception:  # noqa: BLE001
                pass
        return " · ".join(bits) or "평소대로 일하는 중"
    if key in ("bookoasis", "ym_player"):
        try:
            from . import updater
            a = updater._state().get(key) or {}
            pend = a.get("pending") or {}
            return ("새 버전 대기 중 (" + ("받기로 함" if pend.get("update") else "미룸") + ")") if pend else "평소대로"
        except Exception:  # noqa: BLE001
            return "평소대로"
    return ""


def prompt_block(name: str, agents: list[dict] | None = None) -> str:
    keys = owned_by(name, agents)
    if name == steward("chair", agents):
        keys = ["chair"] + keys
    if not keys:
        return ""
    data = loadshed._load()
    lines = [f"- {SYSTEMS[k]['facility']} (실제: {SYSTEMS[k]['label']}) - 내가 지킬 것: {SYSTEMS[k]['wants']}"
             + (f" / 지금: {s}" if (s := status_line(k, data)) else "") for k in keys]
    return ("[내가 맡은 마을 시설 - 실제 서버 프로그램]\n" + "\n".join(lines) + "\n"
            "내 시설이 기능을 잃지 않게 챙기고, 다른 시설 때문에 내 시설이 밀리면 그 담당자에게 따지거나 부탁하고, "
            "내 시설이 남에게 짐이 되면 양보할 줄도 안다. 다만 마을의 순서(시청·독서 > 서버 안정 > 자료 등록)는 넘지 않는다.")


# ===================================================================== 조율 회의
def council(topic: str, facts: str, parties: list[str], decision_spec: str, *, chair_key: str = "chair",
            rounds: int = 1, speak=None, conclude=None, post=None) -> dict:
    """담당자들이 돌아가며 자기 시스템 입장을 말하고(rounds바퀴) 조정자가 결론(JSON)을 낸다.
    parties: 시스템 키 (말하는 순서). decision_spec: 조정자에게 줄 JSON 형식 설명.
    반환 {"decision": dict, "lines": [{"speaker", "system", "line"}], "chair": 이름}"""
    from . import agents as ag, chronicle, memory
    agents = ag.load_agents()
    by_name = {a["name"]: a for a in agents}
    now = now_kst()
    chair = steward(chair_key, agents)
    seats = [(k, steward(k, agents)) for k in dict.fromkeys(parties) if k in SYSTEMS]
    seats = [(k, n) for k, n in seats if n in by_name and n != chair]
    if post is None:
        def post(agent_name: str, text: str) -> None:
            a = by_name.get(agent_name)
            if a:
                ag._post(a, text)
            else:
                ag._post_as(agent_name, text)
    head = f"-# 🛡️ 시설 조율 · {now:%H:%M} · {topic}"
    lines: list[dict] = []

    def _speak(key: str, name: str) -> str:
        a = by_name[name]
        talk = "\n".join(f"{t['speaker']}({SYSTEMS[t['system']]['facility']}): {t['line']}" for t in lines) or "(아직 없음)"
        asleep = ag._asleep(a, now.hour)
        user = (f"[시설 조율] {topic}\n[실제 상황 - 숫자]\n{facts}\n\n[내가 맡은 시설] {SYSTEMS[key]['facility']} "
                f"(실제: {SYSTEMS[key]['label']}) - {SYSTEMS[key]['wants']}\n[지금까지 오간 말]\n{talk}\n\n"
                + ("나는 지금 자는 중이라 미리 남겨 둔 메모로 의견을 낸다. " if asleep else "")
                + "내 시설 입장에서 할 말을 1~2문장으로 해라. 내 시설에 손해면 따지거나 조건을 걸고, 남의 말에 반박해도 된다. "
                "숫자 근거를 하나는 넣고, 마을 순서(시청·독서 > 서버 안정 > 자료 등록)는 인정한다. 작품 제목·계정은 말하지 않는다.\n"
                + ag._voice(a) + '\nJSON으로만: {"say": "할 말"}')
        if speak is not None:
            return speak(key, name, user)
        from .llm import llm_json
        out = llm_json(ag._system(a), user, temperature=0.8, timeout=45, tag=f"시설 조율 {name}", agent=name) or {}
        return str(out.get("say") or "").strip()[:280]

    for _ in range(max(1, rounds)):
        for key, name in seats:
            say = _speak(key, name)
            if not say:
                continue
            note = "📝 " if ag._asleep(by_name[name], now.hour) else ""
            lines.append({"speaker": name, "system": key, "line": say})
            post(name, f"{head}\n{note}{say}")
    talk = "\n".join(f"{t['speaker']}({SYSTEMS[t['system']]['label']}): {t['line']}" for t in lines)
    chair_user = (f"[시설 조율 - 내가 정한다] {topic}\n[실제 상황]\n{facts}\n[담당자들 의견]\n{talk or '(의견 없음)'}\n\n"
                  "목표 순서(1 시청·독서가 불편 없게 2 서버 부하 95% 아래 3 자료를 최대한 DB에)에 맞게, 담당자들 말 중 "
                  "근거가 맞는 쪽을 골라 결론을 낸다. 한쪽만 이기게 하지 말고 서로 조금씩 양보하는 안이 있으면 그걸로.\n"
                  f"{decision_spec}\n'say'에는 내 말투로 결론을 알리는 1~2문장(누구 말을 받아들였는지)을 넣는다.")
    if conclude is not None:
        decision = conclude(chair_user) or {}
    else:
        from .llm import llm_json
        ca = by_name.get(chair)
        decision = llm_json(ag._system(ca) if ca else "너는 마을 시설 조정자다.", chair_user, temperature=0.3,
                            timeout=60, tag="시설 조율 결론", agent=chair, smart=True) or {}
    say = str(decision.get("say") or "").strip()[:300]
    if say:
        post(chair, f"{head}\n**{say}**")
    summary = f"{topic}: {say or decision.get('reason') or '결론 없음'}"
    try:
        chronicle.record_event("조율", topic, summary, [chair] + [n for _, n in seats])
        for n in [chair] + [n for _, n in seats]:
            memory.remember(n, f"시설 조율 '{topic}'에 참여 - {say or decision.get('reason', '')}", 5,
                            [m for m in [chair] + [x for _, x in seats] if m != n], "조율")
    except Exception as e:  # noqa: BLE001
        print(f"[시설 조율] 기록 실패: {e}")
    return {"decision": decision, "lines": lines, "chair": chair}
