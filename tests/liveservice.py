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


def _die_with_parent() -> None:
    """PR_SET_PDEATHSIG SIGTERM, in the child before its exec."""
    import ctypes

    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)
    except OSError:
        pass


class LiveService:
    def __init__(
        self, tmp_path: Path, app_id: str = "com.example.Live", debug: bool = True, keeper: bool = False
    ) -> None:
        """*keeper* lets the service find or start a keeper (PR-3.6), which
        `stop` then ends too; off, the service forks its ptys itself and a
        stop ends every session (`COLLINS_KEEPER=none`)."""
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
            # The service judges upload directories by the sessions it knows:
            # staged sessions, so staged uploads (`uploads.sweep_refusal`).
            "COLLINS_UPLOADS_DIR": str(tmp_path / "uploads"),
            "COLLINS_PTY_STATE_DIR": str(tmp_path / "pty"),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_RUNTIME_DIR": self.runtime,
            "PYTHONPATH": str(REPO),
            "SHELL": "/bin/cat",
        }
        self.env.pop("COLLINS_E2E_STUBS", None)
        if keeper:
            self.env.pop("COLLINS_KEEPER", None)
        else:
            self.env["COLLINS_KEEPER"] = "none"
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
        self.keeper_proc: subprocess.Popen | None = None

    @property
    def socket(self) -> str:
        from collins.api import server as api_server

        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        return api_server.socket_path(self.app_id)

    def start(self, wait_s: float = 20.0) -> subprocess.Popen:
        from collins.api import server as api_server

        if self.env.get("COLLINS_KEEPER") != "none" and self.keeper_pid() is None:
            # The keeper as the test's own child, dying with it (S7 of the
            # review of PR 629): a pytest run killed in CI leaves none.
            self.keeper_proc = subprocess.Popen(
                [sys.executable, "-m", "collins.service.keeper", "--app-id", self.app_id, "--socket",
                 api_server.keeper_socket_path(self.app_id)],
                env=self.env,
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_die_with_parent,
            )  # fmt: skip
            deadline = time.monotonic() + wait_s
            while self.keeper_pid() is None:
                if time.monotonic() >= deadline or self.keeper_proc.poll() is not None:
                    raise RuntimeError("collins-keeper did not come up")
                time.sleep(0.05)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "collins.service.main", "--app-id", self.app_id],
            env=self.env,
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        path = self.socket
        proof = api_server.proof_path(self.app_id)
        deadline = time.monotonic() + wait_s
        # The socket answers a beat before the proof file is renamed into
        # place (`listen` writes it after the socket is up): wait for both,
        # so a test may read the proof right after `start()` returns.
        while api_server.probe_socket(path) != "live" or not os.path.exists(proof):
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

    def keeper_pid(self) -> int | None:
        """The keeper's pid, from the pid file it wrote beside its socket."""
        from collins.api import server as api_server
        from collins.service import keeper

        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        return keeper.read_pid(api_server.keeper_socket_path(self.app_id))

    def stop_keeper(self, wait_s: float = 10.0) -> None:
        """SIGTERM the keeper (it ends its children) and wait for it; a
        no-op with none."""
        pid = self.keeper_pid()
        if pid is None and self.keeper_proc is not None and self.keeper_proc.poll() is None:
            pid = self.keeper_proc.pid
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and os.path.exists(f"/proc/{pid}"):
            time.sleep(0.05)
        if os.path.exists(f"/proc/{pid}"):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        """Everything down: the service, then the keeper."""
        self.stop()
        self.stop_keeper()

    def state(self) -> dict:
        import json

        try:
            return json.loads((self.config / "collins" / "state.json").read_text())
        except (OSError, ValueError):
            return {}
