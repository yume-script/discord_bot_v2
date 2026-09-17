"""관리자(ADMIN_DISCORD_IDS) 판별 - cogs/admin.py, cogs/chat.py가 공유한다."""
from __future__ import annotations

from config import settings


def is_admin(user_id: int | str) -> bool:
    return str(user_id) in settings.ADMIN_DISCORD_IDS
