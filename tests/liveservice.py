# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""A real `collins-service` subprocess on scratch directories, for the
tests that drive `api.client.SocketLink` end to end (test_api_client,
test_service_main). Test code only."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class LiveService:
    def __init__(self, tmp_path: Path, app_id: str = "com.example.Live", debug: bool = True) -> None:
        self.app_id = app_id
        # A short runtime directory: the socket path is bounded at 107 bytes.
        self.runtime = tempfile.mkdtemp(prefix="cr-")
        self.config = tmp_path / "config"
        self.env = {
            **os.environ,
            "COLLINS_APP_ID": app_id,
            "COLLINS_PROJECTS_DIR": str(tmp_path / "projects"),
            "COLLINS_CLAUDE_CONFIG": str(tmp_path / "claude.json"),
            "COLLINS_CHATS_DIR": str(tmp_path / "chats"),
            "COLLINS_PTY_STATE_DIR": str(tmp_path / "pty"),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_RUNTIME_DIR": self.runtime,
            "PYTHONPATH": str(REPO),
            "SHELL": "/bin/cat",
        }
        self.env.pop("COLLINS_E2E_STUBS", None)
        if debug:
            self.env["COLLINS_DEBUG_API"] = "1"
        else:
            self.env.pop("COLLINS_DEBUG_API", None)
        for name in ("projects", "chats"):
            (tmp_path / name).mkdir(exist_ok=True)
        (tmp_path / "claude.json").write_text("{}")
        (self.config / "collins").mkdir(parents=True, exist_ok=True)
        (self.config / "collins" / "state.json").write_text(
            '{"settings": {"welcome_seen": true, "title_model": "none"}}'
        )
        self.proc: subprocess.Popen | None = None

    @property
    def socket(self) -> str:
        from collins.api import server as api_server

        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        return api_server.socket_path(self.app_id)

    def start(self, wait_s: float = 20.0) -> subprocess.Popen:
        from collins.api import server as api_server

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "collins.service.main", "--app-id", self.app_id],
            env=self.env,
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        path = self.socket
        deadline = time.monotonic() + wait_s
        while api_server.probe_socket(path) != "live":
            if self.proc.poll() is not None:
                raise RuntimeError(f"the service exited {self.proc.returncode} before listening")
            if time.monotonic() >= deadline:
                self.stop()
                raise RuntimeError("the service did not listen in time")
            time.sleep(0.05)
        return self.proc

    def stop(self, sig: int = signal.SIGTERM, wait_s: float = 30.0) -> int | None:
        if self.proc is None or self.proc.poll() is not None:
            return None if self.proc is None else self.proc.returncode
        self.proc.send_signal(sig)
        try:
            return self.proc.wait(wait_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(5)

    def state(self) -> dict:
        import json

        try:
            return json.loads((self.config / "collins" / "state.json").read_text())
        except (OSError, ValueError):
            return {}
