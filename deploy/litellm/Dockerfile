# LiteLLM 이미지 + MCP 서버를 stdio로 띄우는 데 필요한 것 (node/npx, uv/uvx, ssh)
# 디스코드 봇이 직접 띄우던 MCP 서버 일부를 LiteLLM MCP 게이트웨이가 대신 띄운다 (deploy/litellm/README.md)
# 쓰던 LiteLLM 이미지 태그로 바꿔도 된다 (빌드할 때 --build-arg LITELLM_IMAGE=...)
ARG LITELLM_IMAGE=ghcr.io/berriai/litellm:main-stable
FROM ${LITELLM_IMAGE}
USER root
# 이미지 바탕이 Debian이면 apt, Wolfi/Alpine이면 apk
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends nodejs npm openssh-client ca-certificates && rm -rf /var/lib/apt/lists/*; \
    else \
      apk add --no-cache nodejs npm openssh-client ca-certificates; \
    fi \
 && pip install --no-cache-dir uv \
 && node --version && uvx --version
