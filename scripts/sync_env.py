"""
env.example이 바뀌었을 때 .env를 맞춰 주는 스크립트 - 기존 값은 그대로 두고, 새 변수만 기본값으로 채운다.

    cd /mnt/discord_bot_v2 && venv/bin/python scripts/sync_env.py            # 바뀔 내용만 보여 준다 (파일 안 바꿈)
    venv/bin/python scripts/sync_env.py --apply                              # .env를 새로 쓴다 (백업 남김)
    venv/bin/python scripts/sync_env.py --check                              # 새 변수가 있으면 종료 코드 1 (배포 스크립트용)

만드는 .env:
- 순서와 설명 주석은 env.example을 따른다 (새 변수 설명도 같이 들어온다).
- env.example에 있는 변수: .env에 값이 있으면 그 값 그대로, 없으면 env.example의 기본값 (= 새 변수).
- .env에만 있는 변수(직접 넣은 것, env.example에서 빠진 것)는 지우지 않고 맨 아래 "기존 변수" 칸에 그대로 남긴다.
- 값 뒤에 같은 줄로 붙은 주석(`KEY=값  # 설명`)은 떼고 값만 옮긴다 (빈 값 뒤 주석이 값으로 읽히는 문제 방지).
- --apply는 원래 .env를 .env.bak.날짜시각으로 백업한다.
- 화면에는 변수 이름만 보여 준다 (토큰/웹훅 같은 값은 출력하지 않는다).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_value(raw: str) -> str:
    """`=` 오른쪽 -> 값. 따옴표로 감싼 값은 그대로(따옴표 포함), 아니면 공백+#부터는 주석으로 보고 뗀다."""
    raw = raw.strip()
    if raw.startswith("#"):
        return ""  # 빈 값 뒤에 붙은 주석
    if raw[:1] in ("'", '"'):
        q = raw[0]
        end = raw.find(q, 1)
        return raw[: end + 1] if end > 0 else raw
    m = re.search(r"\s+#", raw)
    return (raw[: m.start()] if m else raw).strip()


def read_env(path: str) -> tuple[dict[str, str], list[str]]:
    """.env -> ({변수: 값}, 나온 순서). 같은 변수가 여러 번이면 마지막 값 (dotenv와 같게)."""
    values: dict[str, str] = {}
    order: list[str] = []
    if not os.path.exists(path):
        return values, order
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = _KEY_LINE.match(line.rstrip("\n"))
            if not m or line.lstrip().startswith("#"):
                continue
            key, val = m.group(1), parse_value(m.group(2))
            if key not in values:
                order.append(key)
            values[key] = val
    return values, order


def build(template_path: str, env_path: str) -> tuple[str, list[str], list[str], list[str]]:
    """새 .env 내용과 (새로 채운 변수, 기존 값을 쓴 변수, env.example에 없는 기존 변수)."""
    current, current_order = read_env(env_path)
    lines: list[str] = []
    added, kept, seen = [], [], set()
    with open(template_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            m = _KEY_LINE.match(line)
            if not m or line.lstrip().startswith("#"):
                lines.append(line)
                continue
            key, raw = m.group(1), m.group(2)
            if key in seen:
                continue
            seen.add(key)
            inline = re.search(r"\s+#\s*(.*)$", raw) if raw.strip()[:1] not in ("'", '"') else None
            if inline and inline.group(1):
                lines.append(f"# {inline.group(1).strip()}")
            if key in current:
                lines.append(f"{key}={current[key]}")
                kept.append(key)
            else:
                lines.append(f"{key}={parse_value(raw)}")
                added.append(key)
    extra = [k for k in current_order if k not in seen]
    if extra:
        lines += ["", "# ---- env.example에 없는 기존 변수 (sync_env.py가 지우지 않고 그대로 남김) ----"]
        lines += [f"{k}={current[k]}" for k in extra]
    return "\n".join(lines).rstrip("\n") + "\n", added, kept, extra


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="env.example에 맞춰 .env를 채운다 (기존 값 유지, 새 변수만 추가)")
    ap.add_argument("--env", default=os.path.join(BASE_DIR, ".env"))
    ap.add_argument("--example", default=os.path.join(BASE_DIR, "env.example"))
    ap.add_argument("--apply", action="store_true", help=".env를 실제로 새로 쓴다 (백업 남김)")
    ap.add_argument("--check", action="store_true", help="새 변수가 있으면 종료 코드 1, 아무것도 안 바꿈")
    args = ap.parse_args(argv)

    if not os.path.exists(args.example):
        print(f"env.example을 찾을 수 없음: {args.example}")
        return 2
    content, added, kept, extra = build(args.example, args.env)
    exists = os.path.exists(args.env)
    print(f".env: {args.env}{'' if exists else ' (없음 - 새로 만든다)'}")
    print(f"- 기존 값 유지: {len(kept)}개")
    print(f"- 새로 채울 변수 {len(added)}개 (env.example 기본값): {', '.join(added) or '없음'}")
    if extra:
        print(f"- env.example에 없는 기존 변수 {len(extra)}개 (맨 아래에 그대로 남김): {', '.join(extra)}")
    if args.check:
        return 1 if added else 0
    if not args.apply:
        if added or extra or not exists:
            print("\n바꾸려면: python scripts/sync_env.py --apply")
        return 0
    if exists:
        with open(args.env, "r", encoding="utf-8") as f:
            if f.read() == content:
                print("바뀐 것 없음.")
                return 0
        backup = f"{args.env}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(args.env, backup)
        print(f"백업: {backup}")
    tmp = args.env + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    if exists:
        shutil.copymode(args.env, tmp)  # 권한(예: 600) 그대로
    os.replace(tmp, args.env)
    print("✅ .env 갱신 완료 - 봇에 반영하려면 재시작: sudo systemctl restart discord_bot_v2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
