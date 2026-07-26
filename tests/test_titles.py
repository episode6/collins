import threading
import time

from claude_session_manager.titles import (
    TitleError,
    TitleGenerator,
    fallback_title,
    sanitize_title,
)


class FakeRunner:
    """Stands in for the headless `claude -p` call. Each entry in `replies`
    is either a reply string or an exception to raise."""

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Collector:
    """Callback capturing (session_id, title) pairs, with a wait helper."""

    def __init__(self) -> None:
        self.results: dict[str, str] = {}
        self._event = threading.Event()

    def __call__(self, session_id: str, title: str) -> None:
        self.results[session_id] = title
        self._event.set()

    def wait(self) -> bool:
        return self._event.wait(timeout=5)


def wait_until(predicate, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_sanitize_title():
    assert sanitize_title('  "Fix login bug."  ') == "Fix login bug"
    assert sanitize_title("Multi\nline\t title") == "Multi line title"
    assert sanitize_title("x" * 200) == "x" * 60
    assert sanitize_title("   ") == ""


def test_fallback_title():
    assert fallback_title("fix the login bug") == "fix the login bug"
    assert (
        fallback_title("one two three four five six seven eight nine ten eleven twelve")
        == "one two three four five six seven eight nine ten"
    )
    assert fallback_title('"quoted prompt."') == "quoted prompt"


def test_generates_and_delivers_title():
    runner = FakeRunner(["Fix login bug\n"])
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    generator.submit("sid-1", "please fix the login bug in auth.py")
    assert collector.wait()
    assert collector.results == {"sid-1": "Fix login bug"}
    assert "please fix the login bug in auth.py" in runner.prompts[0]


def test_duplicate_and_empty_submits_are_ignored():
    runner = FakeRunner(["Title one"])
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    generator.submit("sid-1", "   ")  # empty prompt: dropped
    generator.submit("sid-1", "do the thing")
    generator.submit("sid-1", "do the thing")  # duplicate: dropped
    assert collector.wait()
    assert len(runner.prompts) == 1
    assert collector.results == {"sid-1": "Title one"}


def test_force_resubmits_an_already_titled_session():
    runner = FakeRunner(["First title", "Second title"])
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    generator.submit("sid-1", "do the thing")
    assert collector.wait()
    generator.submit("sid-1", "do the thing")  # deduped
    generator.submit("sid-1", "do the thing", force=True)
    assert wait_until(lambda: collector.results.get("sid-1") == "Second title")
    assert len(runner.prompts) == 2


def test_fatal_error_disables_generator():
    runner = FakeRunner([TitleError("claude CLI not found on PATH", fatal=True)])
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    generator.submit("sid-1", "do the thing")
    assert wait_until(lambda: generator._disabled)
    assert collector.results == {}


def test_transient_error_skips_session_but_continues():
    runner = FakeRunner([TitleError("exit 1: transient"), "Second title"])
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    generator.submit("sid-1", "first prompt")
    generator.submit("sid-2", "second prompt")
    assert collector.wait()
    assert collector.results == {"sid-2": "Second title"}
    assert not generator._disabled


def test_consecutive_failures_disable_generator():
    runner = FakeRunner([RuntimeError("boom")] * 3)
    collector = Collector()
    generator = TitleGenerator(collector, runner=runner)
    for i in range(3):
        generator.submit(f"sid-{i}", "some prompt")
    assert wait_until(lambda: generator._disabled)
    assert collector.results == {}
