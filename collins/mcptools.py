"""App-side plumbing for the session MCP tools, kept free of GTK.

Everything the socket service and command builders need that isn't a widget:
the tool table served to sessions, argument validation, the newline-delimited
JSON framing shared with the shim, and the per-instance runtime paths plus
the `--mcp-config` file they point at. The split mirrors `proctree.py` /
`chats.py`: no GTK imports, so CI (which has no typelibs) can test the whole
protocol. The tool *handlers* live with the widgets they drive, in `app.py`.

The shim (`mcp_shim.py`) deliberately imports nothing from this package —
wire constants it shares are mirrored there by hand.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import APP_ID, DEBUG_APP_ID

# One frame on the shim↔app socket never legitimately approaches this; a line
# that does is garbage or an attack, not a tool call. Mirrored in mcp_shim.py.
MAX_LINE = 1024 * 1024

# read_terminal's caps: how much of the panel shells' scrollback one call
# carries. The default is a generous screenful-and-then-some; the maximum is
# where terminal_reply's own frame-budget shrinking takes over anyway.
TERMINAL_DEFAULT_LINES = 200
TERMINAL_MAX_LINES = 2000

# The tools Collins serves to sessions, in MCP's own tool shape (the app
# hands these to the shim verbatim for `tools/list`). A tool earns its place
# only if it needs the app — anything the agent can do from its shell stays
# out. Every entry here re-opens the permissions question in the spec:
# capability creep is a security decision, not just a feature decision.
#
# Descriptions are agent-facing English, deliberately untranslated.
TOOLS: list[dict] = [
    {
        "name": "set_session_title",
        "description": (
            "Rename this session in Collins. Sets the user-visible title on "
            "the session's tab and sidebar entry — keep it short, like a good "
            "commit subject, and update it if the work pivots."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "The new session title.",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_in_editor",
        "description": (
            "Open a file in this session's Collins editor pane, optionally at "
            "a line — put a file on the user's screen instead of hoping they "
            "click a path in the terminal. The file must be inside the "
            "session's project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                    "description": (
                        "The file to open; a relative path resolves against "
                        "the working directory, then the project root."
                    ),
                },
                "line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "1-based line to place the cursor on.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "show_image",
        "description": (
            "Show the user an image inline in this session's Collins window — "
            "screenshots, plots, renders, or any picture they asked to see. "
            "This is the default way to put an image in front of the user: "
            "use it whenever they say 'show me', 'let me see', 'display', or "
            "ask for a picture, chart, or screenshot, and any time you'd say "
            "'look at this' without making them click. Prefer it over "
            "delivering the image as a file attachment — send a file only "
            "when the user needs the file itself to keep, download, or open "
            "in another app. Any readable image file works, inside the "
            "project or not, and so does an http(s) URL — Collins fetches "
            "it, so don't spend a download of your own first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                    "description": (
                        "The image to show: a file path, or an http(s) URL "
                        "Collins downloads. A relative path resolves against "
                        "the working directory, then the project root."
                    ),
                },
                "caption": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": (
                        "A short caption shown under the image — what the "
                        "user is looking at, or what to notice in it."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "notify_user",
        "description": (
            "Send the user a notification from this session, for when you "
            "need them back: a question you're blocked on, or the finish "
            "they asked to be told about. Collins titles it with the session "
            "and clicking it opens this session's tab, so the message only "
            "has to say the thing — 'Tests pass, ready to push?'. It shows "
            "as a card inside Collins when the user is there but in another "
            "session, as a desktop notification when they are away, and "
            "goes straight into their notification history when they are "
            "looking at this very session; the reply says which. It "
            "interrupts whatever they're doing, so don't narrate progress "
            "with it, and don't repeat one the user hasn't come back from."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": (
                        "What to tell the user; a sentence or two, since "
                        "notifications are truncated on most desktops."
                    ),
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "attach_pr",
        "description": (
            "Attach a GitHub pull request to this session in Collins, so it "
            "shows on the session's footer and sidebar row with live CI "
            "status. Collins links the PRs that appear in this session's own "
            "output automatically — reach for this when one it can't see "
            "belongs here: a PR opened by a subagent, opened outside this "
            "session, or one this session is reviewing rather than authoring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": (
                        "The pull request's full URL, e.g. "
                        "https://github.com/owner/repo/pull/123."
                    ),
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "start_session",
        "description": (
            "Start a NEW agent session in Collins, in the background, and hand "
            "it a prompt to begin on — a sibling of yours working in parallel "
            "while you keep going. It opens in a tab that never takes the "
            "user's tab selection, keyboard focus, or view: they find it as a "
            "new session row in the sidebar, the same as any session, and it "
            "rings and flashes if it needs them. Returns the new session's id "
            "(and the directory it started in) once the prompt is submitted. "
            "Reach for it to spread a self-contained piece of work to a fresh "
            "agent — an independent change, a long job to babysit, an "
            "exploration to run alongside your own — rather than doing it "
            "inline. The spawned session gets these same Collins tools, so it "
            "can spawn its own; a prompt is required because a session with "
            "nothing to do is a leaked process."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    # The socket frame caps at MAX_LINE (1 MiB); this leaves a
                    # wide margin, and a prompt this long is a design smell
                    # regardless.
                    "maxLength": 50_000,
                    "description": (
                        "The prompt the new session starts on — submitted to "
                        "it the moment its input box is ready, as if typed and "
                        "sent. May be multiple lines; make it self-contained, "
                        "since no one is watching the box to add to it."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                    "description": (
                        "The directory to start the session in. Defaults to "
                        "your own current working directory. Must be an "
                        "existing directory; it need not be a project Collins "
                        "already lists — a new one appears in the sidebar for "
                        "it."
                    ),
                },
                "worktree": {
                    "type": "boolean",
                    "description": (
                        "Force starting in a fresh git worktree on or off. "
                        "Omit to use the project's usual setting. Ignored "
                        "outside a git checkout (the result says so)."
                    ),
                },
                "permission_mode": {
                    "type": "string",
                    # The provider's own permission_modes() values, minus
                    # bypassPermissions — handing a sibling that would be
                    # privilege the user never saw, so it is refused in the
                    # handler (the only human gate on this call is your MCP
                    # permission prompt). The enum tracks the sole MCP-serving
                    # provider's modes; the handler re-checks against the live
                    # provider list.
                    "enum": ["plan", "acceptEdits", "bypassPermissions"],
                    "description": (
                        "Permission mode for the new session: 'plan' "
                        "(read-only) or 'acceptEdits'. Omit to inherit your "
                        "own session's current mode. 'bypassPermissions' is "
                        "refused (and never inherited)."
                    ),
                },
                "model": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "description": (
                        "Model for the new session — anything the CLI's "
                        "--model takes: an alias ('opus', 'sonnet', 'haiku') "
                        "or a full model id. Omit to run it on the same model "
                        "your own session is on right now."
                    ),
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        # The one tool that reads anything back to the agent — including
        # whatever the user typed into their own shells, echoed passwords
        # and all. The user's gates are the CLI's per-session permission
        # prompt and the Preferences switch, same as every tool here; what
        # keeps this one honest is that it only ever reads, and only the
        # calling session's own panel.
        "name": "read_terminal",
        "description": (
            "Read the terminal-panel tabs open alongside this session in "
            "Collins — the plain shells the user runs next to you (the "
            "Ctrl+J panel, tabs titled 'Terminal 1', 'Terminal 2', …). "
            "Returns each one's text, scrollback included: the commands "
            "typed into it, their output, and whatever a still-running "
            "command has printed so far. Reach for it when the user refers "
            "to something in their own terminal — 'the error over there', a "
            "server they left running, a command they tried — instead of "
            "asking them to paste it. Reading is all this does: it cannot "
            "type into a shell, and it says so when no panel terminal is "
            "open."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "terminal": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Which terminal to read, by the number in its tab "
                        "title ('Terminal 2' → 2). Omit to read all of them."
                    ),
                },
                "lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": TERMINAL_MAX_LINES,
                    "description": (
                        "Trailing lines to return per terminal — the end of "
                        "the scrollback, where the latest output is. "
                        f"Defaults to {TERMINAL_DEFAULT_LINES}."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        # read_terminal's writing half, and the more consequential one: it
        # types into a shell the user owns. Its guardrails are visibility
        # and consent — the command runs where the user can watch it (the
        # target is revealed, never a hidden strip), a terminal with a
        # command already running is refused rather than typed into, and
        # the CLI's permission prompt plus the Preferences switch gate it
        # like every tool here.
        "name": "run_in_terminal",
        "description": (
            "Type a command into one of this session's terminal-panel tabs "
            "in Collins and run it — visibly, in the user's own split "
            "terminal (the Ctrl+J panel), where they can watch it, interact "
            "with it, and keep the shell afterwards. The terminal is opened "
            "if none exists, and brought on screen if hidden. Reach for it "
            "when the user should own or watch what runs — a dev server, a "
            "REPL, a watch task, anything they asked to have running in "
            "their terminal — not for your own work: ordinary commands "
            "belong in your normal shell tool. Returns as soon as the "
            "command is typed, without waiting for output — follow up with "
            "read_terminal to see how it's going. A terminal busy running "
            "a command is never typed into."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 10_000,
                    "description": (
                        "The command to run, typed into the shell with a "
                        "trailing Enter. Multiple lines run in order, one "
                        "Enter per line."
                    ),
                },
                "terminal": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Which terminal to type into, by the number in its "
                        "tab title ('Terminal 2' → 2); it must be idle. "
                        "Omit to use the first idle terminal, opening a new "
                        "tab when there is none (or all are busy)."
                    ),
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


def tool_schema(name: str) -> dict | None:
    """The TOOLS entry named *name*, or None."""
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


def tool_setting_key(name: str) -> str:
    """The settings key holding the on/off switch for the tool *name*.

    One key per tool rather than a single list: state.DEFAULT_SETTINGS is
    built from TOOLS through this function, so a tool added to the table
    arrives with its own setting, defaulting on, and a tool removed from it
    leaves a dead key behind that nothing reads.
    """
    return f"mcp_tool_{name}"


def default_tool_settings() -> dict[str, bool]:
    """Every tool's switch, all on — state.DEFAULT_SETTINGS folds this in."""
    return {tool_setting_key(tool["name"]): True for tool in TOOLS}


def enabled_tools(is_enabled: Callable[[str], bool]) -> list[dict]:
    """The TOOLS entries *is_enabled* says a session may see.

    What `tools/list` answers with, so a tool switched off is one the agent
    is never told about rather than one it is told about and refused. A
    session already running keeps the list it was handed at startup, which is
    why `run_tool_call` gates calls too.
    """
    return [tool for tool in TOOLS if is_enabled(tool["name"])]


def disabled_error(name: str) -> str:
    """The agent-facing refusal for a tool the user has switched off."""
    return f"{name} is turned off in Collins (Preferences → Session tools)"


def validate_args(name: str, args: object) -> str | None:
    """An error message when *args* doesn't satisfy *name*'s schema, else None.

    The app re-validates every call: the socket is reachable by any local
    process, so the CLI's own schema enforcement is not a boundary. Only the
    slice of JSON Schema the TOOLS table actually uses is understood here —
    extend this alongside the table, never past it.
    """
    tool = tool_schema(name)
    if tool is None:
        return f"Unknown tool: {name}"
    if not isinstance(args, dict):
        return "Arguments must be a JSON object"
    schema = tool["inputSchema"]
    properties = schema.get("properties", {})
    for key in schema.get("required", ()):
        if key not in args:
            return f"Missing required argument: {key}"
    for key, value in args.items():
        spec = properties.get(key)
        if spec is None:
            return f"Unexpected argument: {key}"
        error = _validate_value(key, value, spec)
        if error is not None:
            return error
    return None


def _validate_value(key: str, value, spec: dict) -> str | None:
    kind = spec.get("type")
    if kind == "string":
        if not isinstance(value, str):
            return f"'{key}' must be a string"
        if len(value) < spec.get("minLength", 0):
            if spec.get("minLength") == 1:
                return f"'{key}' must not be empty"
            return f"'{key}' must be at least {spec['minLength']} characters"
        if len(value) > spec.get("maxLength", float("inf")):
            return f"'{key}' must be at most {spec['maxLength']} characters"
        # An enum-shaped string (start_session's permission_mode) constrains the
        # value to a fixed set. A handler may narrow it further — the enum is
        # the shape, not the whole allowlist.
        choices = spec.get("enum")
        if choices is not None and value not in choices:
            return f"'{key}' must be one of: {', '.join(choices)}"
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{key}' must be an integer"
        if value < spec.get("minimum", value):
            return f"'{key}' must be at least {spec['minimum']}"
        if value > spec.get("maximum", value):
            return f"'{key}' must be at most {spec['maximum']}"
    elif kind == "boolean":
        # bool is the one JSON type that is also an int in Python, so it is
        # checked before nothing else can mistake it — and integer above
        # rejects a bool for the same reason.
        if not isinstance(value, bool):
            return f"'{key}' must be true or false"
    return None


# What a permission mode read off a transcript may look like before it is
# trusted onto a command line: the CLI's own mode names, and nothing else.
_MODE_TOKEN_RE = re.compile(r"[A-Za-z]{1,32}")


def inherited_permission_mode(mode: str | None) -> str:
    """The permission mode a start_session spawn inherits when its caller
    didn't pick one: the calling session's own current mode, as its
    transcript recorded it.

    Passed through verbatim — the CLI wrote it, the CLI will accept it —
    with two exceptions. Anything that doesn't look like a mode token is
    dropped to "" (the CLI's default) rather than spliced into a command
    line: the transcript is app data, but a file on disk all the same.
    And bypassPermissions caps to acceptEdits: a bypass-mode caller has no
    permission prompt gating this call at all, so inheriting bypass would
    let one unattended session mint others no human ever approved —
    acceptEdits is the strongest mode the tool grants explicitly, so it is
    the strongest one inheritance grants too.
    """
    if not mode or not _MODE_TOKEN_RE.fullmatch(mode):
        return ""
    if mode == "bypassPermissions":
        return "acceptEdits"
    return mode


# What a model may look like before it is trusted onto a command line: the
# CLI's aliases and ids (claude-opus-4-1-20250805, us.anthropic.…:0), and
# nothing with a shell's punctuation in it. Shared by the explicit argument
# and the inherited transcript value — shlex.quote would keep either safe,
# but a model name that needs quoting is no model name.
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")


def valid_model(model: str | None) -> bool:
    """Whether *model* is shaped like something --model would accept."""
    return bool(model) and _MODEL_TOKEN_RE.fullmatch(model) is not None


def inherited_model(model: str | None) -> str:
    """The model a start_session spawn inherits when its caller didn't pick
    one: the calling session's own current model, as its transcript recorded
    it on the last reply — so a mid-run ``/model`` carries over too.

    Passed through verbatim — the CLI wrote it, the CLI will accept it —
    unless it doesn't look like a model token, which drops to "" (no
    --model; the CLI's configured default) rather than being spliced into
    a command line: the transcript is app data, but a file on disk all the
    same.
    """
    return model if valid_model(model) else ""


# The room a read_terminal reply's text leaves inside one MAX_LINE frame for
# the JSON-RPC envelope around it.
_TERMINAL_REPLY_MARGIN = 16 * 1024


def _terminal_tail(text: str, lines: int) -> str:
    """The last *lines* lines of one terminal's dump.

    The dump ends in the screen's unused rows, so trailing whitespace goes
    first — the tail should spend itself on output, not blanks.
    """
    return "\n".join(text.rstrip().split("\n")[-lines:])


def _terminal_section(number: int, busy: bool, text: str) -> str:
    state = "command running" if busy else "idle"
    return f"── Terminal {number} ({state}) ──\n{text or '(empty)'}"


def terminal_reply(sections: list[tuple[int, bool, str]], lines: int) -> str:
    """The read_terminal reply: each of *sections* — (tab-title number,
    has-a-running-command, full scrollback dump) — tailed to *lines* lines
    under a header naming the terminal.

    Guaranteed to fit one wire frame: an oversize reply doesn't degrade, it
    closes the shim's connection (see mcpserver._send), so after the line
    tail the reply is measured as it will be encoded — JSON escaping can
    multiply a dump full of control bytes — and every tail keeps halving,
    oldest half dropped first, until the frame takes it. Should the halving
    run dry — every tail already empty, the headers alone over budget, which
    takes tens of thousands of tabs — the reply is cut off outright rather
    than looped on: a section count that absurd is its own answer, and the
    guarantee this function exists for is "returns, and fits".
    """
    tails = [(number, busy, _terminal_tail(text, lines)) for number, busy, text in sections]
    budget = MAX_LINE - _TERMINAL_REPLY_MARGIN
    while True:
        reply = "\n\n".join(_terminal_section(*tail) for tail in tails)
        if len(json.dumps(reply).encode("utf-8")) <= budget:
            return reply
        if not any(text for _number, _busy, text in tails):
            # Headers only by now, so every character is ASCII or "─" and
            # encodes in at most 6 bytes (─) — a sixth of the budget
            # in characters always fits it.
            return reply[: budget // 6]
        tails = [
            (number, busy, text[(len(text) + 1) // 2 :]) for number, busy, text in tails
        ]


class DeferredResult:
    """A tool call whose answer isn't ready yet.

    Handlers return one of these instead of `(ok, text)` when finishing the
    call means work that must not run on the main loop — today only
    `show_image` fetching a URL. The service holds that connection's reply
    until `resolve` is called (from the main loop, once the worker is done),
    which keeps the read → reply → read invariant intact: the session waits
    on its own call and nobody else's is delayed.

    `resolve` may be called at most once meaningfully — the first answer
    wins, so a late arrival can't overwrite a timeout's, and a worker that
    resolves twice is a bug that stays quiet on the wire.
    """

    def __init__(self) -> None:
        self._result: tuple[bool, str] | None = None
        self._watcher: Callable[[bool, str], None] | None = None

    @property
    def resolved(self) -> bool:
        return self._result is not None

    def resolve(self, ok: bool, text: str) -> None:
        if self._result is not None:
            return
        self._result = (ok, text)
        if self._watcher is not None:
            self._watcher(ok, text)

    def watch(self, callback: Callable[[bool, str], None]) -> None:
        """Call *callback* with the result — now if it's already in, else as
        soon as it lands. One watcher: the connection waiting on the reply."""
        self._watcher = callback
        if self._result is not None:
            callback(*self._result)


# What a handler (and so `run_tool_call`) answers with: the result, or the
# promise of one.
ToolResult = tuple[bool, str] | DeferredResult

# The identity error every tool shares: the caller's /proc ancestry reached
# no open tab (a daemon-hosted bg job, a chat session, a tab since closed).
NOT_FROM_TAB_ERROR = "This claude process wasn't launched from a Collins tab"


def run_tool_call(
    tool: str,
    args: object,
    find_tab,
    handlers,
    is_enabled: Callable[[str], bool] | None = None,
) -> ToolResult:
    """One tool call's skeleton: validate, check the switch, resolve identity,
    run the handler.

    The branching lives here, GTK-free, so CI can pin its order and error
    strings; app.py supplies the halves that need widgets or settings —
    `find_tab()` (the pid→tab ancestry walk, returning None for a caller no
    tab owns), `handlers` (tool name → callable taking (found_tab, args) and
    returning (ok, message), or a `DeferredResult` when the answer needs a
    worker thread), and `is_enabled` (the Preferences switch;
    omitted means every tool is on). Validation runs first and
    unconditionally: the socket is reachable by any local process, so the
    CLI's own schema enforcement is not a boundary. The switch comes next —
    it is a property of the tool, not of the caller, and a session that was
    handed the tool before it was switched off is refused here rather than
    acted on. Identity comes last, so a bad call fails the same way whoever
    makes it, leaking nothing about what tabs exist.
    """
    error = validate_args(tool, args)
    if error is not None:
        return False, error
    if is_enabled is not None and not is_enabled(tool):
        return False, disabled_error(tool)
    found = find_tab()
    if found is None:
        return False, NOT_FROM_TAB_ERROR
    handler = handlers.get(tool)
    if handler is None:  # a TOOLS entry whose handler hasn't landed
        return False, f"Unknown tool: {tool}"
    return handler(found, args)


def encode_message(message: dict) -> bytes:
    """One wire frame: compact JSON plus the terminating newline.

    Raises ValueError when the encoding exceeds MAX_LINE — the sender's bug
    to surface, never to put on the wire for the peer to choke on.
    """
    data = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > MAX_LINE:
        raise ValueError("message exceeds the frame limit")
    return data


def decode_message(line: bytes | str) -> dict:
    """The dict encoded in one wire frame.

    Raises ValueError for an over-long line, malformed JSON, or a JSON value
    that isn't an object — the caller treats any of those as a broken peer.
    """
    if isinstance(line, str):
        line = line.encode("utf-8")
    if len(line) > MAX_LINE:
        raise ValueError("message exceeds the frame limit")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    return message


def runtime_dir(app_id: str) -> str:
    """The per-instance directory holding the socket (and, for generated app
    ids, the config — see `config_dir`).

    Keyed by app id, not per-run: the real app reuses a stable path across
    restarts (so a long-lived session's shim can reconnect after Collins
    comes back), while capture harnesses — which generate fresh app ids —
    stay fully isolated from it and from each other. The socket lives here
    unconditionally: XDG_RUNTIME_DIR is guaranteed user-private, and its
    paths stay short of the kernel's ~107-byte unix-socket limit (see
    mcpserver.start), which paths under the home directory may not.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, "collins", app_id)


# The app ids a user's real instances run under: the installed app and the
# start-debug checkout. Everything else — the screenshot harness mints a
# fresh com.episode6.Collins.E2E.<run> id per capture — is a throwaway
# whose files should die with the boot, so this is an allowlist (a prefix
# match would claim the E2E ids too).
STABLE_APP_IDS = frozenset({APP_ID, DEBUG_APP_ID})


def config_dir(app_id: str) -> str:
    """Where this instance's mcp.json lives.

    A stable id gets a persistent directory under the user's data dir: the
    CLI daemon records the `--mcp-config` path verbatim in a background
    job's respawn flags (~/.claude/jobs/<id>/state.json), and a path on
    tmpfs dangles after a reboot until Collins happens to run again — a job
    respawned in that window would launch pointing at a file that doesn't
    exist. Nothing in the file is runtime-scoped (it names the interpreter,
    the shim module, and the socket path, all stable), and `write_config`
    rewrites it on every launch anyway. Generated ids keep the runtime dir:
    their sessions are as disposable as the id, and persistent directories
    for them would only accumulate.
    """
    if app_id in STABLE_APP_IDS:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(base, "collins", app_id)
    return runtime_dir(app_id)


def socket_path(app_id: str) -> str:
    return os.path.join(runtime_dir(app_id), "mcp.sock")


def config_path(app_id: str) -> str:
    return os.path.join(config_dir(app_id), "mcp.json")


def _stdio_servers(app_id: str) -> dict:
    """The stdio MCP servers Collins configures, as mcp.json's mcpServers
    value. One definition shared by `write_config` and
    `infrastructure_cmdlines`, so what the CLI is told to spawn and what the
    busy-tracking poll knows to ignore can never drift apart."""
    return {
        "collins": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "collins.mcp_shim"],
            "env": {
                "COLLINS_MCP_SOCKET": socket_path(app_id),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            },
        },
    }


def infrastructure_cmdlines() -> frozenset[str]:
    """The cmdlines (as /proc renders them: argv space-joined) of the server
    processes the CLI spawns because Collins asked it to.

    These are the CLI's plumbing, alive for a session's whole life, and must
    never read as "the agent left something running" — the busy poll unions
    them into every baseline it applies (see MainWindow's process poll), which
    also covers sessions from before their baseline was ever captured. The
    env block plays no part: it doesn't appear in a cmdline.
    """
    return frozenset(
        " ".join([server["command"], *server["args"]])
        for server in _stdio_servers("").values()
        if server.get("type") == "stdio"
    )


def write_config(app_id: str) -> str | None:
    """Write the `--mcp-config` file and return its path, or None on failure.

    Best-effort like trust.py's config write: a failure just means launched
    commands go out without the flag, exactly as they did before the feature.
    The command is derived from the running interpreter and package location
    so it resolves for every install shape — system package, editable
    checkout, debug instance — without guessing at PATH.
    """
    config = {"mcpServers": _stdio_servers(app_id)}
    path = Path(config_path(app_id))
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        tmp.replace(path)
        return str(path)
    except OSError:
        return None
