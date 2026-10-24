"""
마을 방어 배치 판 - 오아시스 마을을 칸으로 나눈 지도에 인물들이 목책/감시탑/함정/결계석/횃불... 을 놓고 고치고 치운다.

- 판은 크게(BOARD_W x BOARD_H) 잡아 두고, 지금은 "열린 구역"(DISTRICTS 중 open)만 쓴다 - 마을이 넓어지면
  구역을 열어서(open_district) 같은 판 위에 이어 붙인다. 좌표는 판 전체 기준이라 구역이 늘어도 바뀌지 않는다.
- 놓는 사람은 인물만 (사람/관리자 명령 없음). 매시 판단 때 놓을 수 있는 인물(ROLES)에게만 규칙이 붙고,
  "decor"를 고르면 실제로 판에 반영된다 (LLM 호출 추가 없음 - 판단 응답에 한 칸 더).
  · 홍두식: 설치/수리 (품삯), 리정혁(수비대장): 방어 시설, 지은탁(결계 마법사): 결계석/마법등,
    박충섭(룬 각인사): 룬 벽화, 여화정(촌장): 설치/수리, 부상길(영주): 철거
  · 자리는 LLM이 좌표를 고르지 않는다 - "어디 근처"(랜드마크 이름)만 고르면 가장 가까운 빈칸에 놓는다.
- 돈은 "방어 기금"(마을 회의비가 매일 DAILY_FUND씩 쌓임)에서 나간다. 모자라면 못 한다.
- 매일 상태가 줄고(함정/횃불/약초밭은 빨리), 비 오는 날은 약초밭/가시덤불이 살아난다. 상태가 나빠지면 마을 사건 한 번,
  0이 되면 "망가짐" - 수리하거나 치울 때까지 그대로. (2단계: 밤마다 몬스터 침입 때 이 판이 방어력이 된다)
- 인물들은 판단 때 [마을 방어 배치]를 본다 (어디가 약한지/무슨 얘기를 할지 얘깃거리, 망가진 건 민원거리).
- 그림: 매일 저녁 RENDER_HOUR 회차에 판 그림(PNG)을 대화 채널에 올린다 (바뀐 게 있는 날만, townmap 글꼴).
저장: storage/poring_food/decor.json {"fund", "fund_day", "items": [...], "log": [...], "acts": {날짜: {이름: 횟수}},
"open": [구역 id], "decayed_day"}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from .clock import now_kst
from .josa import j
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_DECOR", "1") not in ("0", "false", "False", "")
DECOR_PATH = os.path.join(STATE_DIR, "decor.json")
DECOR_DIR = os.path.join(STATE_DIR, "decor")

BOARD_W, BOARD_H = 64, 40          # 판 전체 (앞으로 넓어질 마을까지)
START_FUND = int(os.getenv("PORING_DECOR_START_FUND", "300000"))
DAILY_FUND = int(os.getenv("PORING_DECOR_DAILY_FUND", "20000"))
WAGE = 20000                        # 설치/수리 품삯 (홍두식)
ACTS_PER_PERSON = 1                 # 한 사람 하루 배치 횟수
ACTS_PER_DAY = 4                    # 마을 전체 하루
BAD = 30                            # 이 아래면 "낡음/시듦" 사건
RENDER_HOUR = int(os.getenv("PORING_DECOR_RENDER_HOUR", "20"))
CELL = 34
TITLE = "마을 방어 배치"

# 구역 (판 전체 좌표 x0, y0, x1, y1 - 끝 포함). 지금은 마을 광장만 열려 있다. 몬스터는 서쪽(검은 숲)에서 길을 따라 온다.
DISTRICTS = {
    "oasis": {"name": "오아시스 마을 광장", "rect": (22, 14, 41, 25), "open": True},
    "upper": {"name": "북쪽 성벽길", "rect": (22, 2, 41, 13), "open": False},
    "market": {"name": "동쪽 시장길", "rect": (42, 14, 63, 25), "open": False},
    "river": {"name": "남쪽 강가", "rect": (22, 26, 41, 39), "open": False},
    "west": {"name": "검은 숲 어귀", "rect": (0, 14, 21, 25), "open": False},
}
# 랜드마크 (기관/건물 - 방어 시설을 놓을 수 없는 칸). (x0, y0, x1, y1)
LANDMARKS = {
    "서쪽 성문": (22, 17, 23, 18),
    "오아시스 마법도서관": (24, 15, 26, 16),
    "별빛 환영극장": (28, 15, 31, 16),
    "찻집 오후세시": (28, 17, 29, 17),
    "수비대 초소": (34, 15, 35, 16),
    "선술집 단밤": (38, 15, 39, 16),
    "음유 주점": (23, 22, 24, 23),
    "주막 남숙": (27, 22, 28, 23),
    "결계석": (30, 21, 31, 21),
    "시장 입구": (33, 22, 34, 23),
    "촌장 회관": (37, 22, 39, 23),
}
# 마을 길 (가운데 가로길 = 몬스터가 들어오는 길 - 방어 시설은 길가에만, 길 위엔 못 놓음)
ROAD_Y = (19, 20)

ITEMS = {  # key: (이름, 비용, 하루 닳는 정도, 그림 색, 한 글자)
    "목책": ("목책", 40000, 4, "#B07D4F", "책"),
    "감시탑": ("감시탑", 120000, 2, "#56CCF2", "탑"),
    "함정": ("함정", 30000, 8, "#828282", "함"),
    "결계석": ("결계석", 150000, 1, "#BB6BD9", "결"),
    "마법등": ("마법등", 50000, 3, "#F2C94C", "등"),
    "횃불": ("횃불", 10000, 20, "#F2994A", "불"),
    "투석기": ("투석기", 100000, 2, "#EB5757", "투"),
    "룬 벽화": ("룬 벽화", 100000, 1, "#9B51E0", "룬"),
    "약초밭": ("약초밭", 30000, 8, "#5BB974", "약"),
    "가시덤불": ("가시덤불", 20000, 5, "#2E8B57", "덤"),
}
GROWS_IN_RAIN = ("약초밭", "가시덤불")
# 누가 무엇을 할 수 있나 (agents.json 이름)
ROLES = {
    "홍두식": {"do": ("place", "repair"), "items": [k for k in ITEMS if k not in ("룬 벽화", "결계석")],
            "why": "품삯 받고 뭐든 하는 모험가 출신 홍반장 - 설치와 수리 담당"},
    "리정혁": {"do": ("place", "repair"), "items": ["목책", "감시탑", "함정", "투석기", "횃불"],
            "why": "성벽 수비대장 - 몬스터가 들어오는 길목을 막는다"},
    "지은탁": {"do": ("place", "repair"), "items": ["결계석", "마법등"], "why": "결계 마법사 - 결계석과 마법등 담당"},
    "박충섭": {"do": ("place", "repair"), "items": ["룬 벽화"], "why": "룬 각인사 - 몬스터를 막는 룬 벽화 담당"},
    "여화정": {"do": ("place", "repair"), "items": list(ITEMS), "why": "촌장 - 마을 회의에서 정한 방어 시설을 챙긴다"},
    "부상길": {"do": ("remove",), "items": list(ITEMS), "why": "영주 - 마음에 안 드는 건 치우라고 한다"},
}


# ===================================================================== 저장
def _load() -> dict:
    try:
        with open(DECOR_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(DECOR_PATH), exist_ok=True)
    tmp = DECOR_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DECOR_PATH)


def _state() -> dict:
    data = _load()
    data.setdefault("fund", START_FUND)
    data.setdefault("items", [])
    data.setdefault("log", [])
    data.setdefault("acts", {})
    data.setdefault("open", [k for k, v in DISTRICTS.items() if v["open"]])
    return data


def open_district(key: str) -> bool:
    """[마을이 넓어질 때] 구역 하나를 연다. 반환: 새로 열었는지."""
    data = _state()
    if key not in DISTRICTS or key in data["open"]:
        return False
    data["open"].append(key)
    _save(data)
    print(f"[{TITLE}] {DISTRICTS[key]['name']} 구역이 열렸다")
    return True


# ===================================================================== 판
def _in(rect, x, y) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _open_cells(data: dict) -> list[tuple[int, int]]:
    cells = []
    for key in data["open"]:
        x0, y0, x1, y1 = DISTRICTS[key]["rect"]
        cells += [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    return cells


def _blocked(x, y) -> bool:
    return y in ROAD_Y or any(_in(r, x, y) for r in LANDMARKS.values())


def _free_near(data: dict, near: str) -> tuple[int, int] | None:
    """랜드마크(없으면 마을 길 한가운데) 바로 옆의 가장 가까운 빈칸."""
    taken = {(i["x"], i["y"]) for i in data["items"]}
    r = LANDMARKS.get(near)
    if r:
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    else:
        x0, y0, x1, y1 = DISTRICTS["oasis"]["rect"]
        cx, cy = (x0 + x1) / 2, ROAD_Y[0] - 1
    cands = [c for c in _open_cells(data) if c not in taken and not _blocked(*c)]
    if not cands:
        return None
    return min(cands, key=lambda c: ((c[0] - cx) ** 2 + (c[1] - cy) ** 2, c[1], c[0]))


def _near_name(x: int, y: int) -> str:
    def dist(r):
        return ((r[0] + r[2]) / 2 - x) ** 2 + ((r[1] + r[3]) / 2 - y) ** 2
    return min(LANDMARKS, key=lambda k: dist(LANDMARKS[k]))


def _cond_word(c: int) -> str:
    return "망가짐" if c <= 0 else "낡음/시듦" if c < BAD else "괜찮음" if c < 70 else "멀쩡함"


# ===================================================================== 매일
def daily(now: datetime | None = None, weather: str = "", inject=None) -> list[str]:
    """[하루 한 번] 방어 기금 적립 + 닳기 + 비 오면 약초밭/가시덤불 회복 + 나빠진 것 사건. 반환: 사건 제목들."""
    now = now or now_kst()
    data = _state()
    day = now.strftime("%Y-%m-%d")
    if data.get("decayed_day") == day:
        return []
    first = "decayed_day" not in data
    data["decayed_day"] = day
    out = []
    if not first:
        data["fund"] += DAILY_FUND
        rain = bool(re.search(r"(^|[\s,(])(비|눈|소나기)(\s|\(|,|$)", weather or ""))  # "비상" 같은 말은 빼고
        for it in data["items"]:
            before = it["cond"]
            it["cond"] = max(0, it["cond"] - ITEMS[it["item"]][2])
            if rain and it["item"] in GROWS_IN_RAIN and it["cond"] > 0:
                it["cond"] = min(100, it["cond"] + 12)
            if before >= BAD > it["cond"] or (before > 0 and it["cond"] == 0):
                word = "망가졌다" if it["cond"] == 0 else ("시들어 간다" if it["item"] in GROWS_IN_RAIN else "낡아 간다")
                out.append(f"{it['near']} 근처 {j(it['item'], '이')} {word}")
    _save(data)
    if inject:
        for title in out:
            try:
                inject(["마을"], f"{TITLE}: {title} - 몬스터가 오기 전에 누가 손봐야 한다.", title)
            except Exception as e:  # noqa: BLE001
                print(f"[{TITLE}] 사건 전달 실패: {e}")
    return out


# ===================================================================== 판단 규칙 / 실행
def prompt_rule(name: str, now: datetime | None = None) -> str:
    """판단에 붙는 규칙 (놓을 수 있는 사람만, 오늘 이미 했거나 마을 전체 상한이면 "")."""
    if not ENABLED or name not in ROLES:
        return ""
    now = now or now_kst()
    data = _state()
    acts = data["acts"].get(now.strftime("%Y-%m-%d"), {})
    if acts.get(name, 0) >= ACTS_PER_PERSON or sum(acts.values()) >= ACTS_PER_DAY:
        return ""
    role = ROLES[name]
    have = ", ".join(f"{i['near']} 근처 {i['item']}({_cond_word(i['cond'])})" for i in data["items"][-12:]) or "아직 아무것도 없음"
    can = []
    if "place" in role["do"]:
        can.append("놓기(place): " + ", ".join(f"{k} {ITEMS[k][1]:,}원" for k in role["items"]))
    if "repair" in role["do"]:
        can.append("고치기(repair): 낡거나 망가진 것 (값의 20%)")
    if "remove" in role["do"]:
        can.append("치우기(remove): 마음에 안 들거나 망가진 것")
    return (f"- {TITLE}({role['why']}): {' / '.join(can)}. 방어 기금 {data['fund']:,}원. "
            f"지금 마을에 놓인 방어 시설: {have}. 몬스터는 서쪽 검은 숲에서 마을 길을 따라 들어온다. "
            f"자리는 장소 이름(어디 근처)만 고르면 된다 - {', '.join(LANDMARKS)}. "
            "할 만할 때만 decor에 쓰고 대부분은 null (하루 한 번).\n")


SPEC = (', "decor": null 또는 {"do": "place|repair|remove", "item": "' + "|".join(ITEMS) + '", '
        '"near": "어디 근처(장소 이름)", "why": "왜 하는지 한 줄"}')


def parse(raw, name: str) -> dict | None:
    if not ENABLED or name not in ROLES or not isinstance(raw, dict):
        return None
    do, item = str(raw.get("do") or "").strip(), str(raw.get("item") or "").strip()
    if do not in ROLES[name]["do"] or item not in ITEMS:
        return None
    if do == "place" and item not in ROLES[name]["items"]:
        return None
    near = str(raw.get("near") or "").strip()
    near = next((k for k in LANDMARKS if k in near or near in k and near), "") if near else ""
    return {"do": do, "item": item, "near": near, "why": str(raw.get("why") or "").strip()[:80]}


def act(name: str, d: dict, now: datetime | None = None) -> dict | None:
    """방어 시설 하나를 판에 반영. 반환 {"text", "cost", "wage", "item"} 또는 None(못 함)."""
    if not ENABLED or not d:
        return None
    now = now or now_kst()
    data = _state()
    day = now.strftime("%Y-%m-%d")
    acts = data["acts"].setdefault(day, {})
    if acts.get(name, 0) >= ACTS_PER_PERSON or sum(acts.values()) >= ACTS_PER_DAY:
        return None
    item, do = d["item"], d["do"]
    near = d.get("near") or ""
    mine = [i for i in data["items"] if i["item"] == item and (not near or i["near"] == near)]
    cost, text = 0, ""
    if do == "place":
        cost = ITEMS[item][1]
        if data["fund"] < cost:
            print(f"[{TITLE}] {name}: {item} 놓기 - 방어 기금 모자람 ({data['fund']:,}원)")
            return None
        spot = _free_near(data, near)
        if not spot:
            return None
        where = near or _near_name(*spot)
        data["items"].append({"item": item, "x": spot[0], "y": spot[1], "near": where, "cond": 100,
                              "by": name, "at": now.isoformat(timespec="minutes")})
        text = f"{where} 근처에 {j(item, '을')} 세웠다"
    elif do == "repair":
        target = min(mine or data["items"], key=lambda i: i["cond"], default=None)
        if not target or target["cond"] >= 70:
            return None
        cost = ITEMS[target["item"]][1] // 5
        if data["fund"] < cost:
            return None
        target["cond"] = 100
        item, near = target["item"], target["near"]
        text = f"{near} 근처 {j(item, '을')} 고쳤다"
    elif do == "remove":
        target = min(mine, key=lambda i: i["cond"], default=None)
        if not target:
            return None
        data["items"].remove(target)
        near = target["near"]
        text = f"{near} 근처 {j(item, '을')} 치워 버렸다"
    data["fund"] -= cost
    wage = WAGE if name == "홍두식" and do in ("place", "repair") and data["fund"] >= WAGE else 0
    data["fund"] -= wage
    acts[name] = acts.get(name, 0) + 1
    data["acts"] = {k: v for k, v in data["acts"].items() if k >= day[:8] + "01" or k == day}
    data["log"] = (data["log"] + [{"at": now.isoformat(timespec="minutes"), "by": name, "do": do, "item": item,
                                   "near": near, "cost": cost, "why": d.get("why", "")}])[-200:]
    data["changed_day"] = day
    _save(data)
    print(f"[{TITLE}] {name}: {text} (기금 -{cost + wage:,}원, 남은 {data['fund']:,}원)")
    return {"text": text, "cost": cost, "wage": wage, "item": item, "near": near, "do": do, "fund": data["fund"]}


def prompt_block(now: datetime | None = None) -> str:
    """판단용: 마을에 놓인 방어 시설 (모두가 보는 마을이라 다 안다)."""
    data = _load()
    items = data.get("items") or []
    if not items:
        return ""
    bad = [i for i in items if i["cond"] < BAD]
    lines = [f"- {i['near']} 근처 {i['item']} ({_cond_word(i['cond'])})" for i in items[-10:]]
    return (f"[마을 방어 배치 - {len(items)}개, 방어 기금 {data.get('fund', 0):,}원"
            + (f", 손봐야 할 것 {len(bad)}개" if bad else "") + "]\n" + "\n".join(lines))


# ===================================================================== 그림
# 사람이 있는 곳(상태의 location 글) -> 판의 랜드마크. 앞에 있는 게 먼저. "마을 길"은 길 위.
PLACE_KEYS = [
    ("찻집 오후세시", ("오후세시", "찻집")),
    ("별빛 환영극장", ("극장", "상영관", "영사실", "매표")),
    ("오아시스 마법도서관", ("도서관", "서가", "금서고", "수선실")),
    ("서쪽 성문", ("성문",)),
    ("수비대 초소", ("수비대", "초소", "성벽", "망루", "감시탑", "창고")),
    ("결계석", ("결계",)),
    ("선술집 단밤", ("단밤", "선술집", "대장간")),
    ("음유 주점", ("음유 주점", "주점", "LP")),
    ("주막 남숙", ("남숙", "주막")),
    ("시장 입구", ("시장", "반찬가게", "생선가게", "어물전")),
    ("촌장 회관", ("촌장 회관",)),
    ("마을 길", ("골목", "마을 길", "광장", "오아시스 마을")),
]


def place_of(location: str) -> str:
    """상태의 장소 글 -> 판 위 자리 (랜드마크 이름 / "마을 길" / "" = 판 밖: 집, 숲 등)."""
    loc = location or ""
    return next((name for name, keys in PLACE_KEYS if any(k in loc for k in keys)), "")


def people_from_states(states: dict, agent_names: set[str]) -> dict:
    """characters 상태 -> {자리: {"agents": [이름], "others": 배경 인물 수}} (자는 사람, 판 밖은 뺀다)."""
    out, seen = {}, set()
    for info in (states or {}).values():
        name = info.get("name", "")
        if not name or name in seen or info.get("state") == "자는 중":
            continue
        seen.add(name)
        where = place_of(info.get("location", ""))
        if not where:
            continue
        slot = out.setdefault(where, {"agents": [], "others": 0})
        if name in agent_names:
            slot["agents"].append(name)
        else:
            slot["others"] += 1
    return out


def render(now: datetime | None = None, people: dict | None = None) -> str | None:
    """열린 구역의 판 그림 (PNG 경로). 글꼴/Pillow가 없으면 None.
    people(people_from_states)를 주면 랜드마크마다 지금 있는 사람 수를 붙이고, 오른쪽에 누가 어디 있는지 적는다."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    from . import townmap
    font_file = townmap._font_file()
    if not font_file:
        return None
    now = now or now_kst()
    data = _state()
    xs = [DISTRICTS[k]["rect"] for k in data["open"]]
    x0, y0 = min(r[0] for r in xs), min(r[1] for r in xs)
    x1, y1 = max(r[2] for r in xs), max(r[3] for r in xs)
    pad, top = 24, 70
    board_w = (x1 - x0 + 1) * CELL + pad * 2
    side = 380 if people else 0
    w, h = board_w + side, (y1 - y0 + 1) * CELL + top + pad + 60
    img = Image.new("RGB", (w, h), townmap.BG)
    dr = ImageDraw.Draw(img)
    f_t, f_c, f_s = (ImageFont.truetype(font_file, s) for s in (26, 15, 13))
    dr.text((pad, 20), f"방어 배치 판 · {now:%m/%d} · 기금 {data['fund']:,}원 · {len(data['items'])}개",
            font=f_t, fill=townmap.TEXT)

    def box(x, y):
        X, Y = pad + (x - x0) * CELL, top + (y - y0) * CELL
        return X, Y, X + CELL - 2, Y + CELL - 2

    for (cx, cy) in _open_cells(data):
        fill = "#3A3C42" if cy in ROAD_Y else townmap.PANEL
        dr.rectangle(box(cx, cy), fill=fill)
    for name, r in LANDMARKS.items():
        if not any(_in(DISTRICTS[k]["rect"], r[0], r[1]) for k in data["open"]):
            continue
        a, b = box(r[0], r[1]), box(r[2], r[3])
        dr.rectangle((a[0], a[1], b[2], b[3]), fill="#4A4D55", outline=townmap.LINE)
        dr.text((a[0] + 4, a[1] + 4), name.replace("오아시스 ", ""), font=f_s, fill=townmap.TEXT)
    for it in data["items"]:
        X0, Y0, X1, Y1 = box(it["x"], it["y"])
        color = ITEMS[it["item"]][3] if it["cond"] >= BAD else "#6B6B6B"
        dr.rounded_rectangle((X0 + 2, Y0 + 2, X1 - 2, Y1 - 2), radius=6, fill=color)
        dr.text((X0 + 9, Y0 + 7), ITEMS[it["item"]][4], font=f_c, fill="#1E1F22")
        if it["cond"] < BAD:
            dr.line((X0 + 3, Y1 - 4, X1 - 3, Y0 + 4), fill="#EB5757", width=2)
    # 지금 있는 사람: 랜드마크/마을 길에 인원 배지, 오른쪽에 누가 어디
    if people:
        def badge(X, Y, n):
            label = f"{n}명"
            tw = dr.textlength(label, font=f_s) + 10
            dr.rounded_rectangle((X - tw, Y - 18, X, Y), radius=8, fill=townmap.ACCENT)
            dr.text((X - tw + 5, Y - 17), label, font=f_s, fill="#1E1F22")
        for where, p in people.items():
            n = len(p["agents"]) + p["others"]
            if not n:
                continue
            if where == "마을 길":
                xs_ = [c[0] for c in _open_cells(data)]
                gx = (min(xs_) + max(xs_)) // 2
                X1 = box(gx, ROAD_Y[0])[2]
                Y1 = box(gx, ROAD_Y[-1])[3]
            elif where in LANDMARKS:
                r = LANDMARKS[where]
                X1, Y1 = box(r[2], r[3])[2], box(r[2], r[3])[3]
                if r[1] == r[3]:  # 한 줄짜리 랜드마크는 이름을 가리지 않게 바깥 오른쪽에
                    X1 += 52
            else:
                continue
            badge(X1 - 2, Y1 - 2, n)
        sx, sy = board_w + 4, top
        dr.text((sx, sy - 34), "지금 어디에", font=f_c, fill=townmap.TEXT)
        order = [n for n, _ in PLACE_KEYS]
        for where in sorted(people, key=lambda k: order.index(k) if k in order else 99):
            p = people[where]
            if not (p["agents"] or p["others"]):
                continue
            dr.text((sx, sy), f"{where} ({len(p['agents']) + p['others']}명)", font=f_c, fill=townmap.ACCENT)
            sy += 22
            names = ", ".join(p["agents"]) + ((f" 외 {p['others']}명" if p["agents"] else f"주민 {p['others']}명") if p["others"] else "")
            for line in townmap._wrap(dr, names, f_s, side - 30):
                dr.text((sx + 10, sy), line, font=f_s, fill=townmap.TEXT)
                sy += 18
            sy += 8
            if sy > h - 70:
                break
    lx, ly = pad, h - 46
    for key, (nm, _, _, color, ch) in ITEMS.items():
        label = f"{ch}={nm}"
        tw = dr.textlength(label, font=f_s) + 24
        if lx + tw > w - pad:
            lx, ly = pad, ly + 20
        dr.rectangle((lx, ly + 2, lx + 12, ly + 14), fill=color)
        dr.text((lx + 16, ly), label, font=f_s, fill=townmap.DIM)
        lx += tw
    os.makedirs(DECOR_DIR, exist_ok=True)
    path = os.path.join(DECOR_DIR, f"{now:%Y-%m-%d}.png")
    img.save(path)
    return path


def should_render(now: datetime) -> bool:
    """오늘 저녁 RENDER_HOUR 회차이고, 오늘 바뀐 게 있고, 아직 안 올렸으면."""
    data = _load()
    day = now.strftime("%Y-%m-%d")
    return (ENABLED and now.hour == RENDER_HOUR and data.get("changed_day") == day
            and data.get("rendered_day") != day)


def mark_rendered(now: datetime) -> None:
    data = _state()
    data["rendered_day"] = now.strftime("%Y-%m-%d")
    _save(data)


def summary(now: datetime | None = None, limit: int = 25) -> str:
    """관리자 조회(/포링푸드골목)용 글: 방어 기금, 열린 구역, 놓인 것(상태), 손봐야 할 것, 최근 기록, 오늘 한 사람."""
    now = now or now_kst()
    data = _state()
    items = data["items"]
    opened = ", ".join(DISTRICTS[k]["name"] for k in data["open"])
    locked = ", ".join(v["name"] for k, v in DISTRICTS.items() if k not in data["open"])
    lines = [f"🛡️ **{TITLE} 판** ({now:%m/%d %H:%M})",
             f"방어 기금 **{data['fund']:,}원** (마을 회의비 하루 +{DAILY_FUND:,}원) · 놓인 것 {len(items)}개",
             f"열린 구역: {opened}" + (f" · 아직 닫힌 구역: {locked}" if locked else "")]
    if items:
        lines.append("\n**놓인 것**")
        for i in sorted(items, key=lambda i: i["cond"])[:limit]:
            lines.append(f"• {i['near']} 근처 {i['item']} - {_cond_word(i['cond'])} ({i['cond']}) · {i['by']}, {i['at'][5:10].replace('-', '/')}")
        if len(items) > limit:
            lines.append(f"…외 {len(items) - limit}개")
        bad = [i for i in items if i["cond"] < BAD]
        if bad:
            lines.append(f"⚠️ 손봐야 할 것 {len(bad)}개: " + ", ".join(f"{i['near']} 근처 {i['item']}" for i in bad[:8]))
    else:
        lines.append("\n아직 아무것도 놓이지 않았어요.")
    if data["log"]:
        lines.append("\n**최근 기록**")
        word = {"place": "세움", "repair": "고침", "remove": "치움"}
        for g in data["log"][-6:][::-1]:
            lines.append(f"• {g['at'][5:16].replace('T', ' ')} {g['by']}: {g['near']} 근처 {g['item']} {word.get(g['do'], g['do'])}"
                         + (f" (-{g['cost']:,}원)" if g.get("cost") else ""))
    acts = data["acts"].get(now.strftime("%Y-%m-%d"), {})
    lines.append(f"\n오늘 배치 {sum(acts.values())}/{ACTS_PER_DAY}번" + (f" ({', '.join(acts)})" if acts else "")
                 + f" · 놓는 사람: {', '.join(ROLES)}")
    return "\n".join(lines)[:1900]
