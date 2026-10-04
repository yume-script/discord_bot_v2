"""
라그나로크M(romel.wiki) 지식 검색 도구.

기존 discord_bot(v1) 프로젝트가 이미 완성해둔 스크래핑+임베딩 파이프라인을 그대로
재사용한다 - 새로 스크래퍼를 만들지 않는다:
  1) /mnt/discord_bot/cron-script/app_romel_scraper.py 등이 romel.wiki/네이버블로그/
     게임 이벤트를 매일/매주 크론으로 스크래핑해서 jsonl로 저장
  2) /mnt/discord_bot/cron-script/ingest.py가 그 jsonl을 로컬 임베딩 모델
     (jhgan/ko-sroberta-multitask, HuggingFace, API 키 불필요)로 Chroma 벡터DB에 반영
     (/mnt/discord_bot/llm_vector_db_local/{room_id})
  3) 이 파일은 그 벡터DB를 "읽기 전용"으로 열어서 검색만 한다 - 스크래핑/ingest는
     기존 discord_bot 쪽 cron이 계속 담당(건드리지 않음).

라그나로크M 데이터가 있는 room_id는 18221226698539974로 확인됨(ragM_equip.jsonl,
ragM_pet.jsonl, 네이버블로그 스크랩, 게임 이벤트 정보가 이 방 아래 있음).

[메모리 판단] discord_bot_v2가 도는 서버는 24GB 중 21GB가 여유라(2026-09-22 확인),
임베딩 모델을 이 프로세스 안에 직접 상주시켜도 부담이 없다고 판단해서 이 방식으로
구현했다 - 별도 마이크로서비스나 MCP 프로세스 분리는 안 함. 첫 검색 요청 때 딱 한 번만
모델/DB를 로드하고(_get_vectorstore가 캐싱), 그 뒤로는 프로세스가 떠있는 동안 재사용한다.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

log = logging.getLogger("ragm_search")

VECTOR_DB_PATH = "/mnt/discord_bot/llm_vector_db_local/18221226698539974"
EMBEDDING_MODEL_NAME = "jhgan/ko-sroberta-multitask"  # ingest.py와 반드시 동일해야 함

_vectorstore = None  # 지연 로딩 캐시 - 프로세스 생애주기 동안 1회만 로드
_load_failed = False  # 한 번 로드 실패하면 매 질문마다 재시도하며 수초씩 낭비하지 않게 함


def _get_vectorstore():
    global _vectorstore, _load_failed
    if _vectorstore is not None or _load_failed:
        return _vectorstore

    import os
    if not os.path.isdir(VECTOR_DB_PATH):
        log.warning("라그나로크M 벡터DB 경로를 찾을 수 없음: %s", VECTOR_DB_PATH)
        _load_failed = True
        return None

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma

        embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu"},
        )
        _vectorstore = Chroma(persist_directory=VECTOR_DB_PATH, embedding_function=embeddings)
        log.info("라그나로크M 벡터DB 로드 완료: %s", VECTOR_DB_PATH)
    except Exception:
        log.exception("라그나로크M 벡터DB/임베딩 모델 로드 실패")
        _load_failed = True
        return None

    return _vectorstore


@tool
def search_ragnarok_wiki(query: str, k: int = 5) -> str:
    """라그나로크M(romel.wiki) 관련 정보(카드/모자/장비/펫/아티팩트/기억석/추출/음식/몬스터,
    게임 이벤트, 공략 정보)를 검색한다. 카드/장비/펫 등 아이템 이름을 알고 있으면 그 이름을
    query에 그대로 넣어라(예: "천사의 속삭임"). "저항 옵션 있는 모자 추천해줘"처럼 애매한
    질문도 의미 기반으로 검색되니 그대로 넣어도 된다. k는 가져올 결과 개수(기본 5)."""
    vectorstore = _get_vectorstore()
    if vectorstore is None:
        return "라그나로크M 지식 검색 기능을 지금 쓸 수 없어요 (벡터DB 로드 실패)."

    try:
        docs = vectorstore.similarity_search(query, k=k)
    except Exception as exc:
        log.exception("라그나로크M 벡터DB 검색 실패")
        return f"검색 중 오류가 발생했어요: {exc}"

    if not docs:
        return f"'{query}'에 대한 라그나로크M 정보를 찾지 못했어요."

    blocks = []
    for doc in docs:
        name = doc.metadata.get("name", "")
        header = f"[{name}]" if name else ""
        blocks.append(f"{header}\n{doc.page_content}".strip())
    return "\n\n---\n\n".join(blocks)


RAGM_TOOLS = [search_ragnarok_wiki]
