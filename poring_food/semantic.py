"""
기억 의미 검색 - 로컬 임베딩 모델로 "단어는 달라도 뜻이 비슷한" 기억을 찾는다.

예) 지금 상황 "부장님이 또 야근을 시켰다" -> 기억 "지난주 밤 11시까지 남아서 보고서를 고쳤다"
    (겹치는 단어가 없어서 키워드 검색으로는 못 찾던 기억)

- 모델: 라그나로크M 검색(ai/ragm_search.py)과 같은 jhgan/ko-sroberta-multitask (한국어, 로컬, API 키/요금 없음 -
  무료 LLM 키의 하루 요청 한도를 쓰지 않는다). PORING_EMBEDDING_MODEL로 바꿀 수 있다.
- 벡터는 world.db memory_vecs 테이블에 기억 id별로 저장한다. 새 기억은 떠올릴 때 한 번에 몇 개씩 묶어서 만든다
  (예전 기억도 그렇게 조금씩 채워진다 - 한 번에 CPU를 오래 쓰지 않게).
- sentence-transformers가 없거나 모델을 못 불러오면 조용히 꺼지고 키워드 검색만 쓴다. PORING_MEMORY_SEMANTIC=0이면 끔.
"""
from __future__ import annotations

import math
import os
import threading
from array import array

from . import store
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)

ENABLED = os.getenv("PORING_MEMORY_SEMANTIC", "1") not in ("0", "false", "False", "")
MODEL_NAME = os.getenv("PORING_EMBEDDING_MODEL", "jhgan/ko-sroberta-multitask")
BATCH_MAX = 64  # 한 번 떠올릴 때 새로 만드는 벡터 수 상한

_model = None
_failed = False
_lock = threading.Lock()


def _load():
    global _model, _failed
    if _model is not None or _failed or not ENABLED:
        return _model
    with _lock:
        if _model is None and not _failed:
            try:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME, device="cpu")
                print(f"[기억] 의미 검색 모델 로드: {MODEL_NAME}")
            except Exception as e:  # noqa: BLE001
                _failed = True
                print(f"[기억] 의미 검색을 못 써서 키워드 검색만 씀: {type(e).__name__}: {e}")
    return _model


def _encode(texts: list[str]) -> list[list[float]] | None:
    model = _load()
    if model is None:
        return None
    vecs = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def available() -> bool:
    return _load() is not None


def _pack(v: list[float]) -> bytes:
    return array("f", v).tobytes()


def _unpack(b: bytes) -> list[float]:
    a = array("f")
    a.frombytes(b)
    return list(a)


def _dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def similarities(query: str, mems: list[dict]) -> dict[int, float]:
    """{기억 id: 지금 상황과의 유사도 0~1}. 못 쓰면 {}. 벡터가 없는 기억은 BATCH_MAX개까지 만들어 저장한다."""
    query = (query or "").strip()
    if not query or not mems or not ENABLED:
        return {}
    try:
        qv = _encode([query[:500]])
        if not qv:
            return {}
        ids = [m["id"] for m in mems if m.get("id") is not None]
        have = store.memory_vecs(ids)
        missing = [m for m in mems if m.get("id") is not None and m["id"] not in have][:BATCH_MAX]
        if missing:
            new = _encode([m["text"] for m in missing]) or []
            rows = {m["id"]: _pack(v) for m, v in zip(missing, new)}
            store.set_memory_vecs(rows)
            have.update(rows)
        q = qv[0]
        return {i: max(0.0, _dot(q, _unpack(b))) for i, b in have.items()}
    except Exception as e:  # noqa: BLE001 - 의미 검색이 실패해도 키워드로 떠올리면 된다
        print(f"[경고] 기억 의미 검색 실패: {e}")
        return {}
