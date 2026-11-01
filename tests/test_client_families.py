# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The window gates on families (server-side spec D5).

The window hides a surface whose family the service does not list, and never
sends that family's requests. `apilink.has(cap)` is the ask, and it comes
**before** the send, as `apilink.is_local()` does for a local extra
(`tests/pathless.py`'s shapes, read by `pathless.behind_an_ask`).

So this walks every client module with `ast` and finds each request of a
family: a dict literal whose `"t"` names a type the table gives a family
other than `core`; the two `core` types a family owns one form of (`spawn`'s
shell kind, `close`'s background mode); and an `http_get` or `http_put`, which
is where a blob or an upload leaves. Each must sit behind `apilink.has` of
that family, in the function it is written in. `ASKED_ELSEWHERE` is the list
of the sites that do not, with why: a reviewer sees a new one in the diff.

What this does not read: a surface being hidden (a walker cannot tell a
button from a label; `scripts/check_server_families.py` holds a running
window to it), and a request whose type is not a literal in the message
(there is none today; `test_every_literal_found_is_a_request_the_table_knows`
would not see one either, so a helper that builds one is held by hand).

The second half is the behaviour of the transports: with the family gone,
every one of them answers what a refusal would, and none reaches the link.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pathless
import pytest

import collins
from collins import apilink
from collins.api import protocol

ROOT = Path(collins.__file__).resolve().parent


def client_modules() -> dict[str, ast.Module]:
    """Every module of `collins/` but the service's and the service's end of
    the wire: `collins/service/` and `api/server.py` are not clients, and
    `api/protocol.py` is the table. `api/client.py` is."""
    modules = {path.stem: ast.parse(path.read_text()) for path in sorted(ROOT.glob("*.py"))}
    modules["api.client"] = ast.parse((ROOT / "api" / "client.py").read_text())
    return modules


# A core type one form of which is a family's: (type, field, value) -> family.
FAMILY_FORMS = {
    ("spawn", "kind", "shell"): protocol.CAP_SHELLS,
    ("close", "mode", "background"): protocol.CAP_BACKGROUND,
}

# What leaves the client by plain HTTP (`/api/blob`, `/api/upload`): any
# family's ask will do, the function says which by its URL.
HTTP_SENDS = frozenset({"http_get", "http_put"})

# The sites asked elsewhere, each with why (`module:qualname:type`).
ASKED_ELSEWHERE = {
    # The e2e probe raises on a refusal by design (`ClientSession._probe_ask`):
    # a misnamed probe, or one on a service without the debug API, must never
    # pass quietly.
    "clientsession:ClientSession.probe:debug.session.get": "the probe raises on a refusal",
    "clientsession:ClientSession.probe_set:debug.session.set": "the probe raises on a refusal",
    "clientsession:ClientSession.probe_call:debug.session.call": "the probe raises on a refusal",
    # D5: `tool` events are accepted from any server and `tool-reply` is only
    # ever an answer to one, so a server that sent the one hears the other.
    "toolclient:ToolClient._reply:tool-reply": "only an answer to a `tool` event the server sent",
    # `apilink.serves_restart`, which `test_a_restart_asks_its_own_question`
    # holds these two to: a pre-family Collins service serves it unlisted (D18).
    "app:App.restart_service:service.restart": "behind apilink.serves_restart",
    "app:App.cancel_service_restart:service.restart": "behind apilink.serves_restart",
    # The chip exists only on a session with a box, and its one road to the
    # service is `SandboxChip._ask`, which asks.
    "sandboxchip:SandboxChip._rebuild:sandbox.plan": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip._rebuild:sandbox.grants": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip.set_tool:sandbox.tools": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip.reset_tools:sandbox.tools": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip._revoke:sandbox.revoke": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip.allow_directory:sandbox.allow": "SandboxChip._ask asks",
    "sandboxchip:SandboxChip._restart:sandbox.restart": "SandboxChip._ask asks",
}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def asks_for(family: str | None):
    """A predicate for `apilink.has(<cap>)` of *family*, or of any family
    when None: the module's function, called with a name of the protocol's
    `CAP_*` (spelt `protocol.CAP_X`, `CAP_X`), or a name for a blob's own
    family (`blobcache.fetch`'s `family`)."""

    def is_ask(node: ast.AST) -> bool:
        if not (isinstance(node, ast.Call) and _dotted(node.func) == "apilink.has" and len(node.args) == 1):
            return False
        argument = node.args[0]
        if family is None:
            return True
        name = (_dotted(argument) or "").rpartition(".")[2]
        return getattr(protocol, name, None) == family

    return is_ask


class _Sites(ast.NodeVisitor):
    """Each family request in one module, with the function it is written in."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[ast.AST] = []
        self.sites: list[tuple[str, str, str | None, ast.AST, ast.AST | None]] = []

    def _qualname(self) -> str:
        return ".".join(n.name for n in self.stack if isinstance(n, (ast.FunctionDef, ast.ClassDef)))

    def _function(self) -> ast.AST | None:
        for node in reversed(self.stack):
            if isinstance(node, ast.FunctionDef):
                return node
            if isinstance(node, ast.Lambda):
                return None
        return None

    def _scoped(self, node: ast.AST) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scoped  # noqa: N815
    visit_ClassDef = _scoped  # noqa: N815
    visit_Lambda = _scoped  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute) and node.func.attr in HTTP_SENDS:
            self._record(node.func.attr, None, node)
        for argument in node.args:
            if isinstance(argument, ast.Dict):
                self._dict(argument, node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if isinstance(node.value, ast.Dict):
            self._dict(node.value, None)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.value, ast.Dict):
            self._dict(node.value, None)
        self.generic_visit(node)

    def _dict(self, literal: ast.Dict, call: ast.Call | None) -> None:
        fields = {
            key.value: value
            for key, value in zip(literal.keys, literal.values, strict=True)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        kind = fields.get("t")
        if not (isinstance(kind, ast.Constant) and isinstance(kind.value, str)):
            return
        message = protocol.TYPES.get(kind.value)
        if message is None:
            return
        if message.family != protocol.FAMILY_CORE:
            self._record(kind.value, message.family, literal)
            return
        keywords = {word.arg: word.value for word in call.keywords} if call is not None else {}
        for (name, field, value), family in FAMILY_FORMS.items():
            if kind.value != name:
                continue
            given = fields.get(field, keywords.get(field))
            if given is not None and any(
                isinstance(leaf, ast.Constant) and leaf.value == value for leaf in ast.walk(given)
            ):
                self._record(f"{name} {field}={value}", family, literal)

    def _record(self, what: str, family: str | None, node: ast.AST) -> None:
        self.sites.append((self.module, self._qualname(), family, node, self._function()) + (what,))


def family_sites() -> list[tuple]:
    sites: list[tuple] = []
    for module, tree in client_modules().items():
        walker = _Sites(module)
        walker.visit(tree)
        sites.extend(walker.sites)
    return sites


def test_every_family_request_is_behind_its_ask() -> None:
    bad = []
    for module, qualname, family, node, function, what in family_sites():
        site = f"{module}:{qualname}:{what}"
        if site in ASKED_ELSEWHERE:
            continue
        if function is None or not pathless.behind_an_ask(function, node, asks_for(family)):
            bad.append(site if family is None else f"{site} (needs apilink.has of {family!r})")
    assert not bad, (
        "these send a family's request with no `apilink.has(...)` before it in the same function "
        "(D5: ask before the send, in one of the shapes pathless._behind_the_ask reads). Ask, or "
        "file the site in ASKED_ELSEWHERE with why:\n  " + "\n  ".join(sorted(bad))
    )


def test_a_restart_asks_its_own_question() -> None:
    def is_ask(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and _dotted(node.func) == "apilink.serves_restart"

    restarts = [site for site in family_sites() if site[5] == "service.restart"]
    assert len(restarts) == 2
    for _module, qualname, _family, node, function, _what in restarts:
        assert function is not None and pathless.behind_an_ask(function, node, is_ask), qualname


def test_every_exception_still_matches_a_site() -> None:
    found = {f"{module}:{qualname}:{what}" for module, qualname, _f, _n, _fn, what in family_sites()}
    stale = sorted(set(ASKED_ELSEWHERE) - found)
    assert not stale, f"ASKED_ELSEWHERE names sites that are gone: {stale}"


def test_the_walker_finds_what_it_says() -> None:
    """A floor under the walker: if the literal's shape changes, finding
    nothing must not look like a pass."""
    found = {what for *_rest, what in family_sites()}
    for expected in (
        "pr.action",
        "fs.read",
        "git.run",
        "job.start",
        "usage.get",
        "panel.key",
        "cut",
        "cwd.settle",
        "sandbox.plan",
        "spawn kind=shell",
        "close mode=background",
        "http_get",
        "http_put",
    ):
        assert expected in found, f"the walker no longer finds {expected}"


def _sample(body: str) -> tuple[ast.FunctionDef, ast.Dict]:
    function = ast.parse(body).body[0]
    literal = next(n for n in ast.walk(function) if isinstance(n, ast.Dict))
    return function, literal


ASK = "apilink.has(protocol.CAP_PRS)"
SEND = 'send({"t": "pr.set"})'


@pytest.mark.parametrize(
    ("body", "behind"),
    [
        (f"def f():\n    if not {ASK}:\n        return\n    {SEND}\n", True),
        (f"def f():\n    if {ASK}:\n        {SEND}\n", True),
        (f"def f():\n    {ASK} and {SEND}\n", True),
        (f"def f(x):\n    if x or not {ASK}:\n        return\n    {SEND}\n", True),
        (f"def f():\n    {SEND}\n", False),
        (f"def f():\n    {SEND}\n    {ASK}\n", False),
        # Another family's ask is no ask.
        (f"def f():\n    if not apilink.has(protocol.CAP_GIT):\n        return\n    {SEND}\n", False),
        # The attribute, not called: always truthy.
        (f"def f():\n    if not apilink.has:\n        return\n    {SEND}\n", False),
    ],
)
def test_the_shapes_of_an_ask(body: str, behind: bool) -> None:
    function, literal = _sample(body)
    assert pathless.behind_an_ask(function, literal, asks_for(protocol.CAP_PRS)) is behind


# ---- what is read as a widget is built (ruling 20) --------------------------------
#
# A hello can arrive after the widget: every `apilink.has` evaluated at
# construction has to be evaluated again by `apply_families`. So it lives in a
# named `_sync_*` the constructor and `apply_families` both call.

# The constructors whose asks need no `_sync_*`, each with why.
BUILT_AGAIN = {
    "sidebar:GroupHeaderRow.__init__": "rebuilt by SessionSidebar.apply_families",
    "sidebar:SessionRow.__init__": "rebuilt by SessionSidebar.apply_families",
    "prefs:PreferencesDialog.__init__": "built as it opens",
    "prefs:PreferencesDialog._build_general_group": "built as it opens",
    "prefs:PreferencesDialog._build_sessions_group": "built as it opens",
    "prefs:PreferencesDialog._build_composer_group": "built as it opens",
    "welcome:WelcomeDialog.__init__": "built as it opens",
}

_ASKS = frozenset({"apilink.has", "apilink.predates_families", "apilink.serves_restart"})


def _own_nodes(function: ast.AST):
    """What *function* runs itself: not a nested function's or a lambda's body,
    which run later and ask then."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _asks_in(function: ast.AST) -> bool:
    return any(isinstance(node, ast.Call) and _dotted(node.func) in _ASKS for node in _own_nodes(function))


def _self_calls(function: ast.AST) -> set[str]:
    called = set()
    for node in _own_nodes(function):
        if isinstance(node, ast.Call):
            name = _dotted(node.func) or ""
            if name.startswith("self.") and name.count(".") == 1:
                called.add(name[len("self.") :])
    return called


def _classes() -> list[tuple[str, ast.ClassDef, dict[str, ast.FunctionDef]]]:
    found = []
    for module, tree in client_modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}
                found.append((module, node, methods))
    return found


def _is_constructor(name: str) -> bool:
    return name == "__init__" or name.startswith("_build")


def test_no_constructor_asks_in_its_own_body() -> None:
    bad = [
        f"{module}:{cls.name}.{name}"
        for module, cls, methods in _classes()
        for name, method in methods.items()
        if _is_constructor(name) and _asks_in(method) and f"{module}:{cls.name}.{name}" not in BUILT_AGAIN
    ]
    assert not bad, (
        "these read `apilink.has` as a widget is built, and a hello can arrive after it (ruling 20). "
        "Move it to a `_sync_*` the class's `apply_families` calls too, or say why in BUILT_AGAIN:\n  "
        + "\n  ".join(sorted(bad))
    )


def test_every_exception_to_the_constructors_still_asks() -> None:
    asking = {
        f"{module}:{cls.name}.{name}"
        for module, cls, methods in _classes()
        for name, method in methods.items()
        if _is_constructor(name) and _asks_in(method)
    }
    assert not set(BUILT_AGAIN) - asking, sorted(set(BUILT_AGAIN) - asking)


def test_every_sync_that_asks_is_reached_from_apply_families() -> None:
    bad = []
    for module, cls, methods in _classes():
        syncs = {n for n, m in methods.items() if n.startswith("_sync_") and _asks_in(m)}
        if not syncs:
            continue
        reached: set[str] = set()
        todo = ["apply_families"] if "apply_families" in methods else []
        while todo:
            name = todo.pop()
            if name in reached or name not in methods:
                continue
            reached.add(name)
            todo.extend(_self_calls(methods[name]))
        bad.extend(f"{module}:{cls.name}.{name}" for name in syncs - reached)
    assert not bad, f"not called from their class's apply_families: {sorted(bad)}"


def test_a_widget_built_from_one_hello_follows_the_next_down_the_tree() -> None:
    """`MainWindow.apply_families` reaches each class that keeps a `_sync_*`."""
    classes = {cls.name: methods for _module, cls, methods in _classes()}
    source = ast.dump(classes["MainWindow"]["apply_families"])
    assert "sidebar" in source and "apply_families" in source
    tab_method = classes["TerminalTab"]["apply_families"]
    tab = {node.attr for node in ast.walk(tab_method) if isinstance(node, ast.Attribute)}
    assert {"_sync_family_surfaces", "_sync_attachments_handle", "_sync_follow_judge", "_dock"} <= tab
    assert "_sync_add_btn" in _self_calls(classes["PanelStrip"]["apply_families"])


# ---- the transports, with the family gone ----------------------------------------


class _Recording(apilink.Link):
    """A link that would answer, and remembers being asked."""

    def __init__(self, families: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.families = families
        self.asked: list[dict] = []

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        self.asked.append(message)
        return {}

    def http_get(self, *args, **kwargs):
        self.asked.append({"t": "http_get"})
        return 200, {}, b""

    def http_put(self, *args, **kwargs):
        self.asked.append({"t": "http_put"})
        return 200, {}, b'{"path": "/x"}'


@pytest.fixture
def bare_link():
    link = _Recording()
    apilink.set_current(link)
    yield link
    apilink.set_current(None)


def test_has_reads_the_last_hello(bare_link) -> None:
    assert not apilink.has(protocol.CAP_PRS)
    bare_link.families = frozenset({protocol.CAP_PRS})
    assert apilink.has(protocol.CAP_PRS)
    assert not apilink.has(protocol.CAP_GIT)


def test_has_with_nothing_to_go_by_says_nothing_is_missing() -> None:
    apilink.set_current(None)
    assert apilink.has(protocol.CAP_PRS)  # the service's own process, a test, a bare widget
    fake = apilink.Link()
    apilink.set_current(fake)
    try:
        assert apilink.has(protocol.CAP_PRS)  # a fake with no hello to go by
    finally:
        apilink.set_current(None)


@pytest.mark.parametrize(
    ("hello", "predates"),
    [
        # The one shape: what a pre-family Collins service's hello could say.
        ({"caps": ["local", "git", "files"]}, True),
        ({"caps": ["local", "debug", "git", "files"], "version": "0.1.4"}, True),
        ({"caps": []}, True),
        ({}, False),  # no hello yet
        # A name, or any family, and it is a server of this API's.
        ({"caps": [], "implementation": "stub-server"}, False),
        ({"caps": ["git", "files"], "implementation": "clinic-server"}, False),
        ({"caps": ["local", "prs"]}, False),
        ({"caps": ["internal"]}, False),
    ],
)
def test_only_one_hello_is_read_as_a_pre_family_service(bare_link, hello, predates) -> None:
    bare_link.hello = hello
    bare_link.families = frozenset(hello.get("caps") or ())
    assert apilink.predates_families() is predates
    assert apilink.serves_restart() is predates or protocol.CAP_INTERNAL in bare_link.families


def test_the_shape_is_every_cap_a_pre_family_service_could_not_list() -> None:
    """A cap added later is one such a service never listed either, and goes
    in the set; `local`, `debug`, `git` and `files` are what it did list."""
    assert apilink._SINCE_FAMILIES == protocol.CAPABILITIES - {
        protocol.CAP_LOCAL,
        protocol.CAP_DEBUG,
        protocol.CAP_GIT,
        protocol.CAP_FILES,
    }


def test_a_restart_with_no_link_is_nobody_s_business() -> None:
    apilink.set_current(None)
    assert apilink.predates_families() is False
    assert apilink.serves_restart() is True  # as `has` answers with no link


def test_implementation_is_bounded(bare_link) -> None:
    assert apilink.implementation() is None
    bare_link.hello = {"implementation": "clinic-server"}
    assert apilink.implementation() == "clinic-server"
    bare_link.hello = {"implementation": "x" * (protocol.IMPLEMENTATION_MAX + 1)}
    assert apilink.implementation() is None
    bare_link.hello = {"implementation": 7}
    assert apilink.implementation() is None


def test_files_answer_with_no_round_trip(bare_link) -> None:
    from collins import remotefiles

    for ask in (
        lambda: remotefiles.read("/x"),
        lambda: remotefiles.write("/x", "y", None),
        lambda: remotefiles.stat_path("/x"),
        lambda: remotefiles.list_dir("/x", "/x"),
        lambda: remotefiles.walk_root("/x"),
        lambda: remotefiles.root_names("/x"),
        lambda: remotefiles.rename_path("/x", "/y", "/"),
        lambda: remotefiles.paste_files(["/x"], "/y", False, "/"),
        lambda: remotefiles.list_dirs("/x"),
        lambda: remotefiles.new_dir("/x"),
        lambda: remotefiles.make_dir("/x", "/"),
    ):
        with pytest.raises(protocol.RequestRefused) as refused:
            ask()
        assert refused.value.error == protocol.ERROR_UNKNOWN
        assert remotefiles.refusal_words(refused.value) == "This service has no files"
    assert remotefiles.present("/x") is False
    assert remotefiles.locate("/x") is None
    assert bare_link.asked == []


def test_a_watch_sends_nothing(bare_link) -> None:
    from collins import remotefiles

    remotefiles.install(bare_link)
    try:
        handle = remotefiles.watcher().watch("/x", lambda _event: None)
        remotefiles.watcher().unwatch(handle)
    finally:
        remotefiles.uninstall()
    assert bare_link.asked == []


def test_git_answers_with_no_round_trip(bare_link) -> None:
    from collins import gitinfo, gitops, remotegit

    remotegit.install(bare_link)
    try:
        assert gitinfo.reader()("/x") is None
        ran = gitops.run_git("/x", ["status"]) if hasattr(gitops, "run_git") else None
        transport = remotegit._transport
        result = transport.run("/x", ["status"], None, 1.0, None, (0,))
        assert result.unreachable and not result.ok and "no git" in result.stderr
        assert ran is None or not ran.ok
        blob = transport.blob("/x", "worktree", None, "a", 1.0)
        assert blob.unreachable and not blob.ok
        assert transport.sizes("/x", ["a"]) == {"a": None}
        remotegit.mirror().watch("/x", ["a"], handle="h")
        remotegit.mirror().unwatch("h")
        called: list = []
        remotegit.mirror().refresh_then("/x", lambda: called.append("then"))
        remotegit.mirror().state_then("/x", called.append)
        assert called == ["then", None]
    finally:
        remotegit.uninstall()
    assert bare_link.asked == []


def test_prs_answer_with_no_round_trip(bare_link) -> None:
    from collins import prblobs, remoteprs
    from collins.prstatus import PullRequest

    pr = PullRequest(number=1, url="https://github.com/o/r/pull/1")
    assert remoteprs.perform("merge", pr) == "This service has no pull requests"
    assert remoteprs.comment(pr, "x") == "This service has no pull requests"
    assert remoteprs.review(pr, "approve") == "This service has no pull requests"
    assert remoteprs.reply_in_thread(pr, "t", "x") == "This service has no pull requests"
    assert remoteprs.set_thread_resolved(pr, "t", True) == "This service has no pull requests"
    assert remoteprs.fetch_detail(pr.url) is None
    assert remoteprs.fetch_threads(pr.url) == ()
    assert remoteprs.sweep([("s", [pr], "/x")]) == {}
    assert remoteprs.resync([pr]) == [pr]
    remoteprs.invalidate(pr.url)
    with pytest.raises(prblobs.BlobError):
        remoteprs.fetch_blob("o/r", "abc", "a.png")
    assert bare_link.asked == []


def test_a_job_is_refused_with_no_round_trip(bare_link) -> None:
    from collins import jobclient

    events: list = []
    assert jobclient.jobs_for(bare_link).start("login.repair", {}, events.append) is None
    assert [event.state for event in events] == [protocol.JOB_REFUSED]
    jobclient.jobs_for(bare_link).cancel("nope")
    assert bare_link.asked == []


def test_a_project_s_icon_is_the_files_family(bare_link) -> None:
    """Ruling 21: a file at the project's root, which costs no token."""
    from collins import blobcache, remoteicons

    assert blobcache.family_of(blobcache.icon_url("/x")) == protocol.CAP_FILES
    assert remoteicons.icon_bytes("/x") is None
    bare_link.families = frozenset({protocol.CAP_USAGE})
    assert remoteicons.icon_bytes("/x") is None  # `usage` alone does not serve it
    assert bare_link.asked == []


def test_with_no_files_a_folder_is_no_checkout(bare_link) -> None:
    """Ruling 20: no box was shown and nothing promised, so the launch is plain;
    None would refuse a Send with a worktree wish for ever."""
    from collins import checkouts

    answers: list = []
    checkouts.ask_answer("/x", answers.append)
    assert answers == [False]
    assert bare_link.asked == []


def test_the_catalog_falls_back_with_no_round_trip(bare_link) -> None:
    from collins import modelcatalog

    assert modelcatalog.cached_models() is None
    assert modelcatalog.available_models() == []
    assert modelcatalog.cache_failed() is False
    assert modelcatalog.cli_default_model() is None
    assert modelcatalog.cli_default_effort() is None
    assert bare_link.asked == []


def test_there_is_a_static_list_to_fall_back_on() -> None:
    from collins import claudemodels

    assert claudemodels.FALLBACK_MODELS  # the [verify] of D5's `usage`


def test_a_blob_goes_out_only_for_a_family_the_service_lists(bare_link) -> None:
    from collins import blobcache

    for url in (
        blobcache.file_url("/x"),
        blobcache.remote_url("https://e/x.png"),
        blobcache.icon_url("/x"),
        "/api/blob?kind=pr&repo=o/r",
        "/api/blob?kind=git&cwd=/x",
    ):
        assert blobcache.family_of(url) is not None
        with pytest.raises(ValueError):
            blobcache.fetch(url, ".png")
    assert bare_link.asked == []


def test_an_upload_goes_out_only_with_tools(bare_link) -> None:
    from collins import remoteuploads

    with pytest.raises(ValueError):
        remoteuploads.upload(b"x", "a.png")
    assert bare_link.asked == []


def test_the_cli_ask_goes_out_only_with_internal(bare_link) -> None:
    from collins import clisetup, remotecli

    assert remotecli.detect() == ""
    verdicts: list = []
    remotecli.check("/x", verdicts.append)
    assert verdicts == [clisetup.MISSING]
    done: list = []
    remotecli.refresh(lambda: done.append(True))
    assert done == [True]
    assert bare_link.asked == []


def test_the_shells_history_goes_out_only_with_shells(bare_link) -> None:
    from collins import panelhistory

    assert panelhistory.history_requests("k", [{"ordinal": 0, "text": "x"}]) == []


def test_the_marks_stay_on_the_page_without_tools(bare_link) -> None:
    from collins.remotediffs import DiffNotesMirror

    mirror = DiffNotesMirror(bare_link)
    mirror.send("s", True, [{"note": 1}], [])
    assert mirror.marks("s") == ([{"note": 1}], [])
    assert bare_link.asked == []
