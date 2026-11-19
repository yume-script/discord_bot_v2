# LiteLLM 이미지 + MCP 서버를 stdio로 띄우는 데 필요한 것 중 없는 것만 (node/npx, uv/uvx, ssh)
# main-latest(Chainguard/Wolfi)에는 node·npx가 이미 있다 - 다시 깔면 openssl 패키지가 부딪힌다
ARG LITELLM_IMAGE=ghcr.io/berriai/litellm:main-latest
FROM ${LITELLM_IMAGE}
USER root
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
    command -v uvx >/dev/null 2>&1 || pip install --no-cache-dir uv || python3 -m pip install --no-cache-dir uv; \
    node --version; uvx --version; ssh -V
