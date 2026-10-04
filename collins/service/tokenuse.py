# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""Token use, served on the service's machine (split-service spec §3.15,
"Token use", PR-1.11).

Everything Claude-shaped runs on the CLI's own login, and the login is the
service machine's `~/.claude/.credentials.json`. Titles were the service
store's already (`store.regenerate-name`, the store's TitleGenerator);
this module answers the rest of what a client asks: the usage panel's
snapshot (`usage.get`: `usage.fetch_snapshot`, its failure as the reply's
`kind` and `error`, the panel's own vocabulary), the model catalog
(`models.get`: `claudemodels`' cache, the fetch behind it when asked, its
failure flag), the CLI's default model and effort for a directory
(`models.defaults`: the CLI's settings files on this machine), and the
write of a generated icon (`icon.save`). The icon's generation and the
login repair run long and are jobs (`service.jobs`). Rule 6 holds
unchanged: every headless run (a title, an icon, a repair) still goes
through `titles.headless_argv` from the scratch dir, in the modules this
calls.

The requests are made from a client's worker thread (a fetch blocks for
up to the network's timeout), so on the loopback these handlers run on
that thread; each touches only its module, whose caches are locked.
GTK-free.
"""

from __future__ import annotations

import logging

from .. import claudemodels, icongen, usage
from ..api import protocol

log = logging.getLogger(__name__)


def usage_get(message: protocol.Message) -> dict:
    try:
        snapshot = usage.fetch_snapshot()
    except usage.UsageError as error:
        return protocol.reply(
            message.id, kind=str(error.kind)[: protocol.SHORT_MAX], error=str(error)[: protocol.ARG_TEXT_MAX]
        )
    except Exception as error:  # never let a surprise kill the panel
        return protocol.reply(message.id, kind="http", error=str(error)[: protocol.ARG_TEXT_MAX])
    return protocol.reply(message.id, snapshot=usage.snapshot_record(snapshot))


def models_get(message: protocol.Message) -> dict:
    if message.get("refresh"):
        models = claudemodels.refresh_models()
    elif message.get("fetch"):
        models = claudemodels.available_models()
    else:
        models = claudemodels.cached_models()
    fields: dict = {
        "cached": models is not None,
        "models": claudemodels.model_records(models or [])[:256],
        "fetched_at": float(claudemodels.cache_fetched_at()),
        "failed": bool(claudemodels.cache_failed()),
    }
    return protocol.reply(message.id, **fields)


def models_defaults(message: protocol.Message) -> dict:
    cwd = message.get("cwd")
    model = claudemodels.cli_default_model(cwd)
    effort = claudemodels.cli_default_effort(cwd, message.get("model") or None)
    fields: dict = {}
    if model:
        fields["model"] = model[: protocol.MODEL_MAX]
    if effort:
        fields["effort"] = effort[: protocol.SHORT_MAX]
    return protocol.reply(message.id, **fields)


def icon_save(message: protocol.Message) -> dict:
    # The generated-icon gate again, on this side (rule 5): what is written
    # into a project is an SVG the gate passes, whoever sent it.
    svg = icongen.extract_svg(message.get("svg"))
    if svg is None:
        return protocol.refuse(message.id, protocol.ERROR_REFUSED, "That is not an icon Collins can save")
    try:
        path = icongen.save_icon(message.get("cwd"), svg)
    except OSError as error:
        return protocol.refuse(message.id, protocol.ERROR_FAILED, "{error}", {"error": str(error)})
    return protocol.reply(message.id, path=str(path))
