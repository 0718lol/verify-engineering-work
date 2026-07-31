#!/bin/sh
set -eu

if [ -n "${OLLAMA_PROXY_TARGET:-}" ]; then
    python3 /usr/local/bin/ollama_proxy.py "$OLLAMA_PROXY_TARGET" &
    proxy_pid=$!
    trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT INT TERM
    python3 -c 'import socket,time
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", 11434), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.05)
else:
    raise SystemExit("Ollama proxy did not start")'
fi

exec codex "$@"
