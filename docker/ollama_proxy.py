#!/usr/bin/env python3
"""Forward container-local Ollama traffic to a host endpoint."""

from __future__ import annotations

import selectors
import socket
import socketserver
import sys


class ProxyHandler(socketserver.BaseRequestHandler):
    upstream_host: str
    upstream_port: int

    def handle(self) -> None:
        with socket.create_connection(
            (self.upstream_host, self.upstream_port), timeout=10
        ) as upstream:
            self.request.setblocking(False)
            upstream.setblocking(False)
            selector = selectors.DefaultSelector()
            selector.register(self.request, selectors.EVENT_READ, upstream)
            selector.register(upstream, selectors.EVENT_READ, self.request)
            try:
                while True:
                    events = selector.select(timeout=60)
                    if not events:
                        continue
                    for key, _ in events:
                        destination = key.data
                        data = key.fileobj.recv(65536)
                        if not data:
                            return
                        destination.sendall(data)
            finally:
                selector.close()


class ThreadedProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        print("usage: ollama_proxy.py host:port", file=sys.stderr)
        return 2
    host, raw_port = sys.argv[1].rsplit(":", 1)
    ProxyHandler.upstream_host = host
    ProxyHandler.upstream_port = int(raw_port)
    with ThreadedProxy(("127.0.0.1", 11434), ProxyHandler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
