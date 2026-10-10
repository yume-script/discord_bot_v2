"""한국어 조사 붙이기 - 이름 끝 받침에 따라 이/가, 와/과, 랑/이랑, 은/는, 을/를을 고른다 ("성덕선가" -> "성덕선이")."""
from __future__ import annotations

PAIRS = {"이": ("이", "가"), "가": ("이", "가"), "와": ("과", "와"), "과": ("과", "와"), "랑": ("이랑", "랑"),
         "이랑": ("이랑", "랑"), "은": ("은", "는"), "는": ("은", "는"), "을": ("을", "를"), "를": ("을", "를")}


def has_batchim(word: str) -> bool:
    ch = (word or " ")[-1]
    if not ("가" <= ch <= "힣"):
        return False  # 한글이 아니면 받침 없는 쪽으로
    return (ord(ch) - ord("가")) % 28 != 0


def j(word: str, particle: str) -> str:
    """j("성덕선", "가") -> "성덕선이", j("소라", "와") -> "소라와"."""
    with_b, without_b = PAIRS.get(particle, (particle, particle))
    return word + (with_b if has_batchim(word) else without_b)
