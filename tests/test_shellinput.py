"""The line reset in front of an app-fed shell command must not ring the bell.

Readline dings when kill-line is asked with the cursor at column 0, which is
what an ordinary close finds, so this is checked against a real shell on a
real pty rather than by reading the constant back. Nothing here touches GTK.
"""

import os
import pty
import re
import select
import shutil
import time

import pytest

from collins.shellinput import shell_command

BASH = shutil.which("bash")
pytestmark = [
    pytest.mark.skipif(BASH is None, reason="needs bash to drive a real shell"),
    # forkpty warns that a multi-threaded process can deadlock between fork
    # and exec. The child below reaches execve without allocating -- its
    # environment is built here, in the parent -- so it has no window to
    # deadlock in.
    pytest.mark.filterwarnings("ignore:This process.*multi-threaded:DeprecationWarning"),
]

PROMPT = "READY$ "
BASH_ARGV = [BASH, "--norc", "--noprofile", "-i"]
SHELL_ENV = {**os.environ, "PS1": PROMPT, "TERM": "xterm-256color", "INPUTRC": "/dev/null"}

# An OSC string may be terminated by BEL, so those bytes are not dings. Shells
# emit them for window titles and VTE's shell integration.
_OSC = re.compile(rb"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)


def _dings(fed: str, timeout: float = 10.0) -> tuple[int, bool]:
    """Feed `fed` to an interactive bash on a pty. Returns how many bells it
    rang and whether it exited.

    Reading stops at EIO, which a pty master raises as soon as the shell is
    gone -- so the shell's answer is collected up to the echo of what it was
    told to run, and the count is only trusted once that echo is in hand.
    """
    pid, fd = pty.fork()
    if pid == 0:  # the child never returns
        os.execve(BASH, BASH_ARGV, SHELL_ENV)

    out = bytearray()
    exited = False

    def read_until(deadline, done) -> None:
        while time.monotonic() < deadline and not done():
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    out.extend(os.read(fd, 65536))
                except OSError:  # the shell exited and closed its end
                    return

    try:
        deadline = time.monotonic() + timeout
        read_until(deadline, lambda: PROMPT.encode() in out)
        assert PROMPT.encode() in out, f"no prompt from bash: {bytes(out)!r}"
        out.clear()

        os.write(fd, fed.encode())
        read_until(deadline, lambda: b"exit" in out)
        # Without the echo there is nothing to have heard a bell in, and a
        # silent capture would pass every assertion below for the wrong reason.
        assert b"exit" in out, f"bash never echoed the fed command: {bytes(out)!r}"

        while time.monotonic() < deadline and not exited:
            exited = os.waitpid(pid, os.WNOHANG)[0] == pid
            if not exited:
                time.sleep(0.05)
        return _OSC.sub(b"", bytes(out)).count(b"\x07"), exited
    finally:
        if not exited:  # including the assertions above walking out
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        os.close(fd)


def test_a_bare_kill_line_is_what_rings():
    """The control. If this shell stays quiet the regression cannot happen
    here, and the test below would pass without proving anything."""
    rang, _ = _dings("\x15exit\r")
    if rang == 0:
        pytest.skip("this bash does not ring on kill-line at column 0")


def test_an_app_fed_command_leaves_an_empty_prompt_silently():
    rang, exited = _dings(shell_command("exit\r"))
    assert rang == 0, "the fed exit rang the terminal bell"
    assert exited, "the shell ignored the fed exit"


def test_the_reset_still_clears_inherited_input():
    """The reason the reset exists at all (PR 256): input the CLI never read
    is sitting on the shell's line, and the fed command must not join it."""
    junk = "35;3;25M"  # the tail of a mouse report, as a real close saw it
    rang, exited = _dings(junk + shell_command("exit\r"))
    assert exited, "the fed exit did not survive inherited input"
    assert rang == 0, "clearing inherited input rang the terminal bell"
