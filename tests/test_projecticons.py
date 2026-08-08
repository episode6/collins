"""Tests for project-icon discovery (collins.projecticons)."""

from pathlib import Path

from collins.projecticons import (
    _MAX_ICON_BYTES,
    PROJECT_ICON_FILENAME,
    project_icon_data,
    project_icon_path,
)

_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>'


def _write_icon(project: Path, data: bytes = _SVG) -> Path:
    icon = project / PROJECT_ICON_FILENAME
    icon.write_bytes(data)
    return icon


def test_finds_icon_in_project_root(tmp_path):
    icon = _write_icon(tmp_path)
    assert project_icon_path(tmp_path) == icon
    assert project_icon_path(str(tmp_path)) == icon  # str cwd, as sessions record it


def test_no_icon_file_means_none(tmp_path):
    assert project_icon_path(tmp_path) is None


def test_empty_and_missing_cwd_mean_none(tmp_path):
    assert project_icon_path(None) is None
    assert project_icon_path("") is None
    assert project_icon_path(tmp_path / "gone") is None


def test_directory_named_like_icon_is_ignored(tmp_path):
    (tmp_path / PROJECT_ICON_FILENAME).mkdir()
    assert project_icon_path(tmp_path) is None


def test_empty_icon_is_ignored(tmp_path):
    _write_icon(tmp_path, b"")
    assert project_icon_path(tmp_path) is None


def test_oversized_icon_is_ignored(tmp_path):
    _write_icon(tmp_path, b"x" * (_MAX_ICON_BYTES + 1))
    assert project_icon_path(tmp_path) is None


def test_icon_at_size_cap_is_accepted(tmp_path):
    icon = _write_icon(tmp_path, b"x" * _MAX_ICON_BYTES)
    assert project_icon_path(tmp_path) == icon


def test_icon_in_subdirectory_is_not_picked_up(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _write_icon(tmp_path)
    assert project_icon_path(sub) is None


# -- project_icon_data: the content gate --------------------------------------


def test_data_returns_svg_bytes(tmp_path):
    _write_icon(tmp_path)
    assert project_icon_data(tmp_path) == _SVG
    assert project_icon_data(str(tmp_path)) == _SVG


def test_data_accepts_xml_prolog_comments_and_bom(tmp_path):
    prolog = b'<?xml version="1.0"?>\n<!-- license -->\n' + _SVG
    _write_icon(tmp_path, prolog)
    assert project_icon_data(tmp_path) == prolog

    bom = b"\xef\xbb\xbf  \n" + _SVG
    _write_icon(tmp_path, bom)
    assert project_icon_data(tmp_path) == bom


def test_data_rejects_gzip(tmp_path):
    # svgz decompresses transparently in librsvg, so a tiny file could
    # expand far past the size cap — refuse the magic outright.
    _write_icon(tmp_path, b"\x1f\x8b\x08" + b"x" * 64)
    assert project_icon_data(tmp_path) is None


def test_data_rejects_non_xml_content(tmp_path):
    # Content sniffing must never get a chance to route these to another
    # image codec: anything that isn't XML-shaped text is refused.
    _write_icon(tmp_path, b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    assert project_icon_data(tmp_path) is None
    _write_icon(tmp_path, b"just some text")
    assert project_icon_data(tmp_path) is None


def test_data_requires_svg_element_near_the_top(tmp_path):
    _write_icon(tmp_path, b"<html><body>hi</body></html>")
    assert project_icon_data(tmp_path) is None


def test_data_rejects_active_content(tmp_path):
    # An icon is inert artwork; scripts, event handlers, and references to
    # anything outside the document are refused before a parser sees them.
    for payload in (
        b"<script>alert(1)</script>",
        b'<image href="https://evil.example/x.png"/>',
        b'<use xlink:href="file:///etc/passwd"/>',
        b'<rect style="fill:url(http://evil.example/f)"/>',
        b'<rect onload="x()"/>',
    ):
        _write_icon(tmp_path, _SVG.replace(b"/>", b">" + payload + b"</svg>"))
        assert project_icon_data(tmp_path) is None


def test_data_accepts_inline_png_data_uris(tmp_path):
    # A hand-shipped icon may embed its own raster artwork as a
    # data:image/png href — projects only reach the sidebar once trusted.
    for payload in (
        b'<image href="data:image/png;base64,iVBORw0KGgo="/>',
        b'<image xlink:href="data:image/png;base64,iVBORw0KGgo="/>',
    ):
        good = _SVG.replace(b"/>", b">" + payload + b"</svg>")
        _write_icon(tmp_path, good)
        assert project_icon_data(tmp_path) == good


def test_data_rejects_non_png_data_uris(tmp_path):
    # PNG is the only subtype with a carve-out. image/svg+xml in particular
    # stays refused: it is XML whose base64 payload could carry the
    # script/handler content the plain-text checks can't see. Other raster
    # codecs, lookalike subtypes, other media types, whitespace-smuggled
    # URIs, and CSS url() are refused too.
    for payload in (
        b'<image href="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Lz48L3N2Zz4="/>',
        b'<image xlink:href="data:image/svg+xml,<svg onload=x()/>"/>',
        b'<image href="data:image/jpeg;base64,/9j/4AA="/>',
        b'<image href="data:image/webp,x"/>',
        b'<image href="data:image/png-evil;base64,AA=="/>',
        b'<image href="data:text/html,<script>alert(1)</script>"/>',
        b'<image href=" data:image/png;base64,iVBORw0KGgo="/>',
        b'<rect style="fill:url(data:image/png;base64,AA==)"/>',
    ):
        _write_icon(tmp_path, _SVG.replace(b"/>", b">" + payload + b"</svg>"))
        assert project_icon_data(tmp_path) is None


def test_data_keeps_internal_references(tmp_path):
    good = _SVG.replace(
        b"/>",
        b'><defs><linearGradient id="g"/></defs><rect fill="url(#g)"/><use xlink:href="#g"/></svg>',
    )
    _write_icon(tmp_path, good)
    assert project_icon_data(tmp_path) == good


def test_data_none_when_no_icon(tmp_path):
    assert project_icon_data(tmp_path) is None
    assert project_icon_data(None) is None
