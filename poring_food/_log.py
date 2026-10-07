"""
포링푸드 모듈들의 print()를 봇 로그(logging)로 보낸다.

cron 시절엔 print()가 그대로 cron 출력이 됐지만, 봇(systemd) 안에서는 stdout이 버퍼링돼서
실행 결과("전송 완료", "[에러] ... 실패")가 journalctl에 바로 안 보인다. 각 모듈이
`from ._log import pf_print as print`로 이 함수를 쓰게 해서, 원래 메시지를 그대로
INFO:poring_food 로그로 남긴다. "[에러]/[오류]/[경고]/[치명적"으로 시작하면 WARNING으로 올린다.
"""
import logging

_logger = logging.getLogger("poring_food")
_WARN_PREFIXES = ("[에러]", "[오류]", "[경고]", "[치명적")


def pf_print(*args, sep=" ", end="\n", file=None, flush=False):  # noqa: ARG001 - print()와 같은 시그니처
    text = sep.join(str(a) for a in args).strip("\n")
    if not text:
        return
    level = logging.WARNING if text.lstrip().startswith(_WARN_PREFIXES) else logging.INFO
    _logger.log(level, text)
