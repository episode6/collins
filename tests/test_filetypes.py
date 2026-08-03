"""Tests for filetypes.icon_for: the file tree's icon-and-color lookup."""

from collins.filetypes import icon_for


def test_directories_keep_the_stock_folder():
    assert icon_for("src", is_dir=True) == ("folder-symbolic", None)
    # ...even when the name would match a file rule
    assert icon_for("license", is_dir=True) == ("folder-symbolic", None)


def test_unknown_files_get_the_plain_file_uncolored():
    assert icon_for("mystery.xyz") == ("ft-file-symbolic", None)
    assert icon_for("no-suffix-at-all") == ("ft-file-symbolic", None)


def test_common_languages_are_colored_code():
    assert icon_for("app.py") == ("ft-file-code-symbolic", "ft-blue")
    assert icon_for("index.js") == ("ft-file-code-symbolic", "ft-yellow")
    assert icon_for("lib.rs") == ("ft-file-code-symbolic", "ft-orange")


def test_lookup_is_case_insensitive():
    assert icon_for("MODULE.PY") == icon_for("module.py")
    assert icon_for("ReadMe.md")[0] == "ft-book-symbolic"


def test_exact_names_outrank_suffixes():
    """package.json is a manifest before it is JSON."""
    assert icon_for("package.json") == ("ft-package-symbolic", "ft-red")
    assert icon_for("data.json") == ("ft-file-code-symbolic", "ft-yellow")


def test_prefix_rules_survive_suffix_noise():
    assert icon_for("LICENSE-MIT.txt") == ("ft-law-symbolic", "ft-yellow")
    assert icon_for("Dockerfile.debug") == ("ft-container-symbolic", "ft-blue")
    assert icon_for(".env.local") == ("ft-gear-symbolic", "ft-yellow")


def test_any_dot_lock_is_a_lockfile():
    assert icon_for("Cargo.lock") == ("ft-lock-symbolic", "ft-grey")
    assert icon_for("flake.lock") == ("ft-lock-symbolic", "ft-grey")
    assert icon_for("yarn.lock") == ("ft-lock-symbolic", "ft-grey")


def test_git_metadata_files():
    assert icon_for(".gitignore") == ("ft-diff-ignored-symbolic", "ft-orange")
    assert icon_for(".gitattributes") == ("ft-diff-ignored-symbolic", "ft-orange")


def test_media_and_archives():
    assert icon_for("shot.png") == ("ft-image-symbolic", "ft-purple")
    assert icon_for("intro.mp4") == ("ft-video-symbolic", "ft-pink")
    assert icon_for("bundle.tar") == ("ft-file-zip-symbolic", "ft-grey")
    assert icon_for("core.so") == ("ft-file-binary-symbolic", "ft-grey")
