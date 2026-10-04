# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""A client's end of the service's jobs (`service.jobs`, PR-1.11).

`start(kind, args, on_event)` sends `job.start` and hands every `job`
event of that job to *on_event* as a `JobEvent`: its state, its progress
or failure already translated (`text`, §3.14: `translate`), its result. A
refused start (no link, a kind the service does not serve) reaches
*on_event* as one final ``refused`` event, so a dialog has one path for
every ending. `cancel(job)` sends `job.cancel`. (`translate` is
`i18n.translate`, re-exported for the dialogs.)

Events can land before the reply that names the job (an inline test
runner; a socket's ordering): they are held under the id and handed over
the moment `start` learns it. One `Jobs` per link, made on first use
(`jobs_for`). GTK-free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from . import apilink
from .api import protocol
from .api.loopback import RequestRefused
from .i18n import _, translate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobEvent:
    job: str
    kind: str
    state: str
    text: str = ""
    result: dict = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.state != protocol.JOB_RUNNING

    @property
    def ok(self) -> bool:
        return self.state == protocol.JOB_DONE


class Jobs:
    """The jobs started over one link."""

    def __init__(self, link) -> None:
        self._link = link
        self._watchers: dict[str, Callable[[JobEvent], None]] = {}
        self._early: dict[str, list[dict]] = {}
        link.on("job", self._on_job)

    def start(self, kind: str, args: dict, on_event: Callable[[JobEvent], None]) -> str | None:
        try:
            reply = self._link.call({"t": "job.start", "kind": kind, "args": dict(args)})
        except RequestRefused as refusal:
            on_event(JobEvent("", kind, protocol.JOB_REFUSED, translate(refusal.msgid, refusal.details)))
            return None
        job_id = reply["job"]
        self._watchers[job_id] = on_event
        for event in self._early.pop(job_id, []):
            self._hand(job_id, event)
        return job_id

    def cancel(self, job_id: str | None) -> None:
        if not job_id or job_id not in self._watchers:
            return
        try:
            self._link.call({"t": "job.cancel", "job": job_id})
        except RequestRefused:
            pass  # already over: its last event is on its way

    def _on_job(self, event: dict) -> None:
        job_id = event.get("job", "")
        if job_id not in self._watchers:
            self._early.setdefault(job_id, []).append(event)
            return
        self._hand(job_id, event)

    def _hand(self, job_id: str, event: dict) -> None:
        state = event.get("state", "")
        watcher = self._watchers.get(job_id)
        if state != protocol.JOB_RUNNING:
            self._watchers.pop(job_id, None)
        if watcher is None:
            return
        result = event.get("result")
        try:
            watcher(
                JobEvent(
                    job_id,
                    event.get("kind", ""),
                    state,
                    translate(event.get("msgid", ""), event.get("args")),
                    dict(result) if isinstance(result, dict) else {},
                )
            )
        except Exception:
            log.exception("jobs: a %s watcher failed", event.get("kind"))


_by_link: dict[int, Jobs] = {}


def jobs_for(link) -> Jobs:
    jobs = _by_link.get(id(link))
    if jobs is None or jobs._link is not link:
        jobs = _by_link[id(link)] = Jobs(link)
    return jobs


def start(kind: str, args: dict, on_event: Callable[[JobEvent], None]) -> str | None:
    """A job on the app's link (`apilink.current`)."""
    link = apilink.current()
    if link is None:
        on_event(JobEvent("", kind, protocol.JOB_REFUSED, _("Not connected to the service")))
        return None
    return jobs_for(link).start(kind, args, on_event)


def cancel(job_id: str | None) -> None:
    link = apilink.current()
    if link is not None and job_id:
        jobs_for(link).cancel(job_id)
