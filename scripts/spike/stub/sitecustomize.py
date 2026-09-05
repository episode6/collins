"""Stub `collins.proctree` to "the kernel won't say" for every query.

Loaded by the interpreter when this directory is on PYTHONPATH (Python
imports `sitecustomize` at startup). Only acts when SPIKE_STUB_PROCTREE=1,
so the same PYTHONPATH can run the unstubbed launch too. The repo root must
also be on PYTHONPATH (the workflow puts it there) so `collins` resolves to
the checkout.

Spec hardware-only item 5: what breaks visually when process inspection
returns nothing, which is what /proc-less macOS gives today's proctree
(every function already swallows OSError, so this stub only makes that
explicit and logs the fact).
"""

import os
import sys

if os.environ.get("SPIKE_STUB_PROCTREE") == "1":
    try:
        from collins import proctree
    except Exception as exc:  # noqa: BLE001
        print(f"sitecustomize: could not import collins.proctree: {exc!r}", file=sys.stderr)
    else:
        proctree.process_cwd = lambda pid: None
        proctree.process_children = lambda pid: []
        proctree.process_ppid = lambda pid: None
        proctree.ancestor_pids = lambda pid, limit=32: set()
        proctree.is_agent_process = lambda pid, cli: False
        proctree.process_cmdline = lambda pid: None
        proctree.agent_descendant_cwd = lambda pid, cli, depth=8: None
        proctree.agent_descendant_pid = lambda pid, cli, depth=8: None
        proctree.descendant_cmdlines = lambda pid, cli: set()
        proctree.has_live_descendant = lambda pid, cli, ignore=frozenset(): False
        print("sitecustomize: collins.proctree stubbed", file=sys.stderr)
