"""Shared test helpers (a real uvicorn server in a subprocess)."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, db, **env):
        self.db, self.env, self.port, self.proc = db, env, free_port(), None
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "app.api:create_app",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
                "--timeout-graceful-shutdown",
                "2",  # open event streams must not hold the server up on SIGTERM
            ],  # fmt: skip
            cwd=ROOT,
            env={**os.environ, "DB_PATH": str(self.db), **self.env},
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/healthz").status_code == 200:
                    return self
            except httpx.TransportError:
                time.sleep(0.1)
        self.stop(signal.SIGKILL)
        raise RuntimeError("server did not start")

    def stop(self, sig):
        self.proc.send_signal(sig)
        self.proc.wait(timeout=10)


def parse_sse(text):
    """Split an SSE body into messages: {"event","id","data"} or {"comment"}."""
    out = []
    for block in text.strip().split("\n\n"):
        msg = {}
        for line in block.splitlines():
            if line.startswith(":"):
                msg["comment"] = line[1:].strip()
            else:
                key, _, value = line.partition(": ")
                msg[key] = value
        out.append(msg)
    return out


class Stream:
    """Drives the ASGI app directly so a stream can be read, then disconnected."""

    def __init__(self, app, path, headers=None, spec_version="2.3"):
        self.chunks: asyncio.Queue[str] = asyncio.Queue()
        self.disconnected = asyncio.Event()
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(),
            "query_string": b"", "scheme": "http", "server": ("t", 80), "client": ("t", 1),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }  # fmt: skip
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self):
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if self.disconnected.is_set():
            raise OSError("client went away")  # what servers do for ASGI spec >= 2.4
        if message["type"] == "http.response.body" and message.get("body"):
            await self.chunks.put(message["body"].decode())

    async def next(self, timeout=1.0):
        return await asyncio.wait_for(self.chunks.get(), timeout)

    async def next_event(self, timeout=1.0):
        """Next non-comment message."""
        while True:
            msg = parse_sse(await self.next(timeout))[0]
            if "comment" not in msg:
                return msg

    async def no_event_for(self, seconds):
        """Assert nothing but keepalive comments arrive for `seconds`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while (left := deadline - loop.time()) > 0:
            try:
                msg = parse_sse(await self.next(left))[0]
            except TimeoutError:
                return
            assert "comment" in msg, f"unexpected event {msg}"

    def disconnect(self):
        self.disconnected.set()
