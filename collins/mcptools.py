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
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

# One frame on the shim↔app socket never legitimately approaches this; a line
# that does is garbage or an attack, not a tool call. Mirrored in mcp_shim.py.
MAX_LINE = 1024 * 1024

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
            "Show an image over this session's Collins window — screenshots, "
            "plots, renders. Use it to say 'look at this' without waiting for "
            "a click or launching an external viewer. Any readable image file "
            "works, inside the project or not."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                    "description": (
                        "The image file to show; a relative path resolves "
                        "against the working directory, then the project root."
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
            "Send the user a desktop notification from this session, for when "
            "you need them back: a question you're blocked on, or the finish "
            "they asked to be told about. Collins titles it with the session "
            "and clicking it opens this session's tab, so the message only "
            "has to say the thing — 'Tests pass, ready to push?'. It "
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
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{key}' must be an integer"
        if value < spec.get("minimum", value):
            return f"'{key}' must be at least {spec['minimum']}"
    return None


# The identity error every tool shares: the caller's /proc ancestry reached
# no open tab (a daemon-hosted bg job, a chat session, a tab since closed).
NOT_FROM_TAB_ERROR = "This claude process wasn't launched from a Collins tab"


def run_tool_call(
    tool: str,
    args: object,
    find_tab,
    handlers,
    is_enabled: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """One tool call's skeleton: validate, check the switch, resolve identity,
    run the handler.

    The branching lives here, GTK-free, so CI can pin its order and error
    strings; app.py supplies the halves that need widgets or settings —
    `find_tab()` (the pid→tab ancestry walk, returning None for a caller no
    tab owns), `handlers` (tool name → callable taking (found_tab, args) and
    returning (ok, message)), and `is_enabled` (the Preferences switch;
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
STABLE_APP_IDS = frozenset({"com.episode6.Collins", "com.episode6.Collins.Debug"})


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
