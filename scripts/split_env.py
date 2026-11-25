"""
.env를 둘로 나눈다 - 기본(봇·서버 관리) 변수는 .env, 포링 이야기(오아시스 거리 인물·장면·취미…) 변수는 poring.env.

    cd /mnt/discord_bot_v2 && venv/bin/python scripts/split_env.py          # 무엇이 어디로 가는지만 보여 준다 (파일 안 바꿈)
    venv/bin/python scripts/split_env.py --apply                           # 나눠서 쓴다 (둘 다 백업 남김)
    venv/bin/python scripts/split_env.py --check                           # 새 변수가 있으면 종료 코드 1

어떻게 나누나 (손으로 목록을 관리하지 않게 코드를 훑어서 정한다):
- 변수를 읽는 코드(os.getenv 등)를 저장소에서 찾아, 그 변수를 읽는 곳이 모두 포링 이야기 모듈(poring_food/ 중 서버 관리·공용 모듈을 뺀 것)이면 poring.env,
  하나라도 봇·서버 관리 코드가 읽으면 .env (예: LLM 모델, 서버 조절, 응답 확인, 채우기, 업데이트, 다운로드 에이전트, 운영자).
- 코드가 읽지 않는 변수(직접 넣은 것 등)는 이름이 PORING_로 시작하면 poring.env, 아니면 .env.
- 파일 모양은 env.example을 따른다 (순서·설명 주석). 설명 덩어리(빈 줄로 나뉜 것)에 두 종류가 섞여 있으면 설명은 양쪽에 같이 둔다.
- 값은 지금 .env와 poring.env에 있던 값 그대로 (같은 변수가 둘 다 있으면 .env 값 - 봇도 .env를 먼저 읽는다).
  env.example에 새로 생긴 변수는 기본값으로 알맞은 파일에 들어간다 (sync_env.py와 같음).
- 봇은 .env 다음에 poring.env를 읽는다 (config/settings.py). 한 번 나눈 뒤에는 배포 때 sync_env.py가 이 스크립트로 둘 다 맞춘다.
- 화면에는 변수 이름만 보여 준다 (값은 출력하지 않는다).
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_env import _KEY_LINE, parse_value, read_env  # noqa: E402

POR_ENV = "poring.env"
# poring_food/ 안이지만 봇·서버 관리가 쓰거나 같이 쓰는 모듈 (이 모듈들이 읽는 변수는 .env에 남긴다)
SHARED_MODULES = {"loadshed", "loadcause", "uptime", "fill", "updater", "ops_agent", "ops_digest", "gdw_queue", "boa_agent", "tg_upload",
                  "waitfree", "config", "clock", "_log", "llm", "runtime", "notifier", "health", "facilities", "plex_api",
                  "mcp_signals", "kpi", "metrics"}
_READ = re.compile(r"(?:getenv|environ\.get|environ\[|_int|_bool|_float|_str|_names|_url|_hours|_base)\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']")
_COMMENTED_KEY = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=")


def usage(root: str = BASE_DIR) -> dict[str, set[str]]:
    """변수 이름 -> 그 변수를 읽는 파일들 (tests·venv 제외)"""
    out: dict[str, set[str]] = {}
    for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel.startswith(("tests/", "venv/", ".venv/")):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for name in _READ.findall(text):
            out.setdefault(name, set()).add(rel)
    return out


def _story_file(rel: str) -> bool:
    return rel.startswith("poring_food/") and os.path.splitext(os.path.basename(rel))[0] not in SHARED_MODULES


def is_story(key: str, used: dict[str, set[str]]) -> bool:
    files = used.get(key)
    if files:
        return all(_story_file(f) for f in files)
    return key.startswith("PORING_")


def split_template(example_path: str, used: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """env.example -> (.env 틀 줄들, poring.env 틀 줄들). 빈 줄로 나뉜 덩어리 단위로"""
    with open(example_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    blocks, cur = [], []
    for line in lines:
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    base, story = [], []
    for block in blocks:
        keys = []
        for line in block:
            m = _KEY_LINE.match(line) if not line.lstrip().startswith("#") else _COMMENTED_KEY.match(line)
            keys.append((line, m.group(1) if m else None))
        named = [k for _, k in keys if k]
        s = [k for k in named if is_story(k, used)]
        if not named or len(s) == 0:
            base += block + [""]
        elif len(s) == len(named):
            story += block + [""]
        else:   # 섞임: 설명 줄은 바로 아래 변수를 따라가고, 덩어리 첫 줄(제목)은 양쪽에
            b, st, pend = [], [], []
            for ln, k in keys:
                if not k:
                    pend.append(ln)
                    continue
                (st if is_story(k, used) else b).extend(pend + [ln])
                pend = []
            b += pend
            head = block[0] if block[0].lstrip().startswith("#") else ""
            if head:
                if b and b[0] != head:
                    b.insert(0, head)
                if st and st[0] != head:
                    st.insert(0, head)
            base += b + [""]
            story += st + [""]
    return base, story


def render(template: list[str], current: dict[str, str], order: list[str], extra: list[str], header: str = "") -> tuple[str, list[str]]:
    """틀에 지금 값을 채운다 -> (내용, 새로 기본값으로 채운 변수)"""
    out, added, seen = ([header, ""] if header else []), [], set()
    for line in template:
        m = _KEY_LINE.match(line)
        if not m or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key, raw = m.group(1), m.group(2)
        if key in seen:
            continue
        seen.add(key)
        inline = re.search(r"\s+#\s*(.*)$", raw) if raw.strip()[:1] not in ("'", '"') else None
        if inline and inline.group(1):
            out.append(f"# {inline.group(1).strip()}")
        if key in current:
            out.append(f"{key}={current[key]}")
        else:
            out.append(f"{key}={parse_value(raw)}")
            added.append(key)
    rest = [k for k in order if k in extra and k not in seen]
    if rest:
        out += ["", "# ---- env.example에 없는 기존 변수 (지우지 않고 그대로 남김) ----"] + [f"{k}={current[k]}" for k in rest]
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n") + "\n"
    return text, added


def plan(example: str, env_path: str, por_path: str, root: str = BASE_DIR) -> dict:
    used = usage(root)
    env_vals, env_order = read_env(env_path)
    por_vals, por_order = read_env(por_path)
    current = {**por_vals, **env_vals}                      # 둘 다 있으면 .env 값
    order = env_order + [k for k in por_order if k not in env_vals]
    base_t, story_t = split_template(example, used)
    in_template = {(_KEY_LINE.match(ln) or _COMMENTED_KEY.match(ln)).group(1) for ln in base_t + story_t
                   if _KEY_LINE.match(ln) or _COMMENTED_KEY.match(ln)}
    extra = [k for k in order if k not in in_template]
    extra_story = [k for k in extra if is_story(k, used)]
    base_text, base_added = render(base_t, current, order, [k for k in extra if k not in extra_story])
    story_text, story_added = render(story_t, current, order, extra_story,
                                     header="# 포링 이야기(오아시스 거리 인물·장면·취미…) 변수 - scripts/split_env.py가 .env에서 나눔. 봇이 .env 다음에 읽는다")
    in_story = {m.group(1) for m in (_KEY_LINE.match(ln) for ln in story_text.split("\n")) if m}
    moved = [k for k in env_order if k in in_story]
    return {"env": base_text, "poring": story_text, "added_env": base_added, "added_poring": story_added, "moved": moved,
            "extra": extra}


def _write(path: str, content: str, stamp: str) -> bool:
    exists = os.path.exists(path)
    if exists:
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return False
        shutil.copy2(path, f"{path}.bak.{stamp}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    if exists:
        shutil.copymode(path, tmp)
    else:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=".env를 기본(.env)과 포링 이야기(poring.env)로 나눈다")
    ap.add_argument("--env", default=os.path.join(BASE_DIR, ".env"))
    ap.add_argument("--poring", default=os.path.join(BASE_DIR, POR_ENV))
    ap.add_argument("--example", default=os.path.join(BASE_DIR, "env.example"))
    ap.add_argument("--apply", action="store_true", help="실제로 나눠서 쓴다 (원래 파일은 .bak.날짜시각으로 백업)")
    ap.add_argument("--check", action="store_true", help="새 변수가 있으면 종료 코드 1, 아무것도 안 바꿈")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.example):
        print(f"env.example을 찾을 수 없음: {args.example}")
        return 2
    p = plan(args.example, args.env, args.poring)
    say = (lambda *a: None) if args.quiet else print
    say(f".env: {args.env}\nporing.env: {args.poring}")
    say(f"- .env에서 poring.env로 옮길 변수 {len(p['moved'])}개: {', '.join(p['moved']) or '없음'}")
    if p["added_env"] or p["added_poring"]:
        say(f"- 새로 채울 변수 (env.example 기본값): .env {', '.join(p['added_env']) or '없음'} / poring.env {', '.join(p['added_poring']) or '없음'}")
    if p["extra"]:
        say(f"- env.example에 없는 기존 변수 {len(p['extra'])}개 (알맞은 파일 맨 아래에 그대로): {', '.join(p['extra'])}")
    if args.check:
        return 1 if p["added_env"] or p["added_poring"] else 0
    if not args.apply:
        say("\n나누려면: python scripts/split_env.py --apply")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    a = _write(args.env, p["env"], stamp)
    b = _write(args.poring, p["poring"], stamp)
    print("✅ " + (" / ".join(x for x in (".env 갱신" if a else "", "poring.env 갱신" if b else "") if x) or "바뀐 것 없음")
          + (f" (백업: *.bak.{stamp})" if a or b else "") + " - 봇에 반영하려면 재시작: sudo systemctl restart discord_bot_v2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
