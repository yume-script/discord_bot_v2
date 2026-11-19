# LiteLLM 이미지 + MCP 서버를 stdio로 띄우는 데 필요한 것 중 없는 것만 (node/npx, uv/uvx, ssh)
# main-latest(Chainguard/Wolfi)에는 node·npx가 이미 있고 pip가 없다
#  - uv/uvx: 공식 uv 이미지에서 실행 파일만 복사 (pip 없이)
#  - ssh: apk (이미지의 openssl과 설정 파일이 겹쳐 --force-overwrite)
ARG LITELLM_IMAGE=ghcr.io/berriai/litellm:main-latest
FROM ghcr.io/astral-sh/uv:latest AS uv
FROM ${LITELLM_IMAGE}
USER root
COPY --from=uv /uv /uvx /usr/local/bin/
RUN set -e; \
    if command -v apk >/dev/null 2>&1; then \
      command -v ssh  >/dev/null 2>&1 || apk add --no-cache --force-overwrite openssh-client; \
      command -v node >/dev/null 2>&1 || apk add --no-cache --force-overwrite nodejs npm; \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update; \
      command -v ssh  >/dev/null 2>&1 || apt-get install -y --no-install-recommends openssh-client; \
      command -v node >/dev/null 2>&1 || apt-get install -y --no-install-recommends nodejs npm; \
      rm -rf /var/lib/apt/lists/*; \
    fi; \
    node --version; uvx --version; ssh -V
