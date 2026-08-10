"""Commands the app types into a tab's shell on the user's behalf.

Closing a tab ends with an ``exit`` fed to the shell the agent CLI was
running in, and a panel shell gets a ``cd`` whenever the session's directory
moves. Neither can go to the pty bare, and this is the one place that says
why:

* **A shell inherits input the CLI never read.** A CLI holding the terminal
  in mouse-tracking mode has VTE writing a report into the pty every time the
  pointer crosses it, and one written in the moment between the CLI's last
  read and its exit stays queued for whoever reads next. The shell parks it
  on its input line, where a bare ``exit`` joins it into a single unknown
  command (``35;3;25Mexit``, from a real close). So the command is fed behind
  ``\\x15`` -- readline's kill-line -- which clears that line first.

* **A bare kill-line rings the bell.** Readline dings when kill-line is asked
  with the cursor at column 0, and column 0 is the ordinary case: the usual
  close finds an untouched prompt with nothing to kill. That BEL was audible
  (VTE rings the system bell of its own accord) and visible (it ran the
  window's bell-flash), so every stopped session announced itself on the way
  out for no reason. Giving the kill a space to eat keeps it a kill-line and
  keeps it quiet.
"""

from __future__ import annotations

# A space for the kill to land on, then \x15 (kill-line). The kill runs
# backwards from the cursor, so it takes the space along with anything the
# shell inherited ahead of it, and the shell reads only what follows.
LINE_RESET = " \x15"


def shell_command(text: str) -> str:
    """`text` behind a line reset, ready to feed to a shell's pty.

    The Enter belongs to the caller: a shell takes either, but which one a
    caller means is worth seeing at the call site.
    """
    return f"{LINE_RESET}{text}"
