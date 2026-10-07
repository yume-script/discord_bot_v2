import os
import json
import requests
from datetime import datetime, timedelta

from config import settings

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from . import runtime
from .config import DISCORD_CHANNEL_ID, DISCORD_WEBHOOK_URL, ROOM_ID, STATUS_OUT_PATH, HISTORY_LOG_PATH, HISTORY_RETENTION_DAYS


def save_to_file(status_data):
    try:
        os.makedirs(os.path.dirname(STATUS_OUT_PATH), exist_ok=True)
        with open(STATUS_OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
        print(f"[성공] 상태 저장 완료: {STATUS_OUT_PATH}")
    except Exception as e:
        print(f"[에러] 파일 저장 실패: {e}")


def append_to_history(status_data, character="애순이"):
    """
    [신규] "어제 뭐 했어?" 같은 질문에 답하려면 현재 스냅샷만으로는 부족해서, 매 실행마다
    한 줄씩 누적 기록한다(JSONL). 전체 status_data를 다 넣으면 파일이 금방 커지니
    질문에 답하는 데 필요한 핵심 필드만 추린다. 오래된 기록(HISTORY_RETENTION_DAYS 이전)은
    매번 같이 정리해서 파일이 무한정 커지지 않게 한다.
    [변경] 애순이 전용이었던 로그를 다인물 공용으로 확장 - character 필드로 구분한다.
    다른 인물들의 기록은 characters.append_character_history()가 같은 파일에 append한다
    (이쪽은 정리를 안 하니, 정리는 애순이 쪽 이 함수가 실행될 때 같이 되는 셈).
    """
    entry = {
        "character": character,
        "timestamp": status_data.get("timestamp"),
        "time_tag": status_data.get("time_tag"),
        "title": status_data.get("title"),
        "location": status_data.get("location"),
        "activity": status_data.get("activity"),
        "state": status_data.get("state"),
        "mood": status_data.get("mood"),
        "narrative": status_data.get("narrative"),
    }
    try:
        os.makedirs(os.path.dirname(HISTORY_LOG_PATH), exist_ok=True)

        cutoff = datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)
        kept_lines = []
        if os.path.exists(HISTORY_LOG_PATH):
            with open(HISTORY_LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        old = json.loads(line)
                        old_ts = datetime.fromisoformat(old.get("timestamp", ""))
                        if old_ts >= cutoff:
                            kept_lines.append(line)
                    except Exception:
                        continue  # 깨진 줄은 버림

        kept_lines.append(json.dumps(entry, ensure_ascii=False))

        with open(HISTORY_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(kept_lines) + "\n")
        print(f"[성공] 히스토리 기록 완료 ({len(kept_lines)}건 보관 중): {HISTORY_LOG_PATH}")
    except Exception as e:
        print(f"[에러] 히스토리 기록 실패: {e}")


def send_to_discord(text):
    """
    매시 일지를 디스코드에 올린다. [변경] 봇으로 합치면서 PORING_DISCORD_CHANNEL_ID가 있으면 봇
    계정이 그 채널에 직접 올리고(cogs/poring_food.py가 연결), 없으면 예전처럼 웹훅으로 보낸다.
    """
    if runtime.has_discord_sender():
        try:
            runtime.send_discord(text)
            print("[성공] Discord 전송 완료 (봇)")
            return
        except Exception as e:
            # 예: 403 Missing Access - 봇이 그 채널(서버)에 권한이 없음. 웹훅이 있으면 웹훅으로 다시 보낸다
            # (웹훅은 봇이 그 서버에 없어도 올릴 수 있다).
            hint = " - 봇에 그 채널의 '채널 보기/메시지 보내기' 권한이 있는지 확인" if "Missing Access" in str(e) else ""
            print(f"[에러] Discord 전송 실패 (봇, 채널 {DISCORD_CHANNEL_ID}): {e}{hint}")
            if not DISCORD_WEBHOOK_URL:
                return
            print("[정보] 웹훅(PORING_DISCORD_WEBHOOK_URL)으로 다시 보냅니다.")
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": text[:2000], "allowed_mentions": {"parse": []}},
            timeout=10,
        )
        resp.raise_for_status()
        print("[성공] Discord 전송 완료 (웹훅)")
    except Exception as e:
        print(f"[에러] Discord 전송 실패: {e}")


def send_to_local_bot(text):
    """
    카톡 방송. [변경] 예전 LOCAL_BOT_URL 대신 봇과 같은 카톡 브릿지(KATALK_BRIDGE_URL/reply)로
    같은 payload를 보낸다. 방은 PORING_KAKAO_ROOM_ID(예전 .env의 ROOM_ID).
    """
    if not settings.KATALK_BRIDGE_URL or not ROOM_ID:
        return
    payload = {"type": "text", "room": ROOM_ID, "data": text}
    try:
        resp = requests.post(f"{settings.KATALK_BRIDGE_URL}/reply", json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[성공] 카톡 전송 완료 (Room: {ROOM_ID})")
    except Exception as e:
        print(f"[에러] 카톡 전송 실패: {e}")
