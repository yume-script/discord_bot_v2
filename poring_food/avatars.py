"""인물 아바타 - 기본 DiceBear 낙서 그림 대신, 인물 설정에 맞춘 사실적인 판타지 초상화를 만들어 쓴다.

- 만들기: 봇의 이미지 생성(ai.image_engine - AI Horde)으로 인물마다 한 장. 프롬프트는 LLM이 인물 프로필을 영어 외모
  묘사(나이대, 종족, 머리/눈, 옷, 분위기)로 바꾼 것 + 공통 스타일(사실적인 판타지 초상, 가슴 위, 글자 없음).
  봇이 PERIOD_MIN분마다 아직 없는 사람 한 명씩 만든다(Horde 줄을 오래 막지 않게). 다시 만들려면
  `python -m poring_food.avatars --force 이름` 또는 저장 폴더의 그 파일을 지운다.
- 저장: storage/poring_food/avatars/<키>.png (256x256) + avatars.json {이름: {"file", "desc", "at"}}.
- 보여 주기: 디스코드 웹훅은 아바타를 '공개 주소'로만 받는다. 봇 웹 서버(PORING_WEBHOOK_PORT)가 GET /avatars/<파일>로
  내보내고, 바깥 주소(nginx proxy manager 등)를 PORING_AVATAR_BASE_URL에 넣으면 그 주소로 바뀐다.
  주소가 없거나 아직 안 만든 사람은 원래 아바타 그대로. 봇 계정이 있는 인물(애순이/소라)은 계정 프로필 사진을 쓴다.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re

from ._log import pf_print as print  # print()를 봇 로그로
from .clock import now_kst
from .config import STATE_DIR

BASE_URL = os.getenv("PORING_AVATAR_BASE_URL", "").strip().rstrip("/")
ENABLED = os.getenv("PORING_AVATARS", "1") not in ("0", "false", "False", "")
DIR = os.path.join(STATE_DIR, "avatars")
INDEX = os.path.join(DIR, "avatars.json")
SIZE = 256
PERIOD_MIN = 15
MODEL = os.getenv("PORING_AVATAR_MODEL", "Realistic Vision")   # AI Horde 모델 이름 (사실적인 인물)
STYLE = ("realistic fantasy portrait, head and shoulders, looking at viewer, detailed face, natural skin texture, "
         "soft cinematic light, painterly background of a medieval fantasy village, high detail, no text, no watermark")
NEGATIVE = "text, watermark, logo, signature, cartoon, chibi, deformed, extra limbs, blurry, nsfw, nude"
_SAFE = re.compile(r"^[a-f0-9]{12}\.png$")


def key(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def _index() -> dict:
    try:
        with open(INDEX, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(data: dict) -> None:
    os.makedirs(DIR, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX)


def path_for(filename: str) -> str | None:
    """웹 서버용: 안전한 파일 이름이고 있으면 경로"""
    if not _SAFE.match(filename or ""):
        return None
    p = os.path.join(DIR, filename)
    return p if os.path.isfile(p) else None


def url_for(name: str, fallback: str = "", index: dict | None = None) -> str:
    """그 인물의 아바타 주소 (만든 초상화가 있고 공개 주소가 설정됐으면 그것, 아니면 fallback)"""
    if not (ENABLED and BASE_URL):
        return fallback
    rec = (index if index is not None else _index()).get(name)
    if not rec or not os.path.isfile(os.path.join(DIR, rec.get("file", ""))):
        return fallback
    return f"{BASE_URL}/avatars/{rec['file']}?v={rec.get('v', '')}"


def apply(agents: list[dict]) -> list[dict]:
    """load_agents가 부른다 - avatar_url을 만든 초상화 주소로 (봇 계정이 있는 인물은 그대로)"""
    if not (ENABLED and BASE_URL):
        return agents
    idx = _index()
    for a in agents:
        if not a.get("account"):
            a["avatar_url"] = url_for(a["name"], a.get("avatar_url", ""), idx)
    return agents


def missing(agents: list[dict]) -> list[dict]:
    idx = _index()
    return [a for a in agents if not a.get("account") and a["name"] not in idx]


def describe(agent: dict, llm=None) -> str:
    """인물 프로필 -> 영어 외모 묘사 한 줄 (실패하면 프로필 앞부분으로 대충)"""
    if llm is None:
        from .llm import llm_json
        llm = lambda s, u: llm_json(s, u, temperature=0.4, timeout=45, tag=f"아바타 {agent['name']}")  # noqa: E731
    out = llm("You write short visual descriptions for portrait image prompts. Fantasy medieval world. "
              "Describe only what is visible: apparent age, race (human/elf/dwarf/oni/goblin/lizardman/orc...), gender, "
              "hair, eyes, notable features, clothing, expression. No names, no story. English, under 45 words. "
              'Answer JSON only: {"desc": "..."}',
              f"Character profile (Korean): {agent.get('profile', '')}\nSpeech/manner: {agent.get('speech', '')}") or {}
    desc = str(out.get("desc") or "").strip()
    return desc[:300] or "a villager of a fantasy frontier town"


def _square(data: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    s = min(w, h)
    top = max(0, int((h - s) * 0.2))          # 얼굴이 위쪽에 있는 경우가 많아서 조금 위를 남긴다
    im = im.crop(((w - s) // 2, top, (w - s) // 2 + s, top + s)).resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def make(agent: dict, gen=None, llm=None) -> str | None:
    """한 사람의 초상화를 만들어 저장. 반환: 파일 이름 (실패하면 None)"""
    import asyncio
    name = agent["name"]
    desc = await asyncio.to_thread(describe, agent, llm)
    if gen is None:
        from ai.image_engine import generate_image

        async def gen(prompt):
            try:   # 사실적인 그림 모델 먼저 (Horde에 그 모델 일꾼이 없으면 기본 모델로)
                return (await generate_image(prompt, style=MODEL, negative_prompt=NEGATIVE)).image_bytes
            except Exception as e:  # noqa: BLE001
                print(f"[아바타] {MODEL} 모델 실패 - 기본 모델로: {e}")
                return (await generate_image(prompt, negative_prompt=NEGATIVE)).image_bytes
    try:
        raw = await gen(f"{desc}, {STYLE}")
        png = _square(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[아바타] {name} 만들기 실패: {e}")
        return None
    os.makedirs(DIR, exist_ok=True)
    fname = f"{key(name)}.png"
    with open(os.path.join(DIR, fname), "wb") as f:
        f.write(png)
    idx = _index()
    idx[name] = {"file": fname, "desc": desc, "at": now_kst().isoformat(timespec="minutes"),
                 "v": hashlib.sha1(png).hexdigest()[:8]}
    _save_index(idx)
    print(f"[아바타] {name} 초상화 만듦 ({desc[:60]})")
    return fname


async def make_next(agents: list[dict] | None = None) -> str | None:
    """[봇, PERIOD_MIN분마다] 아직 없는 사람 한 명 (지금 활동 중인 인물 먼저, 그다음 교대 대기 인물)"""
    if not (ENABLED and BASE_URL):
        return None
    if agents is None:
        from .agents import load_agents
        active = load_agents()
        names = {a["name"] for a in active}
        agents = active + [a for a in everyone() if a["name"] not in names]
    todo = missing(agents)
    return await make(todo[0]) if todo else None


def everyone() -> list[dict]:
    """agents.json의 인물 + 교대 대기(reserve)"""
    from .agents import AGENTS_PATH
    try:
        with open(AGENTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [a for a in (data.get("agents") or []) + (data.get("reserve") or []) if isinstance(a, dict) and a.get("name")]


if __name__ == "__main__":   # python -m poring_food.avatars [--force] [이름 ...]
    import asyncio
    import sys
    args = [a for a in sys.argv[1:] if a != "--force"]
    pool = {a["name"]: a for a in everyone()}
    if "--force" in sys.argv:
        idx = _index()
        for n in args:
            idx.pop(n, None)
        _save_index(idx)
    targets = [pool[n] for n in args if n in pool] or missing(list(pool.values()))
    for a in targets:
        print(a["name"], "->", asyncio.run(make(a)))
