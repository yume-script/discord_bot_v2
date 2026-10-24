"""
"지금 마을은" 그림 - 매시 마을 전체를 PNG 한 장으로 (장소별로 누가 있는지, 이번 시간 누가 누구와 대화했는지).

    ┌ 🕒 11:00 · 날씨 · 깨어 있음 41 / 자는 중 9 ┐
    │ [오아시스 마법도서관] ● 애순이 ● 김동식 …  [찻집 오후세시] ● 소라 …  │   ← 장소별 구역, 소속별 색 점, 에이전트는 밝게/배경 인물은 흐리게
    │ [골목] ● 홍두식 ── ● 윤혜진 …                              │   ← 이번 시간 대화한 사람끼리 선 (통신석은 점선)
    └ 이번 시간 대화 · 속마음 · 진행 중인 마을 프로젝트(진행률) ┘

- LLM을 쓰지 않는다 (Pillow로 그리기만).
- 글꼴: 봇의 개미 짤(core/ant_voice.py)과 같은 NanumGothicCoding.ttf - storage/assets에 없으면 원본 저장소에서 한 번 받는다.
  (PORING_MAP_FONT로 다른 글꼴 경로를 줄 수 있다). 글꼴을 못 구하면 None을 돌려주고 호출한 쪽이 글로 대신 올린다.
- 저장: storage/poring_food/townmap/YYYY-MM-DD_HH.png (최근 KEEP_FILES장만 남긴다).
"""
from __future__ import annotations

import glob
import os
import re

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

MAP_DIR = os.path.join(STATE_DIR, "townmap")
KEEP_FILES = 72
FONT_URL = "https://raw.githubusercontent.com/yume-script/discord_bot/main/NanumGothicCoding.ttf"
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.getenv("PORING_MAP_FONT") or os.path.join(_BASE, "storage", "assets", "NanumGothicCoding.ttf")

W = 1600
PAD = 24
COLS = 3
BG, PANEL, LINE, TEXT, DIM, ACCENT = "#1E1F22", "#2B2D31", "#3F4147", "#F2F3F5", "#8E9297", "#FEE75C"
COMPANY_COLORS = [
    ("도서관", "#F0883E"), ("별빛", "#4E9BF5"), ("오아시스 마을", "#57C27A"), ("찻집", "#C8915A"),
    ("애순이·소라", "#B58BE8"),
]
OTHER_COLOR = "#A0A4AA"


def color_of(company: str) -> str:
    for key, c in COMPANY_COLORS:
        if key in (company or ""):
            return c
    return OTHER_COLOR


def _strip_emoji(text: str) -> str:
    """글꼴에 없는 이모지/기호를 뺀다 (한글/영문/숫자/기본 문장부호만)."""
    return re.sub(r"[^\w\s가-힣·.,:/()%+\-~?!'\"→°]", "", text or "").strip()


def _font_file() -> str | None:
    if os.path.exists(FONT_PATH):
        return FONT_PATH
    try:
        import requests
        res = requests.get(FONT_URL, timeout=20)
        if res.status_code == 200 and len(res.content) > 100_000:
            os.makedirs(os.path.dirname(FONT_PATH), exist_ok=True)
            with open(FONT_PATH, "wb") as f:
                f.write(res.content)
            return FONT_PATH
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 마을 지도 글꼴 받기 실패: {e}")
    return None


def _wrap(draw, text: str, font, width: int) -> list[str]:
    out, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def render(snapshot: dict) -> str | None:
    """
    snapshot = {
      "when": "2026-10-12 11:00", "weather": "...", "asleep": 9,
      "zones": [{"label": "찻집 오후세시", "people": [{"name", "company", "agent": bool, "fresh": bool}]}],
      "talks": [{"a", "b", "how": "통신석|직접", "place"}],
      "thoughts": [(이름, 속마음)], "projects": [(제목, 진행률 0~100)], "meetings": [문자열],
    }
    반환: 저장한 PNG 경로 (글꼴/Pillow가 없으면 None).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    font_file = _font_file()
    if not font_file:
        return None
    f_title = ImageFont.truetype(font_file, 30)
    f_zone = ImageFont.truetype(font_file, 22)
    f_name = ImageFont.truetype(font_file, 19)
    f_small = ImageFont.truetype(font_file, 16)

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    zone_w = (W - PAD * (COLS + 1)) // COLS
    # 1) 구역마다 사람 배치 (흐르듯 줄바꿈) -> 높이 계산
    layouts = []
    for z in snapshot.get("zones", []):
        x, y, items = 14, 48, []
        for p in z["people"]:
            label = p["name"] + ("*" if p.get("fresh") else "") + (f"({_strip_emoji(p['where'])})" if p.get("where") else "")
            font = f_name if p.get("agent") else f_small
            w = int(probe.textlength(label, font=font)) + 26
            if x + w > zone_w - 10:
                x, y = 14, y + 30
            items.append((x, y, label, font, p))
            x += w + 8
        layouts.append((z, items, y + 40))
    rows = [layouts[i:i + COLS] for i in range(0, len(layouts), COLS)]
    zones_h = sum(max(h for _, _, h in r) + PAD for r in rows)

    talks = snapshot.get("talks", [])
    thoughts = snapshot.get("thoughts", [])[:4]
    projects = snapshot.get("projects", [])[:3]
    meetings = snapshot.get("meetings", [])[:2]
    footer_lines = (1 + min(len(talks), 6) if talks else 0) + (1 + len(thoughts) if thoughts else 0) \
        + (1 + len(projects) if projects else 0) + (1 + len(meetings) if meetings else 0)
    footer_h = 30 * footer_lines + (PAD * 2 if footer_lines else 0)
    H = 90 + zones_h + footer_h + 40

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    awake = sum(len(z["people"]) for z in snapshot.get("zones", []))
    d.text((PAD, 22), f"지금 마을은 · {snapshot.get('when', '')}", font=f_title, fill=TEXT)
    sub = f"깨어 있음 {awake}명 · 자는 중 {snapshot.get('asleep', 0)}명"
    if snapshot.get("weather"):
        sub += " · " + _strip_emoji(snapshot["weather"])[:60]
    d.text((PAD, 60), sub, font=f_small, fill=DIM)
    # 범례
    lx = W - PAD
    for key, c in reversed(COMPANY_COLORS):
        tw = d.textlength(key, font=f_small)
        lx -= tw + 30
        d.ellipse((lx, 30, lx + 12, 42), fill=c)
        d.text((lx + 16, 26), key, font=f_small, fill=DIM)

    # 2) 구역 그리기 + 사람 위치 기억
    pos: dict[str, tuple[int, int]] = {}
    labels: list[tuple] = []  # 대화 선을 그린 뒤 이름을 다시 위에 쓰려고
    y0 = 90
    for r in rows:
        rh = max(h for _, _, h in r)
        for ci, (z, items, _h) in enumerate(r):
            x0 = PAD + ci * (zone_w + PAD)
            d.rounded_rectangle((x0, y0, x0 + zone_w, y0 + rh), radius=14, fill=PANEL, outline=LINE)
            title = _strip_emoji(z["label"]) + f"  {len(z['people'])}"
            d.text((x0 + 14, y0 + 12), title, font=f_zone, fill=TEXT)
            for x, y, label, font, p in items:
                cx, cy = x0 + x, y0 + y
                col = color_of(p.get("company", ""))
                r_ = 7 if p.get("agent") else 5
                d.ellipse((cx, cy + 6 - r_ + 5, cx + r_ * 2, cy + 6 + r_ + 5), fill=col if p.get("agent") else LINE,
                          outline=col)
                d.text((cx + 18, cy), label, font=font, fill=TEXT if p.get("agent") else DIM)
                labels.append(((cx + 18, cy), label, font, TEXT if p.get("agent") else DIM))
                pos.setdefault(p["name"], (cx + r_, cy + 11))
        y0 += rh + PAD

    # 3) 이번 시간 대화 선 (반투명)
    if talks:
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        for t in talks:
            a, b = pos.get(t["a"]), pos.get(t["b"])
            if not a or not b:
                continue
            col = (254, 231, 92, 190)
            if t.get("how") == "통신석":
                n = max(1, int(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 // 14))
                for i in range(0, n, 2):
                    p1 = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                    p2 = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
                    od.line([p1, p2], fill=col, width=3)
            else:
                od.line([a, b], fill=col, width=4)
            for p in (a, b):
                od.ellipse((p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9), outline=col, width=3)
        img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
        d = ImageDraw.Draw(img)
        for xy, label, font, fill in labels:  # 선이 이름을 가리지 않게 이름을 다시 위에 (테두리로 또렷하게)
            d.text(xy, label, font=font, fill=fill, stroke_width=3, stroke_fill=PANEL)

    # 4) 아래 띠
    if footer_lines:
        fy = y0
        d.rounded_rectangle((PAD, fy, W - PAD, fy + footer_h), radius=14, fill=PANEL, outline=LINE)
        fy += PAD

        def head(text):
            nonlocal fy
            d.text((PAD * 2, fy), text, font=f_zone, fill=ACCENT)
            fy += 30

        def line(text, color=TEXT):
            nonlocal fy
            d.text((PAD * 2 + 10, fy), _wrap(d, text, f_small, W - PAD * 5)[0], font=f_small, fill=color)
            fy += 30
        if talks:
            head(f"이번 시간 대화 {len(talks)}개  (실선: 직접 만남 / 점선: 통신석)")
            for t in talks[:6]:
                where = "통신석" if t.get("how") == "통신석" else _strip_emoji(t.get("place", ""))
                line(f"{t['a']} → {t['b']}  ({where})")
        if thoughts:
            head("속마음")
            for n, th in thoughts:
                line(f"{n}: {th}", DIM)
        if projects:
            head("마을 프로젝트")
            for title, pct in projects:
                bx = PAD * 2 + 10
                d.rounded_rectangle((bx, fy + 4, bx + 300, fy + 20), radius=6, fill=LINE)
                d.rounded_rectangle((bx, fy + 4, bx + 3 * max(2, min(100, int(pct))), fy + 20), radius=6, fill=ACCENT)
                d.text((bx + 316, fy), f"{_strip_emoji(title)} {int(pct)}%", font=f_small, fill=TEXT)
                fy += 30
        if meetings:
            head("모임")
            for m in meetings:
                line(_strip_emoji(m))
    d.text((PAD, H - 30), "* 이번 시간 새로 마음을 정한 사람 · 밝은 이름: 스스로 사는 사람(에이전트) · 흐린 이름: 배경 인물",
           font=f_small, fill=DIM)

    os.makedirs(MAP_DIR, exist_ok=True)
    stamp = snapshot.get("stamp") or re.sub(r"[^0-9]", "", snapshot.get("when", ""))[:10] or "now"
    path = os.path.join(MAP_DIR, f"{stamp}.png")
    img.save(path, "PNG", optimize=True)
    for old in sorted(glob.glob(os.path.join(MAP_DIR, "*.png")))[:-KEEP_FILES]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path
