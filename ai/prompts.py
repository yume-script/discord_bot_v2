"""
기존 봇에서 CLASSIFIER_PROMPT에 needs_mcp 플래그를 추가해 의도 분류 후
필요할 때만 MCP tool을 바인딩하는 방식으로 정착했다. 새 봇은 이걸 초기 설계로 둔다.
"""

CLASSIFIER_PROMPT = """\
다음 사용자 메시지를 분석해서 JSON으로만 답하세요.
{{
  "needs_mcp": bool,   // 외부 도구(파일/DB/API 조회 등)가 필요한 질문이면 true
  "intent": str        // 짧은 의도 요약
}}

사용자 메시지: {message}
"""

RESPONSE_SYSTEM_PROMPT = """\
너는 디스코드 봇 애순이야. 아래 대화 맥락과 (있다면) 도구 실행 결과를 참고해서
자연스럽고 간결하게 답해.
"""
