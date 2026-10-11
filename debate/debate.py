#!/usr/bin/env python3
"""
토론 시뮬레이터 - 정해 둔 인물들이 한 주제로 횟수 제한 없이 토론한다 (디스코드 봇과 별개인 독립 스크립트).

    python3 debate.py debate.json                 # Ctrl+C로 멈출 때까지 계속
    python3 debate.py debate.json --turns 30      # 30마디까지만
    python3 debate.py debate.json --resume        # 저장된 토론을 이어서
    python3 debate.py debate.json --topic "주 4일제 도입" --delay 3

- LLM: OpenAI 호환 /v1/chat/completions (LiteLLM 프록시 등). 주소/키는 환경변수 DEBATE_BASE_URL/DEBATE_API_KEY
  (없으면 LITELLM_BASE_URL/LITELLM_API_KEY, 같은 폴더 .env도 읽는다). 인물마다 다른 모델을 쓸 수 있다.
- 인물마다 자기 LLM 턴을 갖는다: 각자 자기 페르소나/입장 + 지금까지의 요약 + 최근 발언만 보고 다음 말을 정한다.
- 횟수 제한이 없으니 문맥이 끝없이 길어지지 않게: 최근 RECENT개 발언만 원문으로, 그 이전은 요약(summary)으로 압축한다.
  요약은 summary_every마디마다 사회자 모델이 갱신한다.
- 같은 말이 맴돌지 않게: 사회자가 moderator_every마디마다 새 쟁점/반론 거리를 던진다(끄려면 0).
- 다음 발언자: 직전 발언에서 이름이 불린 사람이 먼저, 아니면 가장 오래 말하지 않은 사람 (같은 사람이 연달아 말하지 않음).
- 기록: transcript(.md, 사람이 읽는 것)와 state(.json, 이어하기용)를 매 발언마다 저장한다. 멈춰도 그때까지 다 남는다.
- 표준 라이브러리만 쓴다 (설치할 것 없음). Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

RECENT = 12            # 원문 그대로 보여 주는 최근 발언 수
RETRIES = 4            # LLM 호출 재시도 (429/5xx/연결 오류)
MAX_FAILS = 5          # 재시도 끝에도 실패한 게 연달아 이만큼이면 멈춘다 (나중에 --resume)
COLORS = ["\033[96m", "\033[93m", "\033[92m", "\033[95m", "\033[94m", "\033[91m"]
RESET, DIM = "\033[0m", "\033[2m"


# ===================================================================== 설정 / LLM
def load_dotenv(path: str) -> None:
    """KEY=VALUE 줄만 읽어 환경변수에 없는 것만 채운다 (python-dotenv 없이)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                os.environ.setdefault(m.group(1), m.group(2).strip("'\""))


def endpoint() -> tuple[str, str]:
    base = (os.getenv("DEBATE_BASE_URL") or os.getenv("LITELLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("DEBATE_API_KEY") or os.getenv("LITELLM_API_KEY") or "").strip()
    if not base:
        sys.exit("LLM 주소가 없습니다: DEBATE_BASE_URL (예: http://localhost:4000) 을 설정하세요.")
    base = re.sub(r"/v1$", "", base)
    return f"{base}/v1/chat/completions", key


def chat(model: str, system: str, user: str, temperature: float = 0.9, max_tokens: int = 600,
         extra: dict | None = None, timeout: int = 90) -> str:
    """한 번 부르고 답 텍스트를 돌려준다. 429/5xx/연결 오류는 기다렸다가 다시 (RETRIES번)."""
    url, key = endpoint()
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], **(extra or {})}
    data = json.dumps(body).encode("utf-8")
    last = ""
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                              **({"Authorization": f"Bearer {key}"} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                out = json.loads(res.read().decode("utf-8"))
            content = out["choices"][0]["message"].get("content") or ""
            if isinstance(content, list):  # 블록 리스트로 오는 모델 대비
                content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            return content.strip()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
            if e.code not in (408, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last = f"응답 형식 오류: {e}"
            break
        wait = min(60, 5 * 2 ** attempt)
        print(f"{DIM}  (LLM 오류 - {wait}초 뒤 다시: {last}){RESET}", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(last or "LLM 호출 실패")


# ===================================================================== 토론 로직
def pick_speaker(participants: list[dict], transcript: list[dict]) -> dict:
    """직전 발언에서 이름이 불린 사람(직전 화자 제외) > 가장 오래 말하지 않은 사람 > 목록 순서."""
    last = transcript[-1] if transcript else None
    cands = [p for p in participants if not last or p["name"] != last["speaker"]] or participants
    if last:
        called = [p for p in cands if p["name"] in last["text"]]
        if called:
            return called[0]
    last_turn = {p["name"]: -1 for p in participants}
    for i, t in enumerate(transcript):
        if t["speaker"] in last_turn:
            last_turn[t["speaker"]] = i
    return min(cands, key=lambda p: (last_turn[p["name"]], participants.index(p)))


def system_prompt(p: dict, cfg: dict) -> str:
    others = ", ".join(o["name"] for o in cfg["participants"] if o is not p)
    return (
        f"너는 '{p['name']}'이다. 지금 '{cfg['topic']}'을(를) 두고 {others}와(과) 토론 중이다.\n"
        f"[나] {p.get('persona', '')}\n[내 입장] {p.get('stance', '자유롭게 판단')}\n"
        + (f"[말투] {p['speech']}\n" if p.get("speech") else "")
        + "[토론 원칙]\n"
        "- 이 사람 자체로 말한다. 착하고 무난한 AI 말투, 사회자 같은 정리/요약 말투 금지.\n"
        "- 직전 발언에 직접 반응해라: 상대 논리의 약한 곳을 짚고, 근거나 구체적인 예를 들어라.\n"
        "- 이미 나온 말을 되풀이하지 말고 새 근거/관점/질문을 보태라. 설득되면 일부 인정해도 된다.\n"
        "- 다른 사람 이름을 불러 질문하거나 반박해도 된다.\n"
        f"- {cfg.get('language', '한국어')}로, {cfg.get('turn_length', '2~4문장')}. 내 말만 써라 (이름표, 따옴표, 지문 없이)."
    )


def context_block(state: dict) -> str:
    recent = state["transcript"][-RECENT:]
    lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in recent) or "(아직 아무도 말하지 않음 - 네가 첫 발언이다)"
    parts = [f"[토론 주제] {state['topic']}"]
    if state.get("summary"):
        parts.append(f"[지금까지의 흐름 요약]\n{state['summary']}")
    parts.append(f"[최근 발언]\n{lines}")
    return "\n\n".join(parts)


def clean_line(text: str, name: str) -> str:
    text = text.strip().strip('"“”').strip()
    text = re.sub(rf"^\s*\**{re.escape(name)}\**\s*[:：]\s*", "", text)  # 모델이 붙인 이름표 제거
    return text


def speak(p: dict, state: dict, cfg: dict) -> str:
    user = context_block(state) + f"\n\n{p['name']}로서 다음 발언을 해라."
    extra = {"reasoning_effort": p["reasoning_effort"]} if p.get("reasoning_effort") else None
    text = chat(p.get("model") or cfg["model"], system_prompt(p, cfg), user,
                temperature=float(p.get("temperature", cfg.get("temperature", 0.9))), extra=extra)
    return clean_line(text, p["name"])


def summarize(state: dict, cfg: dict) -> str:
    """요약 갱신 - 최근 RECENT개보다 앞선 발언들을 기존 요약에 합친다."""
    old = state["transcript"][:-RECENT]
    if not old:
        return state.get("summary", "")
    start = state.get("summarized_upto", 0)
    new = old[start:]
    if not new:
        return state.get("summary", "")
    body = "\n".join(f"{t['speaker']}: {t['text']}" for t in new)
    text = chat(cfg.get("moderator_model") or cfg["model"],
                "너는 토론 기록자다. 지어내지 말고 실제로 나온 말만 정리한다.",
                f"[주제] {state['topic']}\n[기존 요약]\n{state.get('summary') or '(없음)'}\n\n[새로 나온 발언]\n{body}\n\n"
                "기존 요약에 새 발언을 합쳐서 갱신해라. 사람별 입장과 핵심 근거, 합의된 점, 아직 맞서는 쟁점, "
                "이미 충분히 나온 논점(되풀이 금지)을 12줄 이내로.",
                temperature=0.3, max_tokens=900)
    state["summarized_upto"] = len(old)
    return text


def final_summary(state: dict, cfg: dict) -> str:
    """끝날 때 전체 정리 - 지금까지의 요약 + 아직 요약에 안 들어간 발언."""
    rest = state["transcript"][state.get("summarized_upto", 0):]
    body = "\n".join(f"{t['speaker']}: {t['text']}" for t in rest)
    return chat(cfg.get("moderator_model") or cfg["model"],
                "너는 토론 기록자다. 지어내지 말고 실제로 나온 말만 정리한다.",
                f"[주제] {state['topic']}\n[앞부분 요약]\n{state.get('summary') or '(없음)'}\n\n[이후 발언]\n{body}\n\n"
                "토론 전체를 정리해라: 사람별 최종 입장과 핵심 근거, 합의된 점, 끝까지 맞선 쟁점, 가장 설득력 있던 논점.",
                temperature=0.3, max_tokens=1200)


def moderate(state: dict, cfg: dict) -> str:
    return clean_line(chat(
        cfg.get("moderator_model") or cfg["model"],
        f"너는 '{cfg['topic']}' 토론의 사회자다. 편들지 않는다. 토론이 맴돌지 않게 새 쟁점을 던진다.",
        context_block(state) + "\n\n지금까지 덜 다뤄진 쟁점이나 구체적인 사례/반례 하나를 골라, 참가자 중 한 명을 이름으로 "
        "지목해 질문해라. 1~2문장. 사회자 말만 써라.", temperature=0.7, max_tokens=300), "사회자")


# ===================================================================== 저장 / 출력
def save(state: dict, paths: dict) -> None:
    tmp = paths["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, paths["state"])


def append_md(paths: dict, text: str) -> None:
    with open(paths["transcript"], "a", encoding="utf-8") as f:
        f.write(text + "\n")


def show(name: str, text: str, color: str) -> None:
    print(f"\n{color}▶ {name}{RESET}\n{text}", flush=True)


def color_of(name: str, cfg: dict) -> str:
    names = [p["name"] for p in cfg["participants"]]
    return COLORS[names.index(name) % len(COLORS)] if name in names else DIM


def validate(cfg: dict) -> None:
    if not cfg.get("topic"):
        sys.exit("설정에 topic(토론 주제)이 없습니다.")
    if len(cfg.get("participants") or []) < 2:
        sys.exit("participants(참가자)는 2명 이상이어야 합니다.")
    names = [p.get("name") for p in cfg["participants"]]
    if not all(names) or len(set(names)) != len(names):
        sys.exit("참가자 name이 비었거나 겹칩니다.")
    if not cfg.get("model") and not all(p.get("model") for p in cfg["participants"]):
        sys.exit("model(기본 모델)을 정하거나 참가자마다 model을 정하세요.")


def run(cfg: dict, paths: dict, max_turns: int, delay: float, resume: bool, wrap_up: bool) -> dict:
    validate(cfg)
    if resume and os.path.exists(paths["state"]):
        with open(paths["state"], encoding="utf-8") as f:
            state = json.load(f)
        print(f"{DIM}이어하기: {len(state['transcript'])}마디부터{RESET}")
    else:
        state = {"topic": cfg["topic"], "started": datetime.now().isoformat(timespec="seconds"),
                 "summary": "", "summarized_upto": 0, "transcript": []}
        with open(paths["transcript"], "w", encoding="utf-8") as f:
            f.write(f"# 토론: {cfg['topic']}\n\n- 시작: {state['started']}\n")
            for p in cfg["participants"]:
                f.write(f"- **{p['name']}** ({p.get('model') or cfg['model']}): {p.get('stance', '')}\n")
            f.write("\n---\n")
        save(state, paths)
    print(f"\n=== 토론 주제: {state['topic']} ===  (Ctrl+C로 멈춤)")
    summary_every = int(cfg.get("summary_every", 8))
    moderator_every = int(cfg.get("moderator_every", 10))
    done = fails = 0
    try:
        while not max_turns or done < max_turns:
            try:
                done += step(state, cfg, paths, summary_every, moderator_every)
                fails = 0
            except RuntimeError as e:  # LLM이 재시도 끝에도 실패 - 잠깐 쉬고 계속 (연속 MAX_FAILS번이면 멈춤)
                fails += 1
                print(f"{DIM}(LLM 실패 {fails}/{MAX_FAILS}: {e}){RESET}", file=sys.stderr)
                if fails >= MAX_FAILS:
                    print("LLM이 계속 실패해서 멈춥니다. 나중에 --resume으로 이어하세요.")
                    break
                time.sleep(60)
                continue
            if delay:
                time.sleep(delay)
    except KeyboardInterrupt:
        print(f"\n{DIM}(멈춤 - 지금까지 {len(state['transcript'])}마디 저장됨){RESET}")
    if wrap_up and state["transcript"]:
        try:
            final = final_summary(state, cfg)
            append_md(paths, f"\n---\n## 정리 ({len(state['transcript'])}마디)\n\n{final}\n")
            print(f"\n=== 정리 ===\n{final}")
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"{DIM}(정리 생략: {e}){RESET}")
    print(f"\n기록: {paths['transcript']}\n이어하기: python3 {os.path.basename(sys.argv[0] or 'debate.py')} <설정> --resume")
    return state


def step(state: dict, cfg: dict, paths: dict, summary_every: int, moderator_every: int) -> int:
    """한 마디 (필요하면 그 앞에 사회자 질문). 반환: 참가자가 말한 수 (0 또는 1)."""
    n = len(state["transcript"]) + 1
    if moderator_every and n > 1 and (n - 1) % moderator_every == 0 and state["transcript"][-1]["speaker"] != "사회자":
        q = moderate(state, cfg)
        state["transcript"].append({"n": n, "speaker": "사회자", "text": q, "at": datetime.now().isoformat(timespec="seconds")})
        show("사회자", q, DIM)
        append_md(paths, f"\n> **사회자**: {q}\n")
        save(state, paths)
        n += 1
    p = pick_speaker(cfg["participants"], state["transcript"])
    text = speak(p, state, cfg)
    if not text:
        return 0
    state["transcript"].append({"n": n, "speaker": p["name"], "text": text, "at": datetime.now().isoformat(timespec="seconds")})
    show(f"{p['name']} ({n})", text, color_of(p["name"], cfg))
    append_md(paths, f"\n**{p['name']}** ({n}): {text}\n")
    if summary_every and len(state["transcript"]) - RECENT - state.get("summarized_upto", 0) >= summary_every:
        state["summary"] = summarize(state, cfg)
    save(state, paths)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="정해 둔 인물들이 한 주제로 횟수 제한 없이 토론한다")
    ap.add_argument("config", help="설정 JSON (debate.example.json 참고)")
    ap.add_argument("--topic", help="설정의 주제 대신 이 주제로")
    ap.add_argument("--turns", type=int, default=0, help="이만큼 말하면 멈춤 (0 = 제한 없음, 기본)")
    ap.add_argument("--delay", type=float, default=None, help="발언 사이 쉬는 초 (무료 키 분당 한도 대비)")
    ap.add_argument("--resume", action="store_true", help="저장된 토론을 이어서")
    ap.add_argument("--no-summary", action="store_true", help="끝날 때 정리 생략")
    ap.add_argument("--out", help="기록 폴더 (기본: 설정 파일 옆 runs/)")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(here, ".env"))
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    if args.topic:
        cfg["topic"] = args.topic
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.config)), "runs")
    os.makedirs(out, exist_ok=True)
    slug = re.sub(r"[^\w가-힣]+", "_", cfg.get("topic", "debate")).strip("_")[:40] or "debate"
    paths = {"transcript": os.path.join(out, f"{slug}.md"), "state": os.path.join(out, f"{slug}.json")}
    delay = args.delay if args.delay is not None else float(cfg.get("delay", 2))
    run(cfg, paths, args.turns, delay, args.resume, not args.no_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
