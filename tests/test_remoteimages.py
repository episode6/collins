import http.server
import os
import threading
import time

import pytest

from collins import remoteimages

# Enough of a PNG to be worth serving; nothing here decodes it (the lightbox
# does that, on a machine with the GTK stack).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56


class _Handler(http.server.BaseHTTPRequestHandler):
    """One response per path — the shapes a real image URL comes in."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # keep the test output clean
        pass

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler's spelling)
        path = self.path
        if path.startswith("/ok"):
            self._body(PNG, "image/png")
        elif path.startswith("/vague"):
            # A plain file server that won't commit: the URL's suffix decides.
            self._body(PNG, "application/octet-stream")
        elif path.startswith("/charset"):
            self._body(PNG, "image/png; charset=binary")
        elif path.startswith("/page"):
            self._body(b"<html></html>", "text/html")
        elif path.startswith("/empty"):
            self._body(b"", "image/png")
        elif path.startswith("/big"):
            self._body(b"x" * 4096, "image/png")
        elif path.startswith("/nolength"):
            # No Content-Length at all: the body is only bounded by the read
            # cap, which is the point of the test that asks for this.
            self.wfile.write(
                b"HTTP/1.0 200 OK\r\nContent-Type: image/png\r\n\r\n" + b"x" * 4096
            )
            self.close_connection = True
        elif path.startswith("/hop"):
            self._redirect("/ok.png")
        elif path.startswith("/badhop"):
            self._redirect("ftp://example.invalid/x.png")
        else:
            self.send_error(404)

    def _body(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    """A loopback HTTP server for the fetch tests — the only network any of
    this touches."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


# ---- what counts as a URL ----------------------------------------------------


def test_only_scheme_prefixed_arguments_look_remote():
    assert remoteimages.looks_remote("https://example.com/a.png")
    assert remoteimages.looks_remote("HTTP://example.com/a.png")
    assert not remoteimages.looks_remote("/tmp/a.png")
    assert not remoteimages.looks_remote("shot.png")
    assert not remoteimages.looks_remote("~/pics/a.png")


def test_a_scheme_we_cant_fetch_says_so():
    """Any scheme looks remote, so the refusal explains itself instead of
    coming back from the path branch as a puzzling 'No such file'."""
    assert "http(s)" in remoteimages.url_error("ftp://h/a.png")
    assert "http(s)" in remoteimages.url_error("file:///etc/passwd")
    assert "no host" in remoteimages.url_error("http:///a.png")
    assert remoteimages.url_error("https://example.com/a.png") is None


# ---- what the response has to be ---------------------------------------------


def test_known_image_content_types_pick_the_suffix():
    assert remoteimages.suffix_for("https://h/x", "image/png") == ".png"
    assert remoteimages.suffix_for("https://h/x", "image/jpeg; charset=x") == ".jpg"
    assert remoteimages.suffix_for("https://h/x", "image/svg+xml") == ".svg"


def test_a_vague_content_type_falls_back_to_the_url_suffix():
    assert remoteimages.suffix_for("https://h/a.webp", "application/octet-stream") == ".webp"
    assert remoteimages.suffix_for("https://h/a.PNG?v=2", None) == ".png"
    assert remoteimages.suffix_for("https://h/a", "application/octet-stream") is None


def test_an_image_format_the_viewer_cant_decode_is_refused():
    """Trusted content type, unknown format: refusing here beats downloading
    megabytes the lightbox can only fail on."""
    assert remoteimages.suffix_for("https://h/a.heic", "image/heic") is None


# ---- fetching ----------------------------------------------------------------


def test_fetch_returns_the_bytes_and_a_suffix(server):
    data, suffix = remoteimages.fetch(f"{server}/ok.png")
    assert data == PNG
    assert suffix == ".png"


def test_fetch_takes_the_content_type_over_the_url(server):
    data, suffix = remoteimages.fetch(f"{server}/charset")
    assert (data, suffix) == (PNG, ".png")


def test_fetch_refuses_a_page(server):
    with pytest.raises(remoteimages.FetchError, match="isn't an image"):
        remoteimages.fetch(f"{server}/page.html")


def test_fetch_reports_the_status_code(server):
    with pytest.raises(remoteimages.FetchError, match="answered 404"):
        remoteimages.fetch(f"{server}/missing.png")


def test_fetch_reports_an_unreachable_host():
    with pytest.raises(remoteimages.FetchError, match="Couldn't reach"):
        remoteimages.fetch("http://127.0.0.1:1/a.png")


def test_fetch_refuses_an_empty_body(server):
    with pytest.raises(remoteimages.FetchError, match="empty response"):
        remoteimages.fetch(f"{server}/empty.png")


def test_a_declared_size_over_the_cap_is_refused_before_the_body(server, monkeypatch):
    monkeypatch.setattr(remoteimages, "MAX_BYTES", 64)
    with pytest.raises(remoteimages.FetchError, match="larger than"):
        remoteimages.fetch(f"{server}/big.png")


def test_an_undeclared_size_is_capped_while_reading(server, monkeypatch):
    """The Content-Length check is a courtesy; the read is the boundary — a
    server that declares nothing (or lies) hits it instead."""
    monkeypatch.setattr(remoteimages, "MAX_BYTES", 64)
    monkeypatch.setattr(remoteimages, "_CHUNK_BYTES", 16)
    with pytest.raises(remoteimages.FetchError, match="larger than"):
        remoteimages.fetch(f"{server}/nolength.png")


def test_a_slow_body_hits_the_deadline(server, monkeypatch):
    """A socket timeout bounds one read, not their sum — the wall clock does.
    The clock is injected so the test doesn't have to spend the ten seconds."""
    monkeypatch.setattr(remoteimages, "_CHUNK_BYTES", 16)
    clock = iter([0.0, 0.0, 1.0, remoteimages.TIMEOUT_SECONDS + 1.0])
    with pytest.raises(remoteimages.FetchError, match="Timed out"):
        remoteimages.fetch(f"{server}/big.png", now=lambda: next(clock))


def test_redirects_are_followed(server):
    data, suffix = remoteimages.fetch(f"{server}/hop")
    assert (data, suffix) == (PNG, ".png")


def test_a_redirect_off_http_is_refused(server):
    with pytest.raises(remoteimages.FetchError, match="won't follow"):
        remoteimages.fetch(f"{server}/badhop")


# ---- landing on disk ---------------------------------------------------------


def test_fetch_to_file_saves_under_the_fetched_suffix(server, tmp_path):
    path = remoteimages.fetch_to_file(f"{server}/vague.webp", tmp_path)
    assert path.parent == tmp_path
    assert path.name.startswith("remote-")
    assert path.suffix == ".webp"
    assert path.read_bytes() == PNG


def test_fetch_to_file_prunes_stale_downloads(server, tmp_path):
    stale = tmp_path / "remote-old.png"
    stale.write_bytes(b"old")
    old = time.time() - remoteimages.PRUNE_AFTER_SECONDS - 60
    os.utime(stale, (old, old))
    fresh = tmp_path / "remote-new.png"
    fresh.write_bytes(b"new")

    remoteimages.fetch_to_file(f"{server}/ok.png", tmp_path)

    assert not stale.exists()
    assert fresh.exists()


def test_prune_survives_a_missing_directory(tmp_path):
    remoteimages.prune_stale(tmp_path / "never-created")  # no raise


def test_downloads_live_beside_the_dropped_image_copies(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from collins import dropimages

    assert remoteimages.default_directory().parent == dropimages.cache_directory()
    assert remoteimages.default_directory() != dropimages.default_directory()
