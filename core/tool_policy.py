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
)

# 이름만으로는 안전을 보장할 수 없는 범용 패스스루 - 어떤 API를 부를지 인자로 결정되므로
# 이름에 위험한 동사가 없어도 강제로 관리자 전용 처리한다.
_FORCE_ADMIN_ONLY = frozenset({
    "call_api",
})

# 위험한 동사가 이름에 우연히 들어 있지만 실제로는 조회 전용인 오탐 예외.
_SAFE_OVERRIDE = frozenset({
    "run_readonly_query",
    "read_query",
})


def is_server_affecting(tool_name: str) -> bool:
    """이 도구가 서버 상태를 바꿀 수 있는지(=관리자만 써야 하는지)."""
    name = (tool_name or "").lower()
    if name in _SAFE_OVERRIDE:
        return False
    if name in _FORCE_ADMIN_ONLY:
        return True
    return any(s in name for s in _RISKY_SUBSTRINGS)
