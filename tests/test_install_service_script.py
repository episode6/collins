# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""scripts/install-service.sh signals what its patterns match, so they are
pinned against what really runs and against what only mentions the name.
`grep -E` reads them as `pgrep -f` does (POSIX extended)."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-service.sh"


def pattern(name: str) -> str:
    match = re.search(rf"^{name}='(.+)'$", SCRIPT.read_text(), re.MULTILINE)
    assert match, f"{name} not found in {SCRIPT}"
    return match.group(1)


def matches(name: str, command_line: str) -> bool:
    result = subprocess.run(
        ["grep", "-Eq", "--", pattern(name)],
        input=command_line.encode(),
        check=False,
    )
    assert result.returncode in (0, 1)
    return result.returncode == 0


@pytest.mark.parametrize(
    "command_line",
    [
        "/usr/bin/python3 /usr/bin/collins-service",
        "/home/me/.local/share/pipx/venvs/collins/bin/python /home/me/.local/bin/collins-service --app-id X",
        "/usr/bin/python3 -m collins.service.main --app-id com.episode6.Collins.Debug",
        "python3.12 -I -m collins.service.main",
        "/usr/bin/python3 /usr/bin/collins-service --print-socket",
        "collins-service --app-id X",
        "/usr/local/bin/collins-service",
    ],
)
def test_the_service_is_matched(command_line):
    assert matches("SERVICE_RE", command_line)


@pytest.mark.parametrize(
    "command_line",
    [
        # What holds the sessions: stopping it ends them for good.
        "/usr/bin/python3 -m collins.service.keeper --app-id X --socket /run/user/1000/keeper.sock",
        "/usr/bin/python3 /usr/bin/collins-keeper",
        "collins-keeper --app-id X",
        # The window.
        "/usr/bin/python3 /usr/bin/collins",
        "/usr/bin/python3 -m collins",
        # Mentions of the name, none of them the service.
        "journalctl --user -u collins-service",
        "systemctl --user status collins-service.service",
        "tail -f /tmp/collins-service.log",
        "vim collins-service",
        "bash ./scripts/install-service.sh",
        "bash -c python3 -m collins.service.main",
        "sh -c 'exec collins-service --print-socket'",
        "ssh box collins-service --print-socket",
        "claude -p restart python3 -m collins.service.main",
        "/usr/bin/python3 scripts/check_service.py /usr/bin/collins-service",
        "/usr/bin/python3 /tmp/collins-service.py",
        "/usr/bin/python3 -m collins.service.main_helper",
        "/usr/bin/python3 -m pytest tests/test_service_imports.py",
    ],
)
def test_nothing_else_is_matched(command_line):
    assert not matches("SERVICE_RE", command_line)


@pytest.mark.parametrize(
    "command_line",
    [
        "/usr/bin/python3 -m collins.service.keeper --app-id X",
        "/usr/bin/python3 /usr/bin/collins-keeper",
        "collins-keeper",
    ],
)
def test_the_keeper_note_finds_the_keeper(command_line):
    assert matches("KEEPER_RE", command_line)
    assert not matches("KEEPER_RE", "journalctl --user -u collins-keeper")


def test_the_script_parses_and_is_executable():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert os.stat(SCRIPT).st_mode & stat.S_IXUSR
