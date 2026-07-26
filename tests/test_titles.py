import threading
import time

from claude_session_manager.titles import TITLE_MODEL, TitleGenerator, sanitize_title


class FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, text: str) -> None:
        self.content = [FakeBlock(text)]


class FakeMessages:
    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class FakeClient:
    def __init__(self, replies: list) -> None:
        self.messages = FakeMessages(replies)


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


def test_sanitize_title():
    assert sanitize_title('  "Fix login bug."  ') == "Fix login bug"
    assert sanitize_title("Multi\nline\t title") == "Multi line title"
    assert sanitize_title("x" * 200) == "x" * 60
    assert sanitize_title("   ") == ""


def test_generates_and_delivers_title():
    client = FakeClient(["Fix login bug"])
    collector = Collector()
    generator = TitleGenerator(collector, client_factory=lambda: client)
    generator.submit("sid-1", "please fix the login bug in auth.py")
    assert collector.wait()
    assert collector.results == {"sid-1": "Fix login bug"}
    call = client.messages.calls[0]
    assert call["model"] == TITLE_MODEL
    assert call["messages"] == [{"role": "user", "content": "please fix the login bug in auth.py"}]


def test_duplicate_and_empty_submits_are_ignored():
    client = FakeClient(["Title one"])
    collector = Collector()
    generator = TitleGenerator(collector, client_factory=lambda: client)
    generator.submit("sid-1", "   ")  # empty prompt: dropped
    generator.submit("sid-1", "do the thing")
    generator.submit("sid-1", "do the thing")  # duplicate: dropped
    assert collector.wait()
    assert client.messages.calls == client.messages.calls[:1]
    assert collector.results == {"sid-1": "Title one"}


def test_client_factory_failure_disables_generator():
    def broken_factory():
        raise RuntimeError("no credentials")

    collector = Collector()
    generator = TitleGenerator(collector, client_factory=broken_factory)
    generator.submit("sid-1", "do the thing")
    for _ in range(50):  # wait for the worker to hit the factory
        if generator._disabled:
            break
        time.sleep(0.1)
    assert generator._disabled
    assert collector.results == {}


def test_request_error_skips_session_but_continues():
    client = FakeClient([RuntimeError("boom"), "Second title"])
    collector = Collector()
    generator = TitleGenerator(collector, client_factory=lambda: client)
    generator.submit("sid-1", "first prompt")
    generator.submit("sid-2", "second prompt")
    assert collector.wait()
    assert collector.results == {"sid-2": "Second title"}
    assert not generator._disabled
