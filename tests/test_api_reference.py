"""The published service API spec (docs/api/) agrees with the code.

docs/api/messages.md is generated from the message table in
collins/api/protocol.py by scripts/gen_api_reference.py; a table edit that
forgets to regenerate it fails here. The prose half, docs/api/index.md,
names every error code and capability, and both pages stay free of the
angle-bracket placeholders that break the VitePress build outside a code
span. All five MIT-licensed source files carry their SPDX tag, since the rest of
the repository is GPL and a reader of any one of them must be able to tell.
"""

import importlib.util
import os
import re

from collins.api import protocol as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "api")

_PATH = os.path.join(ROOT, "scripts", "gen_api_reference.py")
_spec = importlib.util.spec_from_file_location("gen_api_reference", _PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


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


def _outside_code(markdown: str) -> str:
    """*markdown* with its fenced blocks, code spans and HTML comments
    removed, so that what is left is what VitePress parses as markup."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def test_pages_keep_angle_brackets_in_code():
    for page in ("index.md", "messages.md"):
        prose = _outside_code(_read("docs", "api", page))
        assert not re.search(r"<[A-Za-z]", prose), page


def test_mit_files_carry_their_spdx_tag():
    for path in (
        ("docs", "api", "index.md"),
        ("docs", "api", "messages.md"),
        ("scripts", "gen_api_reference.py"),
        ("collins", "api", "protocol.py"),
        ("collins", "sshremote.py"),
    ):
        head = _read(*path)[:600]
        assert "SPDX-License-Identifier: MIT" in head, path
    assert "MIT License" in _read("docs", "api", "LICENSE")


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
                if rel in ("collins/api/protocol.py", "collins/sshremote.py", "scripts/gen_api_reference.py"):
                    continue
                with open(path, encoding="utf-8") as handle:
                    head = handle.read(600)
                assert "SPDX-License-Identifier: MIT" not in head, rel
