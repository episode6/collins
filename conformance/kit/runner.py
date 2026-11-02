# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""What one run of the kit knows: the socket, the options, the server's
hello, the reporter, and the clients it makes."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

from . import client as apiclient
from . import proto
from .reporter import CheckFailed, CheckSkipped, Reporter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # conformance/
DATA = os.path.join(HERE, "data")

# The terminal the kit says it is: VTE 0.84.0's version number, white on
# black, dark. The colour queries are answered from it, so the expected
# replies are known.
TERM = {
    "vte": 8400,
    "fg": "#ffffff",
    "bg": "#000000",
    "scheme": "dark",
}

COLS, ROWS = 120, 40  # the grid the published scenarios were measured at


@dataclass
class Options:
    sock: str
    level: int = 2
    families: set[str] | None = None  # None: every claimed family
    skip_families: set[str] = field(default_factory=set)
    fake_claude: str = os.path.join(HERE, "fake_claude.py")
    scratch: str = ""
    replay: bool = False
    with_gh: bool = False
    verbose: bool = False
    json_out: str = ""
    only: list[str] = field(default_factory=list)
    keep_scratch: bool = False
    vte_bytes: bool = False  # a query answered in another terminal's voice fails, not notes
    spawn_budget: float = 5.0  # seconds from the spawn to the fake's box taking a prompt


class Run:
    """One run against one server."""

    def __init__(self, options: Options, reporter: Reporter) -> None:
        self.options = options
        self.reporter = reporter
        self.p = proto.load()
        self.hello: apiclient.Reply | None = None
        self.caps: set[str] = set()
        self.implementation = ""
        self.settings: dict = {}  # the server's `settings`, as the first subscribe's snapshot said
        self.spoofs: bool | None = None  # whether an agent pty had the two spoofs (None: not read)
        self.known_session: str | None = None  # a session id the sessions checks bound, for Level 2
        self.clients: list[apiclient.Client] = []
        self.all_clients: list[apiclient.Client] = []  # every client ever made, for the wire check
        self._made_scratch = False
        if not options.scratch:
            options.scratch = tempfile.mkdtemp(prefix="collins-conformance-")
            self._made_scratch = True
        os.makedirs(options.scratch, exist_ok=True)
        # The real path: a server and the CLI name a session's directory by
        # the path they resolve, and a symlinked temporary directory (macOS's
        # default) would bind to another name than the one the kit asked for.
        options.scratch = os.path.realpath(options.scratch)
        # The session's own directory, so its transcript directory is the kit's
        # (`~/.claude/projects/<encoded cwd>`) and nothing of the person's.
        self.cwd = os.path.join(options.scratch, "project")
        os.makedirs(self.cwd, exist_ok=True)

    # -- clients --------------------------------------------------------------

    def client(self, device: str = "conformance", term: dict | None = TERM, **kw) -> apiclient.Client:
        """A connected client that said hello on its primary."""
        c = apiclient.Client(self.options.sock, device=device, term=term, **kw)
        reply = c.connect()
        if not reply.ok:
            raise CheckFailed(f"hello refused: {reply.describe()}")
        self.clients.append(c)
        self.all_clients.append(c)
        return c

    def close_all(self) -> None:
        for c in self.clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        self.clients.clear()

    def claims(self, family: str) -> bool:
        return family in self.caps

    def wants_family(self, family: str) -> bool:
        """Whether this run checks *family*: claimed, asked for, not skipped."""
        if not self.claims(family):
            return False
        if family in self.options.skip_families:
            return False
        if self.options.families is not None and family not in self.options.families:
            return False
        return True

    def need(self, family: str) -> None:
        if not self.claims(family):
            raise CheckSkipped(f"the server does not list `{family}`")
        if not self.wants_family(family):
            raise CheckSkipped(f"`{family}` left out by the options")

    def need_file(self, *parts: str) -> str:
        path = os.path.join(DATA, *parts)
        if not os.path.exists(path):
            raise CheckSkipped(f"missing kit data: {os.path.relpath(path, HERE)}")
        return path

    def cleanup(self) -> None:
        self.close_all()
        if self._made_scratch and not self.options.keep_scratch:
            import shutil

            shutil.rmtree(self.options.scratch, ignore_errors=True)
