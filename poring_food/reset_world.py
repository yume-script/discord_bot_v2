"""
세계 초기화 - 지금까지의 이야기(기억/연대기/관계/상태/실적/빌런 기록...)를 백업하고 지운 뒤 「오아시스 마을」을 1일차부터 시작한다.

- 백업: storage/poring_food_archive_<날짜시각>/ 에 상태 폴더 전체를 그대로 복사 (되돌리려면 그 폴더를 다시 복사해 오면 된다)
- 지우는 것: 상태 폴더(PORING_FOOD_STATE_DIR) 안의 이야기 기록 전부 (world.db - 기억/사건/예약함/실시간 대화 작업 포함)
- 남기는 것 (이야기가 아니라 실제 데이터 캐시): KEEP 목록 - 뉴스, 시세, 날씨, 실측 지표(최근 7일 평균용), 북오아시스/Plex 본 목록
- 마지막에 world_start.json(오늘 날짜)을 쓰고, "이 세계의 시작"을 모두가 아는 사실로 연대기에 남긴다.

봇이 상태 파일을 쓰는 중이면 안 되니 봇을 멈추고 실행한다:
  sudo systemctl stop discord_bot_v2
  venv/bin/python -m poring_food.reset_world --yes
  sudo systemctl start discord_bot_v2
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from . import worldcfg
from .clock import now_kst
from .config import STATE_DIR

KEEP = {"news.json", "market.json", "signals_cache.json", "metrics.json", "bookoasis_state.json", "plex_watch.json", "facilities.json", "book_watch.json",
        ".names_v2_migrated", ".names_v3_migrated"}


def reset(state_dir: str = STATE_DIR, backup: bool = True, now=None) -> dict:
    """반환: {"backup": 백업 폴더(없으면 ""), "removed": [지운 것], "kept": [남긴 것]}."""
    now = now or now_kst()
    out = {"backup": "", "removed": [], "kept": []}
    if not os.path.isdir(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    if backup and os.listdir(state_dir):
        dest = os.path.join(os.path.dirname(os.path.abspath(state_dir)), f"poring_food_archive_{now:%Y%m%d_%H%M}")
        shutil.copytree(state_dir, dest, dirs_exist_ok=True)
        out["backup"] = dest
    for name in sorted(os.listdir(state_dir)):
        path = os.path.join(state_dir, name)
        if name in KEEP:
            out["kept"].append(name)
            continue
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        out["removed"].append(name)
    from . import store
    store._ready.clear()  # world.db를 지웠으니 다음에 열 때 표를 다시 만든다
    with open(os.path.join(state_dir, worldcfg.START_PATH_NAME), "w", encoding="utf-8") as f:
        json.dump({"date": now.strftime("%Y-%m-%d"), "world": worldcfg.STREET}, f, ensure_ascii=False)
    from . import chronicle
    title = f"{worldcfg.STREET}의 시작"
    chronicle.open_thread(title, worldcfg.PREMISE, scope="")
    chronicle.update_thread(title, resolved=True, resolution=worldcfg.PREMISE, permanent=True)
    return out


def main(argv: list[str]) -> int:
    if "--yes" not in argv:
        print(f"이 명령은 {STATE_DIR}의 이야기 기록을 모두 지웁니다 (백업은 남깁니다). 실행하려면 --yes를 붙이세요.")
        return 1
    res = reset(backup="--no-backup" not in argv)
    print(f"[초기화] 백업: {res['backup'] or '없음'}")
    print(f"[초기화] 지움 {len(res['removed'])}개: {', '.join(res['removed'])}")
    print(f"[초기화] 남김: {', '.join(res['kept']) or '없음'}")
    print(f"[초기화] '{worldcfg.STREET}' 1일차 시작 ({now_kst():%Y-%m-%d})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
