# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""The protocol module, loaded by path.

The kit vendors nothing: it imports `collins/api/protocol.py`, the MIT
module that defines the message table, from the checkout it sits in
(`../collins/api/protocol.py` next to `conformance/`), or else from a copy
placed beside the kit as `conformance/protocol.py`. `CONFORMANCE_PROTOCOL`
in the environment names another copy. Nothing else of Collins is imported.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # conformance/

CANDIDATES = (
    os.environ.get("CONFORMANCE_PROTOCOL", ""),
    os.path.join(HERE, "protocol.py"),
    os.path.join(os.path.dirname(HERE), "collins", "api", "protocol.py"),
)

_loaded: types.ModuleType | None = None


def protocol_path() -> str:
    for candidate in CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "collins/api/protocol.py not found: run the kit from a Collins checkout, put a copy of the "
        "module beside it as conformance/protocol.py, or set CONFORMANCE_PROTOCOL to one"
    )


def load() -> types.ModuleType:
    """The protocol module (once)."""
    global _loaded
    if _loaded is None:
        path = protocol_path()
        spec = importlib.util.spec_from_file_location("conformance_protocol", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("conformance_protocol", module)
        spec.loader.exec_module(module)
        _loaded = module
    return _loaded
