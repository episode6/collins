"""Tests for prattach — reading a session's first prompt for PR URLs (and
only URLs), resolving them through gh, and resolve()'s drop-or-attach policy
per reference form."""

import json
import threading

from collins import prattach
from collins.prattach import PromptAttacher, resolve
from collins.prstatus import PullRequest
from collins.titles import PRReference

_REPO = "episode6/collins"


def _url(number: int) -> str:
    return f"https://github.com/{_REPO}/pull/{number}"


def _pr(number: int) -> PullRequest:
    return PullRequest(number=number, url=_url(number), repository=_REPO)


def _transcript(tmp_path, prompt: str, name: str = "s.jsonl"):
    path = tmp_path / name
    path.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n",
        encoding="utf-8",
    )
    return path


def _resolver_by_number(looked: list | None = None):
    """A resolver that answers every reference from its label's number, as if
    every bare number lived in _REPO."""

    def resolver(ref: PRReference, cwd: str | None) -> PullRequest | None:
        if looked is not None:
            looked.append((ref.label, cwd))
        return _pr(int(ref.label.rsplit("#", 1)[-1]))

    return resolver


# -- the read: parse, resolve, dedup ------------------------------------------


def test_find_attaches_every_linked_pr(tmp_path):
    looked: list = []
    attacher = PromptAttacher(callback=lambda *_: None, resolver=_resolver_by_number(looked))
    path = _transcript(tmp_path, f"review {_url(200)} then rebase {_url(34)} onto it")
    prs = attacher._find(path, "/proj")
    assert [pr.number for pr in prs] == [200, 34]  # prompt order
    assert looked == [(f"{_REPO}#200", "/proj"), (f"{_REPO}#34", "/proj")]


def test_find_ignores_numbers_and_slugs(tmp_path):
    # "PR 1" and "PR 2" are the PRs this session is about to open; the
    # repository's real #1 and #2 are somebody else's, and gh would vouch for
    # them all the same (the sessions that prompted the rule: "open PR 0 of
    # the port", "PR 1 (base: main), PR 2 (base: PR 1's branch)").
    looked: list = []
    attacher = PromptAttacher(callback=lambda *_: None, resolver=_resolver_by_number(looked))
    prompt = f"open PR 1 (base: main), then PR 2 stacked on {_REPO}#1, then PR #3"
    assert attacher._find(_transcript(tmp_path, prompt), "/proj") == []
    assert looked == []
    prs = attacher._find(_transcript(tmp_path, f"merge PR 12 via {_url(200)}", "b.jsonl"), "/proj")
    assert [pr.number for pr in prs] == [200]
    assert looked == [(f"{_REPO}#200", "/proj")]


def test_find_dedupes_on_the_resolved_url(tmp_path):
    # Two spellings of one page meet on gh's canonical URL.
    def resolver(ref: PRReference, cwd: str | None) -> PullRequest | None:
        return _pr(12)

    attacher = PromptAttacher(callback=lambda *_: None, resolver=resolver)
    prompt = f"land {_url(12)}, i.e. https://www.github.com/{_REPO}/pull/12"
    prs = attacher._find(_transcript(tmp_path, prompt), "/proj")
    assert [pr.number for pr in prs] == [12]


def test_find_drops_what_the_resolver_wont_vouch_for(tmp_path):
    def resolver(ref: PRReference, cwd: str | None) -> PullRequest | None:
        return _pr(200) if ref.label == f"{_REPO}#200" else None

    attacher = PromptAttacher(callback=lambda *_: None, resolver=resolver)
    prs = attacher._find(_transcript(tmp_path, f"close {_url(12)} via {_url(200)}"), "/proj")
    assert [pr.number for pr in prs] == [200]


def test_find_nothing_without_a_prompt_or_a_mention(tmp_path):
    def resolver(ref: PRReference, cwd: str | None) -> PullRequest | None:
        raise AssertionError(f"unexpected lookup: {ref.label}")

    attacher = PromptAttacher(callback=lambda *_: None, resolver=resolver)
    assert attacher._find(_transcript(tmp_path, "fix the login bug"), "/proj") == []
    assert attacher._find(tmp_path / "missing.jsonl", "/proj") == []


# -- the worker: dedup, skip, delivery ----------------------------------------


def test_submit_delivers_once_per_session(tmp_path):
    delivered: list = []
    done = threading.Event()

    def callback(session_id: str, prs: list) -> None:
        delivered.append((session_id, [pr.number for pr in prs]))
        done.set()

    attacher = PromptAttacher(callback=callback, resolver=_resolver_by_number())
    path = _transcript(tmp_path, f"review {_url(12)}")
    attacher.submit("abc", path, "/proj")
    attacher.submit("abc", path, "/proj")  # every later refresh offers it again
    assert done.wait(5)
    attacher.submit("abc", path, "/proj")
    assert delivered == [("abc", [12])]


def test_skipped_sessions_are_never_read(tmp_path):
    delivered: list = []
    done = threading.Event()

    def callback(session_id: str, prs: list) -> None:
        delivered.append(session_id)
        done.set()

    attacher = PromptAttacher(callback=callback, resolver=_resolver_by_number())
    attacher.skip(["backlog"])
    attacher.submit("backlog", _transcript(tmp_path, f"review {_url(12)}"), "/proj")
    attacher.submit("fresh", _transcript(tmp_path, f"review {_url(34)}"), "/proj")
    assert done.wait(5)
    assert delivered == ["fresh"]


def test_a_promptless_read_delivers_nothing(tmp_path):
    delivered: list = []
    done = threading.Event()

    def callback(session_id: str, prs: list) -> None:
        delivered.append(session_id)
        if session_id == "loud":
            done.set()

    attacher = PromptAttacher(callback=callback, resolver=_resolver_by_number())
    attacher.submit("quiet", _transcript(tmp_path, "no references here"), "/proj")
    # Sequence with a session that does deliver: the worker is serial, so
    # "loud" arriving proves "quiet" was read and produced nothing.
    attacher.submit("loud", _transcript(tmp_path, f"see {_url(12)}", "second.jsonl"), "/proj")
    assert done.wait(5)
    assert delivered == ["loud"]


# -- resolve: the drop-or-attach policy per form ------------------------------


def _serve_lookup(monkeypatch, replies: dict | None):
    """Stub prstatus.lookup_pr; returns the calls it got. *replies* maps the
    first argv entry to a PullRequest, None answering everything."""
    calls: list = []

    def fake(args, cwd=None):
        calls.append((tuple(args), cwd))
        return (replies or {}).get(args[0])

    monkeypatch.setattr(prattach.prstatus, "lookup_pr", fake)
    return calls


def test_resolve_prefers_ghs_answer(monkeypatch):
    calls = _serve_lookup(monkeypatch, {"12": _pr(12)})
    ref = PRReference(label=f"{_REPO}#12", args=("12", "--repo", _REPO))
    assert resolve(ref, None).number == 12
    assert calls == [(("12", "--repo", _REPO), None)]


def test_resolve_runs_a_bare_number_in_its_directory(monkeypatch):
    calls = _serve_lookup(monkeypatch, {"12": _pr(12)})
    ref = PRReference(label="#12", args=("12",), needs_cwd=True)
    assert resolve(ref, "/proj").number == 12
    assert calls == [(("12",), "/proj")]


def test_resolve_drops_a_bare_number_with_nowhere_to_ask(monkeypatch):
    calls = _serve_lookup(monkeypatch, {"12": _pr(12)})
    ref = PRReference(label="#12", args=("12",), needs_cwd=True)
    assert resolve(ref, None) is None
    assert calls == []  # no repository to be a number in — nothing to ask


def test_resolve_drops_an_unanswered_slug(monkeypatch):
    _serve_lookup(monkeypatch, None)
    ref = PRReference(label=f"{_REPO}#12", args=("12", "--repo", _REPO))
    assert resolve(ref, "/proj") is None  # as likely an issue as a PR


def test_resolve_attaches_an_unanswered_url_bare(monkeypatch):
    # A /pull/ page can't be anything but a PR; offline gh mustn't lose it.
    _serve_lookup(monkeypatch, None)
    ref = PRReference(label=f"{_REPO}#12", args=(_url(12),))
    pr = resolve(ref, "/proj")
    assert (pr.number, pr.url, pr.repository) == (12, _url(12), _REPO)
    assert pr.title is None  # the next status fetch fills it in
