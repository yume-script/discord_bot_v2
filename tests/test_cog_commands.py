"""슬래시 명령어 등록 - 내린 명령(/포링푸드사건, /포링푸드메시지)은 없어야 한다."""
import pytest


def test_registered_commands():
    pytest.importorskip("discord")
    from cogs.poring_food import PoringFood
    names = {c.name for c in PoringFood.__cog_app_commands__}
    assert {"포링푸드실행", "포링푸드모임", "포링푸드연대기", "포링푸드이웃", "포링푸드명단", "포링푸드골목"} <= names
    assert not names & {"포링푸드사건", "포링푸드메시지"}
