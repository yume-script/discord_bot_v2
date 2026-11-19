"""관리자 확인/취소 답장 알아듣기 (core/pending_actions.is_confirm/is_cancel)"""
import pytest

from core import pending_actions as pa


@pytest.mark.parametrize("t", ["확인", "확인!", "실행.", "하나야 확인", "하나야, 확인해줘", "아메하나 실행해 주세요", "응 확인",
                               "ㅇㅇ 실행", "<@123456> 확인", "확인해요", "확인 ㄱㄱ", "네 확인합니다"])
def test_confirm(t):
    assert pa.is_confirm(t)
    assert not pa.is_cancel(t)


@pytest.mark.parametrize("t", ["취소", "하나야 취소", "취소해줘", "응 취소할게"])
def test_cancel(t):
    assert pa.is_cancel(t)
    assert not pa.is_confirm(t)


@pytest.mark.parametrize("t", ["확인 말고 취소", "확인했어? 뭐야", "업데이트 확인해봐", "하나야", "", "확인좀 해볼까"])
def test_neither(t):
    assert not pa.is_confirm(t)
