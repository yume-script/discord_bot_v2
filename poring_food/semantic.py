"""
기억 의미 검색 - 로컬 임베딩 모델로 "단어는 달라도 뜻이 비슷한" 기억을 찾는다.

예) 지금 상황 "부장님이 또 야근을 시켰다" -> 기억 "지난주 밤 11시까지 남아서 보고서를 고쳤다"
    (겹치는 단어가 없어서 키워드 검색으로는 못 찾던 기억)

- 모델: 라그나로크M 검색(ai/ragm_search.py)과 같은 jhgan/ko-sroberta-multitask (한국어, 로컬, API 키/요금 없음 -
  무료 LLM 키의 하루 요청 한도를 쓰지 않는다). PORING_EMBEDDING_MODEL로 바꿀 수 있다.
- 벡터는 world.db memory_vecs 테이블에 기억 id별로 저장한다.
- [변경] 처음엔 떠올릴 때마다 벡터가 없는 기억을 그 자리에서 64개씩 만들었더니, 에이전트 40명 x 64개를 저사양
  CPU에서 매시 회차 안에 만드느라 회차가 10분 넘게 늘어지고 그동안 디스코드가 조용했다. 이제 판단 중에는
  지금 상황 한 줄만 임베딩하고, 벡터가 없는 기억은 백그라운드 스레드가 조금씩(EMBED_BATCH개씩, 쉬어 가며,
  CPU 스레드 PORING_EMBEDDING_THREADS개) 채운다. 아직 벡터가 없는 기억은 키워드 관련도로만 본다.
- 읽은 벡터는 메모리에 캐시한다 (float32 array라 기억 1만 개에 30MB 정도).
- sentence-transformers가 없거나 모델을 못 불러오면 조용히 꺼지고 키워드 검색만 쓴다. PORING_MEMORY_SEMANTIC=0이면 끔.
"""
from __future__ import annotations

import os
import threading
import time
from array import array

from . import store
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)

ENABLED = os.getenv("PORING_MEMORY_SEMANTIC", "1") not in ("0", "false", "False", "")
MODEL_NAME = os.getenv("PORING_EMBEDDING_MODEL", "jhgan/ko-sroberta-multitask")
THREADS = int(os.getenv("PORING_EMBEDDING_THREADS", "1"))
EMBED_BATCH = 8        # 백그라운드가 한 번에 만드는 벡터 수 (작게 - 판단 중 질의 임베딩이 오래 기다리지 않게)
EMBED_PAUSE_SEC = 2.0  # 묶음 사이에 쉬는 시간 (CPU를 계속 붙잡지 않게)

_model = None
_failed = False
_load_lock = threading.Lock()
_encode_lock = threading.Lock()   # 판단 중 질의 임베딩과 백그라운드 채우기가 모델을 동시에 쓰지 않게
_cache: dict[int, array] = {}
_pending: dict[int, str] = {}     # 벡터를 만들 기억 {id: text}
_pending_lock = threading.Lock()
_wake = threading.Event()
_worker: threading.Thread | None = None


def _load():
    global _model, _failed
    if _model is not None or _failed or not ENABLED:
        return _model
    with _load_lock:
        if _model is None and not _failed:
            try:
                import torch
                torch.set_num_threads(max(1, THREADS))
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME, device="cpu")
                print(f"[기억] 의미 검색 모델 로드: {MODEL_NAME} (CPU 스레드 {THREADS})")
            except Exception as e:  # noqa: BLE001
                _failed = True
                print(f"[기억] 의미 검색을 못 써서 키워드 검색만 씀: {type(e).__name__}: {e}")
    return _model


def _encode(texts: list[str]) -> list[list[float]] | None:
    model = _load()
    if model is None:
        return None
    with _encode_lock:
        vecs = model.encode(texts, batch_size=EMBED_BATCH, normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def available() -> bool:
    return _load() is not None


def _pack(v) -> bytes:
    return array("f", v).tobytes()


def _unpack(b: bytes) -> array:
    a = array("f")
    a.frombytes(b)
    return a


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


# ===================================================================== 백그라운드 채우기
def process_pending(limit: int = EMBED_BATCH) -> int:
    """기다리는 기억 중 limit개의 벡터를 만들어 저장한다. 반환: 만든 수 (백그라운드 스레드/테스트가 부른다)."""
    with _pending_lock:
        batch = list(_pending.items())[:limit]
    if not batch:
        return 0
    try:
        vecs = _encode([t for _, t in batch]) or []
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 기억 벡터 만들기 실패: {e}")
        vecs = []
    rows = {i: _pack(v) for (i, _), v in zip(batch, vecs)}
    if rows:
        store.set_memory_vecs(rows)
        for i, b in rows.items():
            _cache[i] = _unpack(b)
    with _pending_lock:
        for i, _ in batch:
            _pending.pop(i, None)
    return len(rows)


def _run_worker() -> None:
    done, started = 0, time.time()
    while True:
        _wake.wait()
        n = process_pending()
        done += n
        with _pending_lock:
            left = len(_pending)
        if not left:
            _wake.clear()
            if done:
                print(f"[기억] 기억 벡터 {done}개 채움 ({time.time() - started:.0f}초)")
            done, started = 0, time.time()
        time.sleep(EMBED_PAUSE_SEC if n else 5)


def _enqueue(mems: list[dict]) -> None:
    global _worker
    if not mems:
        return
    with _pending_lock:
        for m in mems:
            _pending.setdefault(m["id"], m["text"])
    if _worker is None:
        _worker = threading.Thread(target=_run_worker, name="memory-embed", daemon=True)
        _worker.start()
    _wake.set()


# ===================================================================== 검색
def similarities(query: str, mems: list[dict]) -> dict[int, float]:
    """{기억 id: 지금 상황과의 유사도 0~1} - 벡터가 이미 있는 기억만. 없는 건 백그라운드에 맡긴다. 못 쓰면 {}."""
    query = (query or "").strip()
    if not query or not mems or not ENABLED:
        return {}
    try:
        qv = _encode([query[:500]])
        if not qv:
            return {}
        ids = [m["id"] for m in mems if m.get("id") is not None]
        need = [i for i in ids if i not in _cache]
        if need:
            for i, b in store.memory_vecs(need).items():
                _cache[i] = _unpack(b)
        _enqueue([m for m in mems if m.get("id") is not None and m["id"] not in _cache])
        q = qv[0]
        return {i: max(0.0, _dot(q, _cache[i])) for i in ids if i in _cache}
    except Exception as e:  # noqa: BLE001 - 의미 검색이 실패해도 키워드로 떠올리면 된다
        print(f"[경고] 기억 의미 검색 실패: {e}")
        return {}
