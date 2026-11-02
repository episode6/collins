"""The conformance kit (conformance/) agrees with the pages and the goldens.

The kit is MIT and standard-library only, and it tests what docs/api/ says,
so the repository holds it to the pages and to the measurements it ships:
its query table is the terminal contract's three tables (parsed by
scripts/termcontract.py from the page), its screens data is the synthetic
goldens under tests/fixtures/streams/ (what the parity script writes from a
real VTE), its copies of the recorded streams are the fixtures, every file
of it carries the MIT tag or sits under its LICENSE, it imports nothing but
the standard library (and collins/api/protocol.py by path), and it names no
Collins module but the two MIT ones (spec §3.5). The run against the service
is the `conformance` CI job (scripts/run_conformance.py), not a unit test.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "conformance")
DATA = os.path.join(KIT, "data")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "streams")

sys.path.insert(0, FIXTURES)
import scenarios  # noqa: E402

_TC_PATH = os.path.join(ROOT, "scripts", "termcontract.py")
_tc_spec = importlib.util.spec_from_file_location("termcontract", _TC_PATH)
termcontract = importlib.util.module_from_spec(_tc_spec)
sys.modules.setdefault("termcontract", termcontract)
_tc_spec.loader.exec_module(termcontract)


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _kit_files(suffixes: tuple[str, ...] = (".py", ".md")) -> list[str]:
    out = []
    for directory, dirs, files in os.walk(KIT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(suffixes):
                out.append(os.path.join(directory, name))
    return sorted(out)


def _queries() -> dict:
    with open(os.path.join(DATA, "queries.json"), encoding="utf-8") as handle:
        return json.load(handle)


# -- the query table is the page's -------------------------------------------


def _rendered(text: str, values: dict) -> bytes:
    return termcontract.render(termcontract.parse(text), values)


def test_the_kits_query_table_is_the_terminal_contracts_tables():
    tables = termcontract.tables(_read("docs", "api", "terminal.md"))
    kit = _queries()
    values = {"n": "7", "i": "3", "vte": "8400", "row": "1", "col": "1", "scheme": "1", "s": "2",
              "rows": "40", "cols": "120", "height": "720", "width": "1080", "rrrr": "ffff",
              "gggg": "ffff", "bbbb": "ffff", "sgr": "0"}  # fmt: skip
    page_answered = [
        (termcontract.render(r.query, values), termcontract.render(r.reply, values))
        for r in tables[termcontract.ANSWERED]
    ]
    kit_answered = [(_rendered(r["query"], values), _rendered(r["reply"], values)) for r in kit["answered"]]
    assert kit_answered == page_answered
    for section, key in ((termcontract.SILENT, "silent"), (termcontract.PASSED, "passed")):
        page = [termcontract.render(r.query, values) for r in tables[section]]
        mine = [_rendered(r["query"], values) for r in kit[key]]
        assert mine == page, key


def test_the_kits_notation_parser_agrees_with_the_pages():
    """The kit has a parser of its own for the notation; both read the
    table's rows to the same bytes."""
    sys.path.insert(0, ROOT)
    from conformance.kit import notation

    values = {"n": "2004", "i": "9", "vte": "8400", "row": "5", "col": "7", "scheme": "1", "s": "1",
              "rows": "40", "cols": "120", "height": "720", "width": "1080", "rrrr": "c0c0",
              "gggg": "0000", "bbbb": "ffff", "sgr": "0;1;31"}  # fmt: skip
    kit = _queries()
    for section in ("answered", "silent", "passed"):
        for row in kit[section]:
            for key in ("query", "reply"):
                if key not in row:
                    continue
                assert notation.render(notation.parse(row[key]), values) == _rendered(row[key], values), row[
                    key
                ]


# -- the screens data is the goldens -------------------------------------------


def test_the_screens_data_is_the_synthetic_goldens():
    goldens = scenarios.load_goldens(FIXTURES)
    synthetic = dict(scenarios.synthetic_scenarios())
    screens_dir = os.path.join(DATA, "screens")
    files = {
        name[: -len(".json")].replace("__", "@"): name
        for name in os.listdir(screens_dir)
        if name.endswith(".json")
    }
    assert set(files) == set(synthetic), "run scripts/check_termscreen_parity.py --write"
    for name, data in synthetic.items():
        with open(os.path.join(screens_dir, files[name]), encoding="utf-8") as handle:
            record = json.load(handle)
        golden = goldens[name]
        assert record["scenario"] == name
        assert base64.b64decode(record["stream"]) == data, name
        assert record["capture"] == golden["capture"], name
        assert record["cursor"] == golden["cursor"], name
        assert (record["cols"], record["rows"]) == (scenarios.COLS, scenarios.ROWS)
        assert isinstance(record["alternate"], bool)


def test_the_streams_data_is_the_recorded_fixtures():
    streams_dir = os.path.join(DATA, "streams")
    copied = sorted(os.listdir(streams_dir))
    expected = sorted(
        session + suffix
        for session in scenarios.recorded_sessions(FIXTURES)
        for suffix in (".bin", ".marks.json", ".golden.json")
    )
    assert copied == expected, "run scripts/check_termscreen_parity.py --write"
    for name in copied:
        with (
            open(os.path.join(streams_dir, name), "rb") as mine,
            open(os.path.join(FIXTURES, name), "rb") as theirs,
        ):
            assert mine.read() == theirs.read(), name


def test_the_vte_data_files_have_their_shape():
    with open(os.path.join(DATA, "decrqm-modes.json"), encoding="utf-8") as handle:
        modes = json.load(handle)
    assert modes["vte"].startswith("0.84")
    for section in ("private", "ansi"):
        assert all(key.isdigit() and value in (1, 2, 3, 4) for key, value in modes[section].items())
    assert modes["private"]["7"] == 1 and modes["private"]["25"] == 1 and modes["private"]["2004"] == 2
    with open(os.path.join(DATA, "vte-palette.json"), encoding="utf-8") as handle:
        palette = json.load(handle)
    assert len(palette["colours"]) == 256
    assert all(len(c) == 14 and c[4] == "/" for c in palette["colours"])


# -- the tree is MIT, standard library only, and names no Collins module ----------


def test_every_kit_file_carries_the_mit_tag_or_sits_under_its_license():
    assert "MIT License" in _read("conformance", "LICENSE")
    for path in _kit_files():
        with open(path, encoding="utf-8") as handle:
            head = handle.read(600)
        assert "SPDX-License-Identifier: MIT" in head, os.path.relpath(path, ROOT)
    for path in _kit_files((".json", ".bin")):
        assert path.startswith(DATA), os.path.relpath(path, ROOT)


def test_the_kit_imports_nothing_but_the_standard_library():
    allowed = set(sys.stdlib_module_names) | {"conformance"}
    for path in _kit_files((".py",)):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # a relative import inside the kit
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                assert name in allowed, f"{os.path.relpath(path, ROOT)} imports {name}"


def test_the_kit_names_no_collins_module_but_the_mit_two():
    import re

    allowed = {"collins/api/protocol.py", "collins/sshremote.py"}
    for path in _kit_files():
        text = open(path, encoding="utf-8").read()
        named = set(re.findall(r"collins/[A-Za-z0-9_/]+\.py", text))
        assert named <= allowed, (os.path.relpath(path, ROOT), named - allowed)
        assert "collins/service" not in text, os.path.relpath(path, ROOT)


def test_the_kit_runs_on_python_3_12():
    assert "3.12" in _read("conformance", "__main__.py")
    assert "3.12" in _read("conformance", "README.md")


def test_the_fakes_command_line_forms():
    fake = os.path.join(KIT, "fake_claude.py")
    version = subprocess.run([sys.executable, fake, "--version"], capture_output=True, text=True, timeout=30)
    assert version.returncode == 0 and "fake" in version.stdout
    agents = subprocess.run(
        [sys.executable, fake, "agents", "--json"], capture_output=True, text=True, timeout=30
    )
    assert agents.returncode == 0 and json.loads(agents.stdout) == []
    headless = subprocess.run(
        [sys.executable, fake, "-p", "hello", "--tools", ""], capture_output=True, text=True, timeout=30
    )
    assert headless.returncode == 0
    no_tty = subprocess.run(
        [sys.executable, fake], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
    )
    assert no_tty.returncode == 1


@pytest.mark.parametrize("name", ["__main__.py", "fake_claude.py", "README.md", "LICENSE"])
def test_the_kit_has_its_pieces(name):
    assert os.path.exists(os.path.join(KIT, name)), name


def test_the_kits_own_tests_pass():
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "conformance/tests", "-t", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
