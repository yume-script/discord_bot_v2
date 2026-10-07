"""
포링푸드 비공개 채널에 봇들(아메하나 + 애순이/소라 등 보조 계정)이 들어올 수 있게 진단/설정하는 1회성 스크립트.

    cd /mnt/discord_bot_v2 && venv/bin/python scripts/grant_channel_access.py           # 진단 + 가능한 것 설정
    venv/bin/python scripts/grant_channel_access.py --check                              # 진단만 (아무것도 안 바꿈)
    venv/bin/python scripts/grant_channel_access.py --channel 1524285939365707868 --guild 591180628842774550

봇마다 순서대로 확인한다:
1) 토큰이 유효한가 (.env의 DISCORD_BOT_TOKEN, SIDE_BOT_KEYS의 {KEY}_BOT_TOKEN)
2) 서버에 들어와 있는가 - 없으면 초대 링크를 출력한다. 봇을 서버에 넣는 건 디스코드 정책상
   서버 관리자가 링크를 눌러 직접 승인해야 한다(코드로 불가).
3) 채널이 보이는가 - 안 보이면 "역할 관리" 권한이 있는 다른 봇(보통 아메하나)으로 그 채널의 권한
   덮어쓰기에 이 봇을 추가한다 (채널 보기 / 메시지 보내기 / 메시지 기록 보기만 허용).
   어떤 봇도 그 권한이 없으면 사람이 할 일을 안내한다.

디스코드 REST API만 쓴다(봇 본체를 따로 띄우지 않음). 봇 본체가 돌고 있어도 같이 실행해도 된다.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

API = "https://discord.com/api/v10"
DEFAULT_GUILD = "591180628842774550"
DEFAULT_CHANNEL = "1524285939365707868"
VIEW, SEND, HISTORY = 1 << 10, 1 << 11, 1 << 16
ALLOW = VIEW | SEND | HISTORY  # 68608
MANAGE_WEBHOOKS = 1 << 29
NAMES = {"aesun": "애순이", "sora": "소라", "kaje": "카제"}
WEBHOOK_BOT = "카제"  # 봇 토큰 없는 에이전트용 채널 웹훅을 만드는 봇 - "웹후크 관리"도 필요


def _tokens() -> list[tuple[str, str]]:
    out = [("아메하나(메인)", os.environ.get("DISCORD_BOT_TOKEN", ""))]
    keys = [k.strip().lower() for k in os.environ.get("SIDE_BOT_KEYS", "aesun,sora,kaje").split(",") if k.strip()]
    for must in ("aesun", "sora", "kaje"):
        if must not in keys:
            keys.append(must)
    for key in keys:
        out.append((NAMES.get(key, key), os.environ.get(f"{key.upper()}_BOT_TOKEN", "")))
    return out


def _get(client: httpx.Client, token: str, path: str) -> httpx.Response:
    return client.get(f"{API}{path}", headers={"Authorization": f"Bot {token}"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guild", default=os.environ.get("DISCORD_GUILD_ID") or DEFAULT_GUILD)
    ap.add_argument("--channel", default=os.environ.get("PORING_AGENT_CHANNEL_ID")
                    or os.environ.get("PORING_DISCORD_CHANNEL_ID") or DEFAULT_CHANNEL)
    ap.add_argument("--check", action="store_true", help="진단만 하고 아무것도 바꾸지 않는다")
    args = ap.parse_args()
    guild, channel = str(args.guild), str(args.channel)
    print(f"서버 {guild} / 채널 {channel}\n")

    bots = []
    with httpx.Client(timeout=15) as client:
        # 1) 토큰/서버/채널 상태 진단
        for label, token in _tokens():
            if not token:
                print(f"- {label}: 토큰 없음 (.env) - 건너뜀")
                continue
            me = _get(client, token, "/users/@me")
            if me.status_code != 200:
                print(f"- {label}: 토큰이 유효하지 않음 (HTTP {me.status_code}) - 건너뜀")
                continue
            user = me.json()
            app = _get(client, token, "/oauth2/applications/@me")
            app_id = app.json().get("id", user["id"]) if app.status_code == 200 else user["id"]
            in_guild = _get(client, token, f"/guilds/{guild}").status_code == 200
            sees = in_guild and _get(client, token, f"/channels/{channel}").status_code == 200
            bots.append({"label": label, "token": token, "id": user["id"], "user": user["username"],
                         "app_id": app_id, "in_guild": in_guild, "sees": sees})
            state = "채널 OK" if sees else ("서버에는 있음, 채널 안 보임" if in_guild else "서버에 없음")
            if sees and label == WEBHOOK_BOT:
                hooks_ok = _get(client, token, f"/channels/{channel}/webhooks").status_code == 200
                bots[-1]["hooks_ok"] = hooks_ok
                state += " / 웹후크 관리 " + ("OK" if hooks_ok else "없음")
            print(f"- {label} ({user['username']}, id {user['id']}): {state}")

        # 2) 서버에 없는 봇 -> 초대 링크
        missing = [b for b in bots if not b["in_guild"]]
        if missing:
            print("\n[서버 관리자가 직접 눌러야 하는 초대 링크] (봇을 서버에 넣는 건 코드로 불가)")
            for b in missing:
                print(f"- {b['label']}: https://discord.com/oauth2/authorize?client_id={b['app_id']}"
                      f"&scope=bot&permissions={ALLOW}&guild_id={guild}&disable_guild_select=true")

        # 3) 서버엔 있는데 채널이 안 보이는 봇 -> 채널을 볼 수 있는 봇이 권한 덮어쓰기 추가 시도
        need = [b for b in bots if b["in_guild"] and (not b["sees"] or b.get("hooks_ok") is False)]
        if not need:
            print("\n채널 권한 설정이 필요한 봇 없음." if not missing else "")
            return 0
        if args.check:
            print(f"\n[--check] 채널 권한 추가가 필요한 봇: {[b['label'] for b in need]} (설정은 하지 않음)")
            return 0
        granters = [b for b in bots if b["sees"]] + [b for b in bots if b["in_guild"] and not b["sees"]]
        failed = []
        for b in need:
            done = False
            wants = [ALLOW | MANAGE_WEBHOOKS, ALLOW] if b["label"] == WEBHOOK_BOT else [ALLOW]
            for g in granters:
                for allow in wants:
                    r = client.put(
                        f"{API}/channels/{channel}/permissions/{b['id']}",
                        headers={"Authorization": f"Bot {g['token']}"},
                        json={"type": 1, "allow": str(allow), "deny": "0"},
                    )
                    if r.status_code in (200, 204):
                        extra = " + 웹후크 관리" if allow & MANAGE_WEBHOOKS else ""
                        print(f"\n✅ {g['label']}가 채널 권한에 {b['label']}를 추가함 (보기/보내기/기록 보기{extra})")
                        done = bool(allow & MANAGE_WEBHOOKS) or b["label"] != WEBHOOK_BOT
                        break
                if done:
                    break
            if not done:
                failed.append(b)

        if failed:
            print("\n[직접 해야 함] 채널 권한을 바꿀 수 있는 봇이 없음(어느 봇도 이 채널의 '권한 관리'가 없음).")
            print("디스코드에서: 채널 이름 옆 톱니바퀴 → 권한 → 멤버 또는 역할 추가 → 아래 봇 추가 →")
            print("  채널 보기 / 메시지 보내기 / 메시지 기록 보기 ✅")
            for b in failed:
                print(f"  - {b['label']} ({b['user']})")
            print(f"  ({WEBHOOK_BOT}는 '웹후크 관리'도 ✅ - 봇 토큰 없는 에이전트들이 이 웹훅으로 말한다."
                  " 또는 채널 설정 → 연동 → 웹후크를 직접 만들고 URL을 .env PORING_AGENT_WEBHOOK_URL에 넣어도 된다)")
            print("다음부터 이 스크립트로 처리하려면 아메하나 봇을 이 채널에 넣고 '권한 관리'(Manage Permissions)도 허용해 두면 된다.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
