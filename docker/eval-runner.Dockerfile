FROM redis@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2

ARG CODEX_VERSION=0.144.0-alpha.4

RUN apk add --no-cache \
        ca-certificates \
        git \
        nodejs \
        npm \
        python3 \
        ripgrep \
    && npm install --global "@openai/codex@${CODEX_VERSION}" \
    && codex --version

COPY docker/ollama_proxy.py /usr/local/bin/ollama_proxy.py
COPY docker/codex-entrypoint.sh /usr/local/bin/codex-entrypoint
RUN chmod 0755 /usr/local/bin/ollama_proxy.py /usr/local/bin/codex-entrypoint

LABEL org.opencontainers.image.title="Verify Engineering Work evaluation runner"
LABEL org.opencontainers.image.source="https://github.com/0718lol/verify-engineering-work"

ENTRYPOINT ["/usr/local/bin/codex-entrypoint"]
CMD ["--help"]
