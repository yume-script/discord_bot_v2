"""
어떤 도구(MCP든 로컬이든)가 "서버에 영향을 준다"(쓰기/파괴적)고 볼지 판단한다.
ai/rag_engine.py의 도구 실행 루프가 이 판단으로 관리자가 아니면 실행 자체를 막는다.

[설계 원칙] 화이트리스트가 아니라 블랙리스트다 - 앞으로 MCP 서버/도구가 계속 늘어날 걸
전제로(ai/mcp_manager.py 주석 참고), 새 도구를 추가하면서 깜빡 잊고 이 파일을 안 고쳐도
이름에 위험한 동사가 들어 있으면 기본적으로 막힌다. 반대로 안전한 조회 도구가 실수로
막히는 건 성가시지만 위험하지는 않다 - 관리자가 요청하면 언제든 되고, _SAFE_OVERRIDE에
한 줄 추가하면 풀린다.

부분 문자열 매칭이라, mcp_manager.py가 이름 충돌 시 "{서버명}_{도구명}"으로 개명해도
(예: docker_bookoasis_stop_container) 그대로 걸린다.
"""
from __future__ import annotations

from ai.mcp_manager import is_admin_only_tool

# 이름에 이 중 하나라도 들어 있으면 기본적으로 관리자 전용.
# (예시: write_file/write_query, create_table/create_container, delete_entities,
#  remove_container, update_book_metadata, set_balance, bulk_set_favorite,
#  propose_bulk_*, bds_send_command, start/stop/restart_container, run_container,
#  pull_image, exec_in_container, add_observations, edit_file, move_file)
_RISKY_SUBSTRINGS = (
    "write", "create", "delete", "remove", "drop", "update", "set_", "_set",
    "insert", "exec", "send_command", "start", "stop", "restart", "kill",
    "prune", "upload", "mkdir", "rmdir", "move_", "edit_", "add_", "bulk",
    "propose", "patch", "replace", "truncate", "push", "run_", "pull",
    "send", "post", "command", "shell", "reset", "clear", "purge", "save",
    "rename", "reboot", "shutdown", "deploy", "grant", "revoke", "transfer",
)

# 이름만으로는 안전을 보장할 수 없는 범용 패스스루 - 어떤 API를 부를지 인자로 결정되므로
# 이름에 위험한 동사가 없어도 강제로 관리자 전용 처리한다.
_FORCE_ADMIN_ONLY = frozenset({
    "call_api",
    # 대화 로그 SQLite 조회 - 읽기 전용이긴 하지만 모든 카톡방/디스코드 채널의 대화가 한
    # 테이블에 있어서, 누구나 쓸 수 있으면 아무 방에서나 다른 방 대화를 통째로 볼 수 있다.
    "read_query",
    # ai/discord_reader.py - 토큰/자격정보 메모 채널까지 읽을 수 있는 범용 채널 리더.
    # (지금은 rag_engine에 연결돼 있지 않지만, 연결하는 순간 바로 막히도록 미리 등록)
    "read_discord_channel",
})

# 위험한 동사가 이름에 우연히 들어 있지만 실제로는 조회 전용인 오탐 예외.
# run_readonly_query는 BookOasis MCP 서버가 서버 쪽에서 읽기 전용을 강제한다는 전제다 -
# 그 전제가 깨지면(쓰기 SQL이 통과하면) 여기서 빼야 한다.
_SAFE_OVERRIDE = frozenset({
    "run_readonly_query",
    # 지식그래프 메모리에 "기억해줘"를 쌓는 추가 전용 도구 - 프롬프트가 누구에게나 이 기능을
    # 안내하므로 일반 유저도 쓸 수 있게 연다. 지우는 쪽(delete_*)은 계속 관리자 전용.
    "create_entities",
    "create_relations",
    "add_observations",
})


def is_server_affecting(tool_name: str) -> bool:
    """이 도구가 서버 상태를 바꿀 수 있는지(=관리자만 써야 하는지)."""
    # 서버 단위 지정이 이름 규칙보다 우선한다 - 예: filesystem 서버의 read_file은 이름만
    # 보면 안전한 조회지만 /mnt 아래 .env까지 읽을 수 있으므로 서버째로 관리자 전용이다.
    if is_admin_only_tool(tool_name):
        return True
    name = (tool_name or "").lower()
    if name in _SAFE_OVERRIDE:
        return False
    if name in _FORCE_ADMIN_ONLY:
        return True
    return any(s in name for s in _RISKY_SUBSTRINGS)
