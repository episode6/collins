---
name: collins-testing
description: >-
  How Collins is tested and how to test a change in it: the GTK-free pytest
  unit suite and its import blocklist, the scripts/check_*.py end-to-end
  checks driven by scripts/run_e2e.py under a headless display, the claude
  shim pattern, headless probe scripts, ruff, and what CI runs. Use whenever writing or running tests for Collins, adding
  an e2e check, debugging a CI-only failure (e2e flaky, idle starvation, a
  dialog on top), or deciding where a piece of logic must live so it can be
  tested.
---

# Testing Collins

## The two suites

**Unit suite** — `python3 -m pytest tests/ -q`. Runs on `python3-gi` alone:
GLib, GObject and Gio are importable, but `tests/conftest.py` installs a
meta-path finder that raises `ImportError` for `gi.repository.{Gtk, Adw, Gdk,
Gsk, Graphene, Vte}`, in both import styles (`gi.require_version` and a bare
`from gi.repository import Gtk`). So a test may import only GTK-free modules.
This is why the codebase splits every feature into a pure module (`editorfiles`,
`gitinfo`, `docktree`, `prstatus`, `notifycenter`, `traymodel`, `composerkeys`,
`gitloads`, `diffmodel`, `gitpatch`, `gitfresh`, …) and a widget module that imports it. If logic you want to test
sits in a widget module, move it into the pure sibling first — that is the
convention, not a workaround. Modules whose pure half needs key constants
spell keyvals and modifier bits as integers for the same reason
(`composerkeys`, `panelkeys`).

**The pathless test** (`tests/test_client_is_pathless.py`, required: it
runs with the rest of the suite and needs only `ast`). `tests/pathless.py`
walks every GTK module and the `CLIENT_HELPERS` for filesystem and
subprocess calls; each site (``module:qualname:call``) must be in
`tests/pathless_allowlist.py`, and each entry must still match a site.
The list is final since PR-2.8: a new `os.path.isdir`,
`Path.read_text`, `shutil.which` or `subprocess` call in a widget module
fails the suite until it is filed, and each group is pinned by its size
(`DEVICE` per module too), so filing one is an edit of the test that a
reviewer sees; only this device's own files and gated local extras
belong. Ask the service instead (`remotefiles.stat_path` and
friends, off the main loop). The four groups have a rule each: `DEVICE`
is this device's own files and programs; a `LOCAL_EXTRAS` site must be
**behind** `apilink.is_local()` in its own function
(`pathless.function_asks_local` reads the AST: every call of the site
after an earlier `if not apilink.is_local(): return` in the function's
own body, or inside `if apilink.is_local():`, or after the ask in the
same `and`. The ask is `apilink`'s function, called: not the bare
attribute, not another object's `is_local()`, not one after the read, in
another branch, inside a lambda or bound to a name first; a closure does
not inherit its parent's ask). Every function a qualname names is held
to the ask (a property's getter and its setter), and a function that
binds `apilink` itself has none. Since PR-3.0 (D59) the list is those
two groups and `NOT_A_SITE` only: `UNRULED` (three sites) and
`RECORDED` (four `gitloads` default arguments) were ruled and moved, and
importing either from `pathless_allowlist` fails a test.

**When the test names a site that is no read.** The any-receiver words
and the name hints match by name: `entry.is_dir()` on a service fact,
`decoder.load_bytes(data)`, a `Gtk.Root` called `root` with
`.resolve()`. Such a site has no truthful group. Rename a local variable
that only looks like a path; otherwise add the site to
`pathless_allowlist.NOT_A_SITE` with the number of calls it covers and
a line saying what the receiver is, and edit
`test_what_is_no_site_is_pinned_with_its_reason_and_its_count` in the
same diff. The failing test prints the four answers (ask the service;
gate and `LOCAL_EXTRAS`; `DEVICE`; `NOT_A_SITE`). `HONEST` in the test
is the table of what the walker says of honest code.

**What the walker reads, and what it does not.** It resolves import
aliases (`import shutil as sh`, `from os import path as P`), follows a
name bound to a path or a `Gio.File` inside a function (an assignment, a
tuple, a walrus, a `Path` annotation or default, a loop over
`.iterdir()`, a name bound to a bound name), looks through a path's own
method chains (`Path(p).expanduser().resolve().is_dir()`), takes
`is_dir` / `is_file` / `read_text` / `iterdir` and the other names only
a path has on any receiver at all, and counts a site's function that is
handed on or bound without being called (`map(os.path.isdir, ps)`, a
`run=subprocess.run` default; a path's method the same: `filter(Path.
is_dir, ps)`, `key=Path.stat`), and takes the names only a `Gio.File`
has (`load_contents`, `load_bytes`, `query_info`, `query_exists`, …)
and `spawn_async` / `spawn_with_fds_async` / `spawnv` on any receiver,
the keyword-property constructors of the GTK file openers
(`Gtk.Picture(file=g)`, `GtkSource.FileLoader(buffer=b, location=f)`)
and `set_property("file", …)`, `io.FileIO` / `io.open_code` /
`posixpath.*`, and a `Gio.File` renamed, re-moded or made
(`set_display_name`, `set_attribute*`, `new_tmp`): the five families the
2026-10-18 Fable pass found missing. And, since PR-3.0, the launchers:
`Gtk.FileLauncher.new`, `Gtk.UriLauncher.new`,
`Gio.AppInfo.launch_default_for_uri(_async)`, `open_containing_folder`
and `launch_uris*` on any receiver, and `launch` on a receiver named for
an app's info (`info`, `app_info`): a launch of a web address or of a
file this device has is `DEVICE`, anything else a local extra behind the
ask. `test_the_walker_catches` and
`test_the_walker_is_known_to_miss` are tables of both: add a row when
the vocabulary changes. It does **not** follow calls: a site is named by
the function the read is written in, so a widget calling a disk-reading
function of `sessions`, `providers`, `clonerepo`, `panelhistory` or a
helper of its own module is invisible to it. Nor does it see a hintless
receiver for the method names other objects share (`p.exists()`,
`self._thing.unlink()`, `write_text`), `launch` on an app's info whose
name does not say so, `expanduser` / `abspath`, a star
import, `asyncio` / `pty` / `multiprocessing`, or a read through
another library. Check those by hand when adding one. The
ones known today are recorded (spec D50; D54 ruled and PR-3.0 moved the
first list's): the record is the comment in
`tests/pathless_allowlist.py`, which names every form the walker misses.

**The families walker** (`tests/test_client_families.py`, required). The
window hides a family the service does not list and never sends its requests
(server-side spec D5). The test walks every client module with `ast` for a
dict literal whose `"t"` is a type of a family other than `core`, the two
`core` forms a family owns (`spawn` with `kind: "shell"`, `close` with `mode:
"background"`) and every `http_get` / `http_put`, and holds each to
`apilink.has` of that family, earlier in the same function and in a shape
`pathless.behind_an_ask` reads (the pathless walker's own: `if not
apilink.has(...): return`, inside `if apilink.has(...):`, after it in an `and`).
A new family request fails the suite until it asks; `ASKED_ELSEWHERE` is the
list of those that cannot, each with its reason. It reads no surface: a
button left showing is `check_server_families.py`'s to find. The file's second
half calls each transport with the family gone and asserts nothing reached the
link. `apilink.has` answers yes with no link and for a fake with no hello
(`families` None), so a unit test of a mirror is unchanged; a test of the gate
installs a link with `families` set (`no_families` in
`tests/test_clientsession.py`). The same file holds what is read as a widget is
built (ruling 20): no `apilink.has` directly in a constructor's body, unless that
widget is rebuilt on a hello or built as it opens (`BUILT_AGAIN`, each with its
reason), and every `_sync_*` that asks is reached from its class's
`apply_families`.

Fixtures worth knowing (all in `tests/conftest.py`): `projects_dir` (a fake
`~/.claude/projects` with two projects, monkeypatched into `sessions` and
forcing `ClaudeProvider.available()` True), `app_state` (an `AppState`
isolated to a temp config dir, panel-history dir redirected), and an autouse
fixture clearing `prstatus._listeners` (every `SessionStore` a test builds
registers a `PrStore` there). `make_transcript_lines(cwd, text)` builds
realistic JSONL entries. Modules with injectable transports (`usage`,
`remotearchive`, `remoteimages`, `updatecheck`, `claudemodels`) are tested
against fakes or a local HTTP server, never the network.

**E2E checks** — `scripts/check_<name>.py`, each a self-contained script that
stages its own scratch tree and app id, builds a real `App`, drives real
widgets (and where needed a real VTE child behind a `claude` shim on `PATH`),
prints `ok`/`FAIL` lines and exits non-zero on failure. `scripts/run_e2e.py`
discovers them (no registration), runs them serially — each under its own
`dbus-run-session` so bus-name owners like `check_status_icon.py` never
collide — with a per-check timeout, one retry (a pass on retry is reported
"flaky", not failed), and a GitHub step summary. Run locally behind the
headless compositor so nothing appears on screen:

```bash
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
    python3 scripts/run_e2e.py --only new_chat --timeout 120
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
    python3 scripts/run_e2e.py --shard 2/5          # what CI's second leg runs
python3 scripts/run_e2e.py --list --shard 2/5       # …and its estimate; no display needed
```

The uploads are isolated twice over. The wrapper exports one scratch
`COLLINS_UPLOADS_DIR` for everything it runs (so a check run directly
under it never touches `~/.local/share/collins/uploads`), and the runner
gives each run of a check a fresh directory inside whatever the
environment names, a retry included. Both matter: `check_composer_paste.py`
and `check_drop_upload.py` count the files in the pending folder, and
with only the wrapper's directory they shared it, so under one wrapper
`drop_upload` found `composer_paste`'s two pastes and failed on the count
(CI never saw it: `xvfb-run` sets no `COLLINS_UPLOADS_DIR`, so the runner
minted one per check there). A check that counts its uploads also sets
`COLLINS_UPLOADS_DIR` in its own preamble, as those two do, so a direct
run is isolated as well.

The service and the mirrors have modules of their own. `tests/test_remote_state.py`
drives `remotestate.RemoteState` through a fake link that holds every reply
until the test answers it (a refused write reverts, notifies and toasts; an
event landing while a write is unanswered does not clobber it; the draft
debounce, with a fake timer; `get_setting`'s routing). `tests/test_remote_store.py`
runs the real `ServiceCore` (its own `AppState` and a `SessionStore` fed the
`projects_dir` sessions) behind `tests/inproc.py`, the unit suite's in-process
harness (`LoopbackServer` / `LoopbackLink` over a core: the same dicts and
bytes a socket carries, validated both ways, no socket and no thread; test
code only, nothing in `collins/` imports it) with both mirrors on it: the
snapshot, an `item` moving one property and its signal, every mutation,
archived paging, the service refusing a device setting. Where the socket
itself is the subject (`test_api_client.py`, `test_service_main.py`),
`tests/liveservice.py` starts a real `collins-service` subprocess on scratch
directories (with `COLLINS_KEEPER=none` unless built with `keeper=True`:
a keeper outlives the service by design, so a live service with one needs
`LiveService.close()`, which stops the keeper too by the pid file beside
its socket; `tests/test_keeper.py` is the spike's cases as tests, the pty
server adopting through a keeper, and a real service restarted under a
running shell). `tests/test_api_server.py` runs a real `Soup.Server` on a
scratch socket; `test_connection.py` drives the connection manager's state
machine with fakes, its remote path included (a `FakeTransport` whose
forward the test kills: the start's word, the lost road, a hello failing
over a live forward, `switch`); `test_sshremote.py` the two ssh argv, each
failure as a code, the local socket unlinked first (a non-socket refused), a
fake `Popen` whose stderr the test writes, the checks on a recorded pid with
no `/proc` and no pidfd (`sshremote.PROC_DIR` and `PS` are the seams: a fake
`ps`, then the real one), a discovery command through the wrapper under the
real shells present (the rest skip), and that the module runs as one
file copied out of the tree; `test_sshforward.py` Collins' word for each
failure and a forward's exit as the transport's `lost`; `test_connections.py` the list in `ui-state.json`
(rule 5's grammar, `find`, `last_connection`, a `command` that does not fit
dropped alone). `scripts/check_connect_dialog.py` is the dialog's form behind
an `ssh` stub on PATH (*Advanced*, Test's argv, Add's row). The by-hand half of PR-3.1
(the connect dialog's Test and Connect against the ssh target of
`~/specs/collins/assets/split-service/ssh-target/`, the forward killed, the
banner and the reconnect) has no e2e check: it needs a container the CI
lacks, and the record of a run lives in the PR. `tests/test_service_imports.py` imports every module of
`collins.service` and `collins.api` in a fresh subprocess and fails on any
`gi.repository` widget library (GLib, GObject and Gio are allowed): the
service must run with no display. `tests/test_client_boundary.py` reads
`window.py` and `app.py` with `ast` (the window is a GTK module the suite
cannot import) and fails if the client does again what the service now does:
the agent-list poller, a transcript read for a /bg handoff, a chat folder,
the archive sweep.

Real ptys are fine in the unit suite: `tests/test_ptyserver.py` spawns
`cat` and `sh -c` children on ptys the server owns and iterates the
default GLib main context by hand (its `pump`), no display, no GTK. Put the
pty in raw mode from the master side (`tty.setraw(master)`) rather than
with `stty` in the child: the child's `stty` races the first write, and the
line discipline's `^[` echo of an escape then lands in the output.

Also: `ruff check collins/ tests/` (CI pins `ruff==0.16.4`, rules
`E F W I UP B`, `E402` ignored for `gi.require_version` ordering; UP035 means
`Callable` comes from `collections.abc`).

## Writing an e2e check

**A client that is not local.** No transport makes one before Phase 3,
so a check takes the proof away: `apilink.current().local = False` on
the app's link (`check_local_extras.py`), or a second `SocketLink` that
never calls `prove_local` (`check_drop_upload.py`,
`check_filetree_ops.py`). `apilink.is_local()` is the one reader, so
every gate follows at once; widgets that decided at build time (the
footer) are read on a tab built afterwards. `check_local_extras.py` is
the pattern for anything that would start a program or open the file
manager: it replaces `Gtk.FileLauncher.new`, `Gtk.UriLauncher.new` and
`Gio.AppInfo.launch_default_for_uri` with a recorder before importing
`collins` (an assignment on the GI class; every module sees it), clicks
links of each scheme both ways, wraps `footerapps.launch_app`, stages its
own `ghostty` and footer-app stubs on `PATH` and under a scratch
`XDG_DATA_HOME`, and gives the service a scratch `$HOME` (`start_service
({**os.environ, "HOME": ...})`), so nothing reaches the desktop even
when a gate is broken. A window's actions are `win.lookup_action(name).
activate(variant)`: `win.activate_action("name", …)` without the `win.`
prefix does nothing and makes a "nothing happened" assertion vacuous.

**The probe** (D27). A session's logic runs on the service, so a
check never reads a tab's privates: `tab.probe(name)`,
`tab.probe_set(name, value)` and `tab.probe_call(name, *args, **kwargs)`
are the `debug.session.*` requests (a dotted name walks from the
`Session`: `finish_ledger.armed`, `transcript.set_path`,
`host.session_resolved`, `activity.tracker.mark`, `activity.judge.held`),
`debug.screen` / `debug.pty` read a pty's model and process facts, and
`debug.sandbox` calls the sandbox host, the live grants or the core, and
`debug.local {local}` sets whether the service treats the asking client as
local (D60). `e2e_service.drop_local(link)` calls it with False and clears
`link.local`, so both ends agree the client is what an ssh client is (the
service confines its `fs.*` and serves it a file the agent named whole,
D51, which a local client is never served; `apilink.is_local()`
and `app.local` say no); a reconnect proves again, so call it again after
one. `check_local_extras.py` and `check_path_picker.py` use it. The
service serves them only with `COLLINS_DEBUG_API=1` in its environment:
every check sets it in its preamble and `e2e_service.start_service()`
passes it to the service it spawns (a service the app starts never has it).
`debug_patch`, `debug_find_handle`, `debug_spy_writes` and `debug_tools_*`
are the core's methods behind `debug.sandbox`. A probe answers None with no pty
(before a new chat's Send, after the exit) and raises on a refusal. Keep a
check's assertions as they were: only the call path moves.

**Every check starts its own service.** The service is its
own process, so a check that builds an `App()` calls
`e2e_service.start_service()` right before `App()`: after its scratch
tree, every `COLLINS_*` / `XDG_*` override and the `claude` shim on
`PATH` are in the environment, since the service inherits the environment
it is started with and spawns the shells. The helper runs
`python3 -m collins.service.main --app-id <the check's id>` out of this
checkout with `COLLINS_DEBUG_API=1`, waits for the socket and SIGTERMs it
at exit. A check that drives widgets with **no `App`** behind them and
reaches the service (a PR page's gh requests, a job, the model catalog)
calls `e2e_service.harness_link()` instead: a `SocketLink` to a service
of its own, installed as the current link. `scripts/run_e2e.py` sets
nothing new. Importing `e2e_service` sets `XDG_RUNTIME_DIR` to a short
scratch directory of the check's own (a Unix socket path is bounded at
107 bytes; the user's `/run/user/<uid>/collins/` never sees a check's
id), removed at exit; the service is spawned with `PR_SET_PDEATHSIG` so
a check dying on its deadline takes it along.
Since PR-3.6 every pty is forked by a **keeper**, which the harness starts
itself before the service (`e2e_service.start_keeper`, as the check's own
child with `PR_SET_PDEATHSIG`, so a check killed on its deadline, a
segfault or a SIGKILL takes the keeper and the check's shims with it; the
service finds it on its socket rather than spawning one detached, and
`LiveService(keeper=True)` does the same; both from the main thread only,
since Linux ties `PR_SET_PDEATHSIG` to the forking thread and a worker
thread's exit would SIGTERM the keeper and every session with it). A service's SIGTERM leaves its
ptys to the keeper rather than ending them: `e2e_service`'s `atexit` stops
the keeper too (`_stop_keepers`, by the pid file beside its socket, a
SIGKILL after a bounded wait), so a check's shims never outlive the
check; `stop_service(proc)` followed by `start_service()` on the same app
id is a service restart under the check's sessions
(`check_service_restart.py`), and `keeper_pid()` says who holds them. A
check that wants the keeperless stop sets `COLLINS_KEEPER=none` before
`start_service()`. A watchdog that leaves through `os._exit` still calls
`e2e_service._stop_all()` first, as `check_service_restart.py` does.

**A patch over a service-side module does not reach the service.** What a
check used to monkeypatch in its own process (`claudemodels.*`,
`prdetail.fetch`, `prstatus.gh_json`) it hands to
`start_service(stubs={...})` / `harness_link(stubs=...)` as JSON records;
`scripts/e2e_stubs.py` applies them inside the service (only under the
probe's flag), re-reads the data on every call (`e2e_service.
update_stubs` restages a step's detail) and records every stubbed call to
a file `e2e_service.stub_calls()` reads. A constant to lower on the
service is `debug.sandbox` with target `core` and name `debug_patch`
(`check_composer_paste_back.py`'s piece limits); the tools' dispatcher is
`debug_tools_list` / `debug_tools_dispatch` the same way.

**A server that does not list a family.** `start_service(stubs={"caps_drop":
[cap, ...]})` takes those caps out of the hello and answers their requests,
family forms, blob kinds and uploads `unknown`, recording each as a `refused`
stub call. `check_server_families.py` is the pattern: a process per family, a
few at a time (a window builds from the hello it finds), its `SURFACES` table
asserted gone there and shown in a run that drops nothing, so a "hidden" cannot
pass vacuously. `stubs={"refuse_subscribe": 2}` is a first subscribe refused and
the reconnect's after it (the number is how many),
and `"legacy_hello": True` beside a `caps_drop` is a pre-family Collins service
(no `implementation` in the hello, `service.restart` still served). The stub
otherwise keeps `implementation`, so a service with every family dropped is not
read as one. That run also walks the road on a faked hello, one that lists every
family and then one that does not, since whatever a window builds from one hello
must follow the next. The `upgrade` run takes it for real: `stop_service` and
`start_service` under the window, the panels back with no reopen.
`scripts/stub_server.py` is a whole Level 0 server, for looking at the window by
hand; it prints what it refuses.

**Two checks own the process boundary.** `check_service_survives_client.py`
starts a session, SIGKILLs the app, starts a second app on the same service
and opens the running row (the screen compared with `debug.screen`);
`check_detach_reopen.py` covers detach, the running row, reopen on attach,
*Stop Sessions* (`service.status.ptys` empty) and `open_tabs` surviving a
relaunch. A check that needs the service's own files finds them where
`api.server` says: `api.sock`, `service.lock` and `local-proof` under
`$XDG_RUNTIME_DIR/collins/<app id>/` (the helper's scratch runtime dir).

**A write's outcome lands a moment later.** A `state.set`, a `store.flags` or a
`notify.post` is optimistic in the mirror and answered by the service over
the socket: the reply and the event land on the main loop afterwards, so a check that reads the outcome off disk
(`AppState().get_setting`, `state.json`), off a mirror (`center.rows()`,
a green row) or off a tab (`session-resolved` from a probed
`host.session_resolved`) pumps first: `e2e_service.settle()` (150 ms,
then until nothing is pending) or `wait_until(predicate)`. Never loosen
the assertion; wait for it. **Never `call` from the link's own I/O thread**
(`SocketLink.call` raises there): a blocking `call` is for the main thread
or a worker thread, never a callback the link's thread runs. A check that must observe a transient reads
it with nothing pumped where the code sets it in the call (the clone dialog's
box, locked inside `_start_clone`): even a bare pending-pump can land a fast
fake clone's finish first, which failed half the first attempts on a fast
runner. A verdict or anything else that arrives as a reply is waited for on
the verdict itself: a fixed wait reads the old word on a slow one.

Copy `scripts/check_new_chat.py`'s preamble rather than retyping it. The
essentials, all of which are read at import time somewhere in `collins`:

```python
E2E = tempfile.mkdtemp(prefix="collins-x-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"  # unique; 'r' since ids can't start with a digit
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"        # write "{}" to it
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"                  # NOT optional: else the app reaps the user's chats
os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"              # the service sweeps uploads by the sessions it knows; never isolate by HOME alone
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
```

Seed `config/collins/state.json` with `{"settings": {"welcome_seen": true,
"gh_welcome_dismissed": true}}`: the first-launch welcome dialog otherwise
opens over the window under test, and CI's container has no `gh`, so the
"better with the GitHub CLI" card appears a moment after launch — a dialog on
top makes `grab_focus()` inside anything below return False with every
ancestor looking healthy (libadwaita shadows the lower dialog by clearing
`can-focus` on its bin). `win.get_visible_dialog()` names what is on top.
Set `"title_model": "none"` too unless the check is about titling, or the
app spawns headless `claude` runs against your fake sessions.

Then `sys.path.insert(0, repo_root)` **before** importing `collins`, or the
system-installed copy under `/usr/lib/python3/dist-packages` wins and the
methods you just added "don't exist". Give the script its own deadline
(`GLib.timeout_add` + `os._exit`): an exception inside a GLib callback is
swallowed and the app runs forever.

Sidebar headers need staging: put a `claude` shim on `PATH` first (CI
has no real `claude`; a dev box does). **No check types a command of its
own** since PR-3.0: the service assembles every agent command from its
own `claude` (the `spawn` request has no `command_override`), so a check
or a unit test that wants a session's command to sleep or to exit stages
a `claude` that does (`check_notifications.py`, `check_root_name_links.py`;
`tests/test_core.py`'s `stage_cli`, which also answers `agents` with
none so a resume's background-agents probe does not wait). The service
looks it up on its session environment's PATH, which starts with the
check's own PATH entries. A group the sidebar
never showed starts collapsed (`AppState().set_group_expanded("proj:<name>",
True)` before `App()`), and the Favorites header exists only once something
is starred.

## The claude shim

A stub `claude` that the tab spawns and types into must:

1. `tty.setraw(0)` before reading stdin — `inject_prompt` sends text and a
   lone `\r`; cooked mode turns it into `\n` and a loop waiting for `\r` never
   ends. The real CLI raw-modes stdin too (even `claude agents --json` does,
   which is why every helper spawn in the app uses `stdin=DEVNULL`).
2. Draw the idle prompt as `❯` followed by **U+00A0**, not a space:
   `Provider.takes_prompt` keys on the no-break space. Copy the bytes from
   `check_new_chat.py`; a retyped space makes `takes_prompt()` False forever.
3. Write a transcript under `COLLINS_PROJECTS_DIR/<encoded cwd>/<uuid>.jsonl`
   so the tab's resolver binds (the directory name is the cwd with every
   non-alphanumeric character replaced by `-`; filenames must be UUID-shaped
   and the file must carry `cwd`, `timestamp` and a user message).
4. Log `' '.join(sys.argv[1:])` if the check asserts on the launch argv — and
   derive the expected string from `titles.headless_argv` / the provider
   rather than matching `-p --model X` (the empty `--tools ""` shows as two
   spaces).
5. `sleep` forever if the tab should read as busy; exit if it should not — a
   `sleep infinity` descendant keeps the process poll marking the session
   busy for as long as the tab is open.

Driving a close: a shim whose `agents` subcommand prints `[]` and otherwise
`exec sleep 300` reads busy and dies on Ctrl+C. To exercise busy==0 paths on
a tab running a real CLI, kill the foreground process group
(`os.killpg(os.tcgetpgrp(pty_fd), SIGKILL)`), not the shell.

## Headless probe traps

- `win.activate_action("name", …)` silently does nothing on a `MainWindow`:
  `Gtk.Widget.activate_action` shadows `Gio.ActionGroup`'s. Spell the group:
  `win.activate_action("win.name", variant)` (returns True when dispatched).
  A `SimpleAction` a previous context menu left disabled also no-ops.
- `widget.has_focus()` is False under a headless compositor even for the
  window's focus widget; assert `root.get_focus() is widget`. Entries report
  focus on their inner `GtkText`.
- `GLib.idle_add` at default-idle priority never runs under CI's Xvfb (the
  frame clock never idles). Landings that reset gates must be
  `PRIORITY_DEFAULT`; checks must not assert on cosmetic idle settles.
- Setting the clipboard right after `present()` under the headless GNOME
  Shell is dropped silently (Wayland needs a recent input serial). Retry
  `set_content` every ~50 ms until `clipboard.is_local()`. Under Xvfb it
  always takes, so the flake is local-only.
- To answer an `Adw.AlertDialog`: `dialog.emit("response", "<id>")` runs the
  handler but does not close it — follow with `dialog.force_close()`. An open
  Adw dialog also swallows a window's close attempt.
- `Adw.TabView` pages that were never selected are unmapped and measure 0;
  a `Gtk.Stack` stands in for one in checks (`check_panel_bg_tab_width.py`).
- A `Vte.Terminal` widget that finalizes SIGHUPs its child; holding a Python
  reference keeps the child alive with no window. Assert on a `weakref`, not
  the pid.
- To read a sidebar context menu without compositing, stub
  `SessionSidebar._popup_menu` to capture the `Gio.Menu` and walk its
  sections (`get_item_link(i, Gio.MENU_LINK_SECTION)`).
- A scratch `HOME` is never isolation on its own: with the user's
  `XDG_DATA_HOME` inherited the service reads the scratch home's "CLI
  projects directory", the orphan-uploads refusal does not fire, and the
  sweep runs against the user's real uploads root. Set
  `COLLINS_UPLOADS_DIR` (and `COLLINS_PROJECTS_DIR`) whenever `HOME` is
  redirected.
- Real-CLI probes: copy `~/.claude.json` into the scratch `HOME` so folder
  trust resolves (headless `-p` skips the trust gate and proves nothing);
  scrub `CLAUDE_*` from the environment (an inherited
  `CLAUDE_CODE_CHILD_SESSION` turns transcript saving off); a probe that posts
  `/model` rewrites the user's `~/.claude/settings.json` default — restore it.
- `with-headless-display.sh` swaps the Wayland display and leaves the
  command on the user's session bus. Synthesizing input through
  `org.gnome.Mutter.RemoteDesktop` on that bus types into the user's live
  desktop. Connect to the headless shell's own bus (read it from the
  shell's `/proc/<pid>/environ`) and refuse to run on the session bus; the
  capture-screenshots skill has the recipe. Prefer in-process synthesis
  (`feed_child`, `paste_text`, emitting the signal) when real events are
  not the point.
- Real-CLI probes that must run side by side: `scripts/spike_split_common.py`
  makes a throwaway `HOME` per probe (copies of the login and the config,
  the work directory pre-trusted), refuses when the token is close to
  expiring, and leaves the CLI with Ctrl+C Ctrl+C. `CLAUDE_CODE_NO_FLICKER`
  picks the CLI's mode (`0` classic, `1` fullscreen); unset, the account's
  server gates do, so a fixture always sets it.
- Long inline shell pipelines are refused by the worktree guard; put probes
  in a scratchpad `.py`/`.sh` and run them with `PYTHONPATH=<worktree>`.
- A check that needs only a terminal, not the app:
  `scripts/check_termstream_answers.py` builds a childless `Vte.Terminal`
  in a bare `Gtk.Window` (no `App`, no scratch tree) and compares
  `collins.service.termstream`'s answers with what VTE commits. It reads
  VTE's answers up to a sentinel (`CSI 5 n` fed last; its `CSI 0 n` is the
  last commit), so nothing waits on a timer. `get_cursor_position()` counts
  rows from the top of the buffer, not the screen: take a fresh terminal
  before comparing a cursor. It also reads the published terminal
  contract, `docs/api/terminal.md` (`--table PATH` names another copy):
  `scripts/termcontract.py` (GTK-free) parses the page's three tables in
  the page's own notation (`CSI ?61;1;21;22;28c`, a `<name>` placeholder
  per value the row explains, `\|` for a pipe), and the check holds every
  answered row to VTE (one reply, matching the row's pattern, each query
  placeholder tried with a few sample values and every OSC query with both
  terminators) and to the filter (the same reply, the query stripped),
  every silent row to no answer from either, and every pass-through row to
  the filter forwarding it untouched; and it fails when a query of its own
  table is not a row of the page. So a change to the page's bytes, or to
  the filter's, fails here, and a row that does not parse fails
  `tests/test_api_reference.py` first. The page is MIT and written from
  the spec and from measurements, never from `termstream.py`: when the two
  disagree, measure VTE before deciding which is wrong.
- `scripts/check_termscreen_parity.py` holds a real VTE to the termscreen
  goldens (`tests/fixtures/streams/*.golden.json`): rows, cursor, every
  drawn cell read one at a time through `Vte.Format.HTML` (a row-level
  HTML read folds faint into a colour only for its first run), the dim
  tail and the grammar's reads. `--write` regenerates the goldens from
  this VTE; the unit suite then holds the model to them. It converts
  VTE's buffer-relative cursor row by homing the cursor with origin mode
  off and reading where it landed. The recorded fixtures were made from
  a neutral directory in an isolated `$HOME` (the terminal-tab skill's
  "Re-recording"), so no path of the user's is in them; a test pins that.

## CI

`.github/workflows/ci.yml`: `lint`, `verify-versions` and `docs` (the
VitePress build of `docs/`, so a page that Vue can't compile — a `<word>`
outside a one-line code span — fails the PR rather than the deploy after
the merge) on the bare runner;
`test`, `e2e-shard (1)` … `(5)` (`xvfb-run … scripts/run_e2e.py
--timeout 120 --shard N/5`: the suite, every tab and panel shell on the
service's pty server over the socket of a service each check starts;
60-minute job cap each),
`packaging` and `ppa-source
(resolute)` inside the resolute CI image; `ppa-source (noble)` in the noble
packaging image; `rpm` in the Fedora image. The e2e suite runs as five
parallel shards dealt out by measured time (the `CHECK_SECONDS` table in
`scripts/run_e2e.py`; a check the table lacks is dealt in at a default
weight, so a new check needs no registration, and a stale name fails
`tests/test_run_e2e.py`). A bare `e2e` job fans the shards back in — it is
the name the main-branch ruleset requires, so it must stay. The shard jobs
`needs: image` and show up **after** the first `gh pr checks --watch` may
have exited green — keep watching until the `e2e` row is listed and
finished. A check that hangs is one that needs more than 120 s; a shard
passes in about a minute. When a leg drifts or a check changes, the
`balance-e2e-shards` skill refreshes the weights from a run's logs and
previews the deal. On a dev box the whole suite is
`python3 scripts/run_e2e.py` under the headless wrapper;
`scripts/check_attach_redraw.py` is about the tab's client terminal on the
service's pty (a second VTE attaching to a running pty is redrawn from the
service's model, scrollback and colours compared, and the redraw guard's
sentinel answer is shown never to reach the pty; `COLLINS_ATTACH_RECORD=<file>`
writes the first client's stream for a replay through the model and a VTE
outside the app). `scripts/probe_server_backend.py` (headless, no quota) is
the pty client's probe set: a flow-control redraw's sentinel never
reaching the pty, the guard's watchdog and a failed attach, a NUL commit,
two attaches back to back, the mouse coalescer under random streams.
Reproduce a shard (or, without
`--shard`, the whole suite) on any machine with Docker:

```bash
docker run --rm -it --init -v "$PWD:/src" -w /src ghcr.io/episode6/collins-ci:<tag> \
  xvfb-run -a -s "-screen 0 1920x1200x24" python3 scripts/run_e2e.py
```

(`--init` matters for the unit suite: as pid 1 a proctree test fails.) The
tag is in the image job's step summary of any recent run.

Related skills: `capture-screenshots` (the launch/staging recipe these checks
share), `collins-gtk-sharp-edges`.
