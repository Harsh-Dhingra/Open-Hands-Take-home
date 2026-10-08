"""Shared test helpers (a real uvicorn server in a subprocess)."""

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
