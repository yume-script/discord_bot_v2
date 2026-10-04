"""
AI Horde(https://aihorde.net) 연동. 기존 봇의 horde_gen.py + aesun_img_gen.py를
통합했던 horde_image_engine.py 역할을 대체한다.

흐름: POST /generate/async 로 제출 -> GET /generate/check/{id} 폴링 ->
완료되면 GET /generate/status/{id}로 결과(base64 or url) 회수.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from ai.backends.base import GeneratedImage, ImageBackend, ImageGenError
from config import settings

log = logging.getLogger("horde")

HORDE_BASE_URL = "https://aihorde.net/api/v2"
POLL_INTERVAL_SEC = 4
MAX_WAIT_SEC = 1800


class HordeBackend(ImageBackend):
    name = "horde"

    def __init__(self):
        if not settings.HORDE_API_KEY:
            raise ImageGenError("HORDE_API_KEY가 설정되지 않았습니다.")
        self._headers = {
            "apikey": settings.HORDE_API_KEY,
            "Client-Agent": "discord_bot_v2:1.0:yume-script",
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        prompt: str,
        *,
        style: str | None = None,
        negative_prompt: str | None = None,
    ) -> GeneratedImage:
        model = style or settings.HORDE_DEFAULT_MODEL
        full_prompt = prompt if not negative_prompt else f"{prompt} ### {negative_prompt}"

        # Horde는 모든 요청에 Client-Agent 헤더를 요구하므로 클라이언트 기본 헤더로 붙인다.
        async with httpx.AsyncClient(timeout=15, headers=self._headers) as client:
            job_id = await self._submit(client, full_prompt, model)
            try:
                await self._wait_until_done(client, job_id)
            except BaseException:
                # 타임아웃/실패/취소 시 Horde 쪽 작업도 취소해서 kudos와 워커 자원을 돌려준다.
                await self._cancel(client, job_id)
                raise
            return await self._fetch_result(client, job_id, model)

    async def _cancel(self, client: httpx.AsyncClient, job_id: str) -> None:
        try:
            await client.delete(f"{HORDE_BASE_URL}/generate/status/{job_id}")
        except Exception:  # noqa: BLE001 - 취소는 최선 노력, 실패해도 원래 예외를 올린다
            log.warning("horde 작업 취소 실패 (job_id=%s)", job_id, exc_info=True)

    async def _submit(self, client: httpx.AsyncClient, prompt: str, model: str) -> str:
        body = {
            "prompt": prompt,
            "params": {
                "sampler_name": "k_euler_a",
                "cfg_scale": 7,
                "width": 512,
                "height": 512,
                "steps": 25,
                "n": 1,
            },
            "models": [model],
            "nsfw": False,
            "r2": False,  # base64로 직접 받기
        }
        resp = await client.post(f"{HORDE_BASE_URL}/generate/async", json=body)
        if resp.status_code >= 300:
            raise ImageGenError(f"horde 제출 실패 ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise ImageGenError(f"horde 응답에 job id 없음: {data}")
        return job_id

    async def _wait_until_done(self, client: httpx.AsyncClient, job_id: str) -> None:
        waited = 0
        while waited < MAX_WAIT_SEC:
            resp = await client.get(f"{HORDE_BASE_URL}/generate/check/{job_id}")
            if resp.status_code >= 300:
                raise ImageGenError(f"horde 상태 확인 실패 ({resp.status_code})")
            status = resp.json()
            if status.get("faulted"):
                raise ImageGenError(f"horde 작업 실패(faulted): job_id={job_id}")
            if status.get("done"):
                return
            await asyncio.sleep(POLL_INTERVAL_SEC)
            waited += POLL_INTERVAL_SEC
        raise ImageGenError(f"horde 작업 타임아웃({MAX_WAIT_SEC}s): job_id={job_id}")

    async def _fetch_result(
        self, client: httpx.AsyncClient, job_id: str, model: str
    ) -> GeneratedImage:
        resp = await client.get(f"{HORDE_BASE_URL}/generate/status/{job_id}")
        if resp.status_code >= 300:
            raise ImageGenError(f"horde 결과 조회 실패 ({resp.status_code})")
        data = resp.json()
        generations = data.get("generations") or []
        if not generations:
            raise ImageGenError(f"horde 결과에 생성물 없음: job_id={job_id}")

        gen = generations[0]
        img_field = gen["img"]

        if img_field.startswith("http"):
            # 외부 저장소(R2 등) URL이라 apikey가 담긴 Horde 헤더를 보내지 않도록 별도 클라이언트로 받는다.
            async with httpx.AsyncClient(timeout=30) as plain_client:
                img_resp = await plain_client.get(img_field)
            if img_resp.status_code >= 300:
                raise ImageGenError(f"horde 이미지 다운로드 실패 ({img_resp.status_code}): job_id={job_id}")
            image_bytes = img_resp.content
        else:
            image_bytes = base64.b64decode(img_field)

        return GeneratedImage(
            image_bytes=image_bytes,
            backend=self.name,
            model=gen.get("model", model),
            seed=str(gen.get("seed")) if gen.get("seed") is not None else None,
        )
