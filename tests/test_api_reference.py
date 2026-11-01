"""The published service API spec (docs/api/) agrees with the code.

docs/api/messages.md is generated from the message table in
collins/api/protocol.py by scripts/gen_api_reference.py; a table edit that
forgets to regenerate it fails here. The prose half, docs/api/index.md,
names every error code and capability, and both pages stay free of the
angle-bracket placeholders that break the VitePress build outside a code
span. Every MIT-licensed file carries its SPDX tag (the terminal stream
fixtures' data files, which cannot, are covered by the LICENSE file beside
them), since the rest of the repository is GPL and a reader of any one of
them must be able to tell; the list here, docs/api/LICENSE and
debian/copyright move together. The terminal contract's tables parse in
the page's own notation, so scripts/check_termstream_answers.py can hold
every row to VTE.
"""

import ast
import importlib.util
import inspect
import os
import re
import sys

from collins import gitloads, gitmodel, gitops, mcptools, state
from collins.api import protocol as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "api")

_PATH = os.path.join(ROOT, "scripts", "gen_api_reference.py")
_spec = importlib.util.spec_from_file_location("gen_api_reference", _PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_TC_PATH = os.path.join(ROOT, "scripts", "termcontract.py")
_tc_spec = importlib.util.spec_from_file_location("termcontract", _TC_PATH)
termcontract = importlib.util.module_from_spec(_tc_spec)
sys.modules.setdefault("termcontract", termcontract)  # its dataclasses look the module up
_tc_spec.loader.exec_module(termcontract)

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "streams")
# The MIT files, as docs/api/LICENSE and debian/copyright list them: the
# three pages, the generator, the two modules, and every file under the
# stream fixtures (scenarios.py with a tag of its own, the data files under
# the directory's LICENSE).
MIT_SOURCES = (
    ("docs", "api", "index.md"),
    ("docs", "api", "messages.md"),
    ("docs", "api", "terminal.md"),
    ("scripts", "gen_api_reference.py"),
    ("collins", "api", "protocol.py"),
    ("collins", "sshremote.py"),
    ("tests", "fixtures", "streams", "scenarios.py"),
)
MIT_PYTHON = {"collins/api/protocol.py", "collins/sshremote.py", "scripts/gen_api_reference.py",
              "tests/fixtures/streams/scenarios.py"}  # fmt: skip


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_message_reference_is_current():
    expected = gen.render(os.path.join(ROOT, gen.SOURCE))
    assert _read(gen.OUTPUT) == expected, (
        "docs/api/messages.md is stale: run python3 scripts/gen_api_reference.py"
    )


def test_reference_lists_every_type_once():
    text = _read("docs", "api", "messages.md")
    for name in p.type_names():
        assert text.count(f"### `{name}`\n") == 1, name


def test_reference_sections_cover_the_table():
    listed = [name for _, names in gen.sections(os.path.join(ROOT, gen.SOURCE)) for name in names]
    assert sorted(listed) == sorted(p.type_names())
    assert len(listed) == len(set(listed))


def test_index_names_every_error_code_and_capability():
    text = _read("docs", "api", "index.md")
    for code in p.ERRORS:
        assert f"`{code}`" in text, code
    for cap in p.CAPABILITIES:
        assert f"`{cap}`" in text, cap


def test_the_reference_says_who_serves_each_type():
    text = _read("docs", "api", "messages.md")
    assert text.index("## Conformance\n") < text.index("## Message types\n")
    for entry in p.TYPES.values():
        section = text.split(f"### `{entry.name}`\n", 1)[1]
        assert section.lstrip().split("\n\n", 2)[1] == f"Family: `{entry.family}` · Level {entry.level}"
    for level in p.CONFORMANCE_LEVELS:
        assert f"### Level {level}: " in text, level
    for family in p.CONFORMANCE_FAMILIES:
        assert f"### Family `{family}`: " in text, family
    for name, _meaning in gen.MATCHED_MSGIDS:
        assert f"`{getattr(p, name)}`" in text, name
    section = text.split("## Conformance\n", 1)[1].split("## Message types\n", 1)[0]
    for key in p.STATE_KEYS:
        assert f"`{key}`" in section, key
    for name in p.TYPES["service.status"].request.reply:
        assert f"`{name}`" in section, name


def test_the_prose_has_a_section_for_a_server_and_reads_the_license_for_one():
    text = _read("docs", "api", "index.md")
    section = text.split("## Implementing a server\n", 1)[1].split("\n## ", 1)[0]
    for name in (*p.CONFORMANCE_FAMILIES.keys() - {p.CAP_DEBUG}, "core", "unknown", "implementation"):
        assert f"`{name}`" in section, name
    interoperability = " ".join(text.split("## Interoperability\n", 1)[1].split("\n## ", 1)[0].split())
    assert "implements the *service's* side of this API" in interoperability
    assert "a derivative of Collins" in interoperability


def test_the_prose_keeps_a_server_author_out_of_the_services_code():
    """The worked examples are the client's, and both sections say what a
    server author reads."""
    text = _read("docs", "api", "index.md")
    clean_room = (
        "A server author who wants the [Interoperability](#interoperability) section's reading writes "
        "from the MIT pages alone and reads neither `collins/api/server.py` nor `collins/service/`."
    )
    for heading in ("## Writing a client\n", "## Implementing a server\n"):
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        assert clean_room in " ".join(section.split()), heading
    examples = text.split("## Writing a client\n", 1)[1].split("A server author", 1)[0]
    assert "collins/api/server.py" not in examples
    for module in ("collins/api/client.py", "collins/connection.py", "collins/sshforward.py"):
        assert module in examples, module


def test_the_git_builders_table_is_the_registry():
    assert set(p.GIT_BUILDERS) == set(gitops.BUILDERS)
    for name, entry in gitops.BUILDERS.items():
        documented = {arg.split(":")[0]: arg for arg in p.GIT_BUILDERS[name].args}
        wanted = {
            param.name + ("" if param.default is inspect.Parameter.empty else "?")
            for param in inspect.signature(entry.build).parameters.values()
        }
        assert set(documented) == wanted, name


def test_the_git_builders_table_prints_the_formats_a_client_parses():
    assert gitmodel.LOG_FORMAT in p.GIT_BUILDERS["log_argv"].runs
    assert gitloads.COMMIT_FORMAT in p.GIT_BUILDERS["commit_message_argv"].runs
    assert ", ".join(f"`{kind}`" for kind in gitops.OPERATION_KINDS) in p.GIT_BUILDERS["continue_argv"].runs
    assert all(f"`{side}`" in p.GIT_BUILDERS["checkout_side_argv"].runs for side in gitops.RESOLVE_SIDES)


def test_the_state_keys_table_is_what_the_service_serves():
    assert set(p.STATE_KEYS) == set(state.SHARED_KEYS)
    for name, key in state.SHARED_KEYS.items():
        assert p.STATE_KEYS[name].form == key.form, name
        assert p.STATE_KEYS[name].writable == key.writable, name


def test_the_settings_table_is_the_services_settings():
    prefixes = ("mcp_tool_", "sandbox_tool_")
    tools = {f"{prefix}{tool}" for tool in p.SESSION_TOOLS for prefix in prefixes}
    assert set(p.SERVICE_SETTINGS) | tools == set(state.SERVICE_SETTINGS)
    assert not set(p.SERVICE_SETTINGS) & tools
    assert set(p.SESSION_TOOLS) == set(mcptools.tool_names())
    json_types = {bool: "boolean", int: "integer", str: "string"}
    for name, setting in p.SERVICE_SETTINGS.items():
        assert setting.kind == json_types[type(state.DEFAULT_SETTINGS[name])], name


def _client_sources():
    """The text of every module of the client: all of collins but the
    service, the API package's table and its server."""
    root = os.path.join(ROOT, "collins")
    for directory, _dirs, files in os.walk(root):
        if os.path.relpath(directory, root).split(os.sep)[0] == "service":
            continue
        for name in files:
            path = os.path.join(directory, name)
            excluded = ("api/protocol.py", "api/server.py")
            if name.endswith(".py") and os.path.relpath(path, root) not in excluded:
                with open(path, encoding="utf-8") as handle:
                    yield handle.read()


def test_every_optional_type_is_a_type():
    assert set(p.OPTIONAL_TYPES) <= set(p.TYPES)


def test_what_the_reference_says_no_client_sends_no_client_sends():
    """A type marked as sent by no client: no client module builds it. Should
    one start to, the reference has to stop saying so."""
    sources = list(_client_sources())
    for name in ("finish.witness", "baseline.absorb", "baseline.cmdlines", "sandbox.forget"):
        assert p.OPTIONAL_TYPES[name].startswith("No Collins client sends")
        assert not any(f'"{name}"' in text for text in sources), name
    # These two keep a method on the client's session mirror that nothing calls.
    for name, method in (
        ("transcript.set", ".set_transcript_path("),
        ("transcript.relocate", ".relocate_transcript("),
    ):
        assert p.OPTIONAL_TYPES[name].startswith("No Collins client sends")
        callers = [text for text in sources if method in text]
        assert all("def relocate_transcript" in text for text in callers), name


def test_the_walkthrough_compiles_and_names_real_messages():
    """The minimal client in docs/api/index.md: valid Python, and every
    message it sends one the table validates."""
    text = _read("docs", "api", "index.md")
    section = text.split("## A minimal client", 1)[1]
    code = section.split("```python\n", 1)[1].split("```", 1)[0]
    tree = ast.parse(code)
    sent = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [key.value for key in node.keys if isinstance(key, ast.Constant)]
            if "t" in keys:
                sent.append(node.values[keys.index("t")].value)
    assert {"hello", "subscribe", "spawn", "attach", "ack", "prompt"} <= set(sent)
    assert set(sent) <= set(p.TYPES)
    assert not set(sent) & set(p.OPTIONAL_TYPES)


def _outside_code(markdown: str) -> str:
    """*markdown* with its fenced blocks, code spans and HTML comments
    removed, so that what is left is what VitePress parses as markup."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def test_pages_keep_angle_brackets_in_code():
    for page in ("index.md", "messages.md", "terminal.md"):
        prose = _outside_code(_read("docs", "api", page))
        assert not re.search(r"<[A-Za-z]", prose), page


def test_mit_files_carry_their_spdx_tag():
    for path in MIT_SOURCES:
        head = _read(*path)[:600]
        assert "SPDX-License-Identifier: MIT" in head, path
    assert "MIT License" in _read("docs", "api", "LICENSE")
    license_text = _read("docs", "api", "LICENSE")
    for name in ("terminal.md", "tests/fixtures/streams/", "collins/sshremote.py"):
        assert name in license_text, name
    stanza = _read("debian", "copyright").split("Files: collins/api/protocol.py", 1)[1].split("\n\n", 1)[0]
    for name in ("docs/api/*", "scripts/gen_api_reference.py", "tests/fixtures/streams/*", "License: Expat"):
        assert name in stanza, name


def test_the_stream_fixtures_are_mit_as_a_directory():
    """Every file under tests/fixtures/streams/ is MIT (§8 (2) and (3) of
    the server-side spec): the one Python file by its tag, the data files
    by the directory's LICENSE, and nothing else is there, so a new kind of
    file lands under a licence somebody chose."""
    names = sorted(name for name in os.listdir(FIXTURES) if os.path.isfile(os.path.join(FIXTURES, name)))
    assert "LICENSE" in names and "scenarios.py" in names
    assert "MIT License" in _read("tests", "fixtures", "streams", "LICENSE")
    for name in names:
        if name in ("LICENSE", "scenarios.py"):
            continue
        assert name.endswith((".bin", ".marks.json", ".golden.json")), name
    recorded = {name[: -len(".bin")] for name in names if name.endswith(".bin")}
    for session in recorded:
        assert f"{session}.marks.json" in names and f"{session}.golden.json" in names, session


def test_no_other_source_file_claims_mit():
    """The MIT grant is exactly the files the spec names: a new module
    under collins/api/ is GPL unless this test and docs/api/LICENSE say
    otherwise."""
    for directory in ("collins", "scripts", "tests"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, directory)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, ROOT)
                if rel in MIT_PYTHON:
                    continue
                with open(path, encoding="utf-8") as handle:
                    head = handle.read(600)
                assert "SPDX-License-Identifier: MIT" not in head, rel


def test_the_terminal_page_names_no_collins_module_but_two_and_links_relatively():
    """§3.5: a move of the MIT pages is a copy, so the terminal contract
    names no module of Collins but protocol.py and sshremote.py, and every
    link in it is relative. And it quotes nothing of the service: not even
    its modules' names."""
    text = _read("docs", "api", "terminal.md")
    for match in re.finditer(r"collins/[A-Za-z0-9_./]+", text):
        assert match.group(0) in ("collins/api/protocol.py", "collins/sshremote.py"), match.group(0)
    assert "termstream" not in text and "termscreen" not in text and "ptyserver" not in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        assert target.startswith(("./", "#")), target
    assert "SPDX-License-Identifier: MIT" in text[:200]


def test_the_terminal_page_tables_parse_in_their_notation():
    """The three tables of docs/api/terminal.md parse with
    scripts/termcontract.py: every query and reply is a row of tokens, every
    placeholder is one the check knows a class for, and the answered table
    holds the queries the contract promises. The answers check (e2e) then
    holds each row to VTE; this is the cheap half that fails first."""
    tables = termcontract.tables(_read("docs", "api", "terminal.md"))
    answered = tables[termcontract.ANSWERED]
    queries = {termcontract.render(row.query, {"n": "7", "i": "3"}) for row in answered}
    for promised in (b"\x1b[c", b"\x1b[>c", b"\x1b[=c", b"\x1b[>q", b"\x1b[5n", b"\x1b[6n", b"\x1b[?6n",
                     b"\x1b[?996n", b"\x1b[?7$p", b"\x1b[7$p", b"\x1b[18t", b"\x1b[14t", b"\x1b[21t",
                     b"\x1b]10;?\x1b\\", b"\x1b]11;?\x1b\\", b"\x1b]12;?\x1b\\", b"\x1b]4;3;?\x1b\\",
                     b"\x1bP+q544e\x1b\\", b"\x1bP$qm\x1b\\"):  # fmt: skip
        assert promised in queries, promised
    for row in answered:
        assert row.reply is not None
        for name in termcontract.placeholders(row.reply):
            assert name in termcontract.CLASSES, name
        # A query's placeholder is answered back: the reply names it too.
        assert set(termcontract.placeholders(row.query)) <= set(termcontract.placeholders(row.reply))
    silent = {termcontract.render(row.query, {}) for row in tables[termcontract.SILENT]}
    assert {b"\x1b[?u", b"\x1b[16t", b"\x1b[?4m", b"\x05"} <= silent
    passed = {termcontract.render(row.query, {}) for row in tables[termcontract.PASSED]}
    assert b"\x1b]9;4;1;50\x1b\\" in passed
    assert not (passed & queries) and not (passed & silent) and not (silent & queries)


def test_the_notation_is_exact():
    parse = termcontract.parse
    assert termcontract.render(parse("CSI ?61;1;21;22;28c"), {}) == b"\x1b[?61;1;21;22;28c"
    assert termcontract.render(parse("DCS !|7E565445 ST"), {}) == b"\x1bP!|7E565445\x1b\\"
    assert termcontract.render(parse("OSC 4 ; <i> ; ? ST"), {"i": "9"}) == b"\x1b]4;9;?\x1b\\"
    assert termcontract.render(parse("CSI > c"), {}) == b"\x1b[>c"
    assert termcontract.render(parse("DCS $q SP q ST"), {}) == b"\x1bP$q q\x1b\\"
    pattern = termcontract.pattern(parse("CSI ?<n>;<s>$y"), {"n": "2004"})
    assert pattern.fullmatch(b"\x1b[?2004;2$y") and not pattern.fullmatch(b"\x1b[?2005;2$y")
    assert termcontract.placeholders(parse("OSC 10;rgb:<rrrr>/<gggg>/<bbbb> ST")) == ["rrrr", "gggg", "bbbb"]
    for bad in ("CSI <nope> c", "CSI \x07", "", "OSC ü"):
        try:
            parse(bad)
        except termcontract.NotationError:
            continue
        raise AssertionError(bad)
    try:
        termcontract.render(parse("CSI <n> $ p"), {})
    except termcontract.NotationError:
        pass
    else:
        raise AssertionError("a placeholder with no value rendered")
    cells = termcontract._cells("| `DCS !\\|7E565445 ST` | a \\| b | `x` |")
    assert cells == ("`DCS !|7E565445 ST`", "a | b", "`x`")
