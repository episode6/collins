# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-28. Full change history: git log for this file.

"""Small human-readable formatting helpers shared across the UI."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .i18n import _


def display_path(path: str) -> str:
    """A directory path as shown in the UI: the home prefix collapsed to ~."""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def format_timestamp(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return (
            datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        )
    except ValueError:
        return ts


# Projects named in the "delete hidden sessions" confirmation: list them all
# while the list is short, and once it isn't, name only the biggest few and sum
# the rest on one line — enough to see the damage, and the same readable dialog
# whether 3 projects are affected or 300.
_BLAST_RADIUS_MAX = 7  # list every project up to this many
_BLAST_RADIUS_ROWS = 4  # named once the list is capped


def blast_radius_body(total: int, breakdown: list[tuple[str, int, int]]) -> str:
    """Spell out what "all hidden sessions" actually means before the user
    commits to it: how many transcripts, spread over which projects, and how
    many of those lose *every* session they have — every row the sidebar keeps
    out of sight, not only the ones hidden by hand. Hiding is cheap and
    accumulates, so the pile is usually far bigger than it feels, and a project
    that loses everything is gone from the sidebar unless it's kept.

    `breakdown` is store.hidden_breakdown(): (project, hidden, total) biggest
    first. A short list is named in full; a long one is cut to the biggest few
    with the rest summed on one line, so the dialog stays a readable size with
    hundreds of projects hidden.

    Which projects get named goes by session count, but the lines are then
    ordered shortest first. AdwAlertDialog centres its body, and a centred
    column of ragged lines is hard to read in any order — sorted by length it
    at least tapers evenly instead of jumping about.
    """
    lines = [
        _("{n} session(s) in {p} project(s) have their transcripts moved to "
          "the trash, where they can be restored. Sessions hidden with their "
          "whole project — and originals a backgrounded fork replaced — are "
          "included.").format(n=total, p=len(breakdown))
    ]
    if len(breakdown) <= _BLAST_RADIUS_MAX:
        shown, rest = breakdown, []
    else:
        shown, rest = breakdown[:_BLAST_RADIUS_ROWS], breakdown[_BLAST_RADIUS_ROWS:]
    named = [
        _("{project} — {n} of {total}").format(project=name, n=count, total=project_total)
        for name, count, project_total in shown
    ]
    lines.append("")
    lines += sorted(named, key=lambda line: (len(line), line))
    if rest:
        # Always last: it stands for everything the named lines left out.
        lines.append(
            _("…and {p} other project(s) — {n} session(s)").format(
                p=len(rest), n=sum(count for _name, count, _total in rest)
            )
        )
    emptied = sum(1 for _name, count, project_total in breakdown if count >= project_total)
    if emptied:
        lines.append("")
        lines.append(
            _("{p} of these project(s) lose every session they have.").format(p=emptied)
        )
    return "\n".join(lines)
