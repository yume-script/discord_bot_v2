"""
.env 정리 - 코드에서 더는 읽지 않는 환경변수를 찾아 지운다 (기본은 보여 주기만).

    cd /mnt/discord_bot_v2 && venv/bin/python scripts/prune_env.py           # 지울 것만 보여 준다 (파일 안 바꿈)
    venv/bin/python scripts/prune_env.py --apply                            # .env에서 지운다 (.env.bak.날짜시각 백업)
    venv/bin/python scripts/prune_env.py --keep FOO,BAR                     # 이 변수는 남긴다 (여러 번 써도 됨)
    venv/bin/python scripts/prune_env.py --env /다른/경로/.env               # 다른 .env

"쓰는 변수"를 가리는 법 (이 저장소 안의 코드를 읽는다 - 봇을 띄우지 않는다):
- *.py *.sh *.yaml *.yml *.service *.js *.toml *.cfg *.ini 안에 이름이 그대로 나오는 변수 (os.getenv("X"), environ["X"], ${X} 등)
  - 테스트(tests/), 문서(README, *.md), env.example 자체는 세지 않는다 (거기만 나오면 안 쓰는 것)
- f-string으로 이름을 만드는 곳은 정해진 부분이 맞는 변수를 모두 쓰는 것으로 본다
  (getenv(f"PORING_DOCKER_{x}") -> PORING_DOCKER_로 시작, environ.get(f"{key.upper()}_BOT_TOKEN") -> _BOT_TOKEN으로 끝남)
- 라이브러리가 스스로 읽는 변수(프록시, 시간대, LLM 키 등 LIBRARY_VARS)는 늘 남긴다
지우는 것:
- 쓰지 않는 변수의 줄 (값이 들어 있어도 - 그래서 먼저 보여 주고, --apply 때 백업)
- 같은 변수가 여러 번 있으면 앞의 것 (마지막 값이 쓰이므로)
- 그 결과 바로 위에 남는 설명 주석 줄과 연달아 생기는 빈 줄
화면에는 변수 이름만 보여 준다 (토큰·웹후크 같은 값은 출력하지 않는다).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_EXT = (".py", ".sh", ".yaml", ".yml", ".service", ".js", ".toml", ".cfg", ".ini", ".timer")
SKIP_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__", "storage", "tests", "logs", "data"}
_KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
# getenv(f"PREFIX_{..}") / environ.get(f"PREFIX_{..}") / os.environ[f"PREFIX_{..}"] 처럼 이름을 만들어 읽는 곳
_DYNAMIC = re.compile(r"""(?:getenv|environ(?:\.get)?|env_bool|env_int|_env|env)\s*[\(\[]\s*f["']([A-Z][A-Z0-9_]*)\{""")
# getenv(f"{key.upper()}_BOT_TOKEN") 처럼 뒷부분이 정해진 곳
_DYNAMIC_TAIL = re.compile(r"""(?:getenv|environ(?:\.get)?|env_bool|env_int|_env|env)\s*[\(\[]\s*f["']\{[^}"']*\}([A-Z0-9_]{3,})["']""")
# 라이브러리가 직접 읽는 변수 - 코드에 이름이 없어도 지우지 않는다
LIBRARY_VARS = {
    "TZ", "LANG", "LC_ALL", "PATH", "HOME", "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT", "LANGCHAIN_ENDPOINT",
    "LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT",
    "HF_TOKEN", "HF_HOME", "TOKENIZERS_PARALLELISM", "DOCKER_HOST",
}


def code_files(root: str):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for f in files:
            if f.endswith(CODE_EXT) and f != os.path.basename(__file__):
                yield os.path.join(d, f)


def used_names(root: str) -> tuple[set[str], set[str], set[str]]:
    """코드에 나오는 대문자 이름들과, 이름을 만들어 읽는 곳의 앞부분·뒷부분들"""
    names, prefixes, suffixes = set(), set(), set()
    for path in code_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        names.update(_NAME.findall(text))
        prefixes.update(p for p in _DYNAMIC.findall(text) if len(p) >= 4)
        suffixes.update(_DYNAMIC_TAIL.findall(text))
    return names, prefixes, suffixes


def env_keys(path: str) -> list[tuple[int, str]]:
    """[(줄 번호, 변수)] - 주석 줄은 뺀다"""
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if line.lstrip().startswith("#"):
                continue
            m = _KEY_LINE.match(line)
            if m:
                out.append((i, m.group(1)))
    return out


def plan(env_path: str, root: str, keep: set[str]) -> tuple[list[str], list[str], set[int]]:
    """(안 쓰는 변수, 겹친 변수, 지울 줄 번호)"""
    names, prefixes, suffixes = used_names(root)
    keys = env_keys(env_path)
    unused, dup, drop = [], [], set()
    last = {}
    for i, k in keys:
        last[k] = i
    for i, k in keys:
        if last[k] != i:
            dup.append(k)
            drop.add(i)
            continue
        if k in names or k in keep or k in LIBRARY_VARS or any(k.startswith(p) for p in prefixes) \
                or any(k.endswith(x) for x in suffixes):
            continue
        unused.append(k)
        drop.add(i)
    return unused, sorted(set(dup)), drop


def rewrite(lines: list[str], drop: set[int]) -> list[str]:
    """지울 줄을 빼고, 그 바로 위에 붙어 있던 설명 주석(빈 줄 전까지)도 그 줄이 지운 변수만 설명하면 같이 뺀다"""
    remove = set(drop)
    for i in sorted(drop):
        j = i - 1
        block = []
        while j >= 0 and lines[j].lstrip().startswith("#") and not _KEY_LINE.match(lines[j].lstrip("# ").rstrip()):
            block.append(j)
            j -= 1
        # 주석 바로 아래가 지운 변수들뿐이고 그다음이 빈 줄/끝이면 그 설명은 지운 변수의 것
        k = i + 1
        while k < len(lines) and k in drop:
            k += 1
        if block and (k >= len(lines) or not lines[k].strip() or lines[k].lstrip().startswith("#")):
            remove.update(block)
    out = [line for n, line in enumerate(lines) if n not in remove]
    cleaned = []
    for line in out:   # 빈 줄이 셋 이상 이어지지 않게
        if not line.strip() and len(cleaned) >= 2 and not cleaned[-1].strip() and not cleaned[-2].strip():
            continue
        cleaned.append(line)
    return cleaned


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=".env에서 안 쓰는 환경변수 지우기")
    ap.add_argument("--env", default=os.path.join(BASE_DIR, ".env"))
    ap.add_argument("--root", default=BASE_DIR, help="코드를 찾을 폴더 (기본: 이 저장소)")
    ap.add_argument("--keep", action="append", default=[], help="남길 변수 (쉼표로 여러 개)")
    ap.add_argument("--apply", action="store_true", help=".env를 실제로 고친다 (백업 남김)")
    args = ap.parse_args(argv)
    if not os.path.exists(args.env):
        print(f".env가 없습니다: {args.env}")
        return 2
    keep = {k.strip() for item in args.keep for k in item.split(",") if k.strip()}
    unused, dup, drop = plan(args.env, args.root, keep)
    total = len(env_keys(args.env))
    print(f"{args.env}: 변수 {total}개")
    if dup:
        print(f"\n겹친 변수 {len(dup)}개 (앞의 것을 지우고 마지막 값을 남김): " + ", ".join(dup))
    if unused:
        print(f"\n코드에서 안 쓰는 변수 {len(unused)}개:")
        for k in unused:
            print(f"  - {k}")
        print("\n남겨야 할 것이 있으면 --keep 이름,이름 으로 빼고 다시 실행하세요.")
    if not drop:
        print("\n지울 것이 없습니다.")
        return 0
    if not args.apply:
        print(f"\n(보여 주기만 했습니다. 지우려면 --apply)")
        return 0
    with open(args.env, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
    backup = f"{args.env}.bak.{datetime.now():%Y%m%d%H%M%S}"
    shutil.copy2(args.env, backup)
    new = rewrite(lines, drop)
    tmp = args.env + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(new)
    shutil.copymode(args.env, tmp)
    os.replace(tmp, args.env)
    print(f"\n{len(drop)}줄을 지웠습니다 ({total} -> {len(env_keys(args.env))}개). 백업: {backup}")
    print("봇을 다시 시작하면 적용됩니다: sudo systemctl restart discord_bot_v2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
