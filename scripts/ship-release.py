#!/usr/bin/env python3
"""Ship a Collins release branch by publishing a GitHub release.

Creates the GitHub release + tag ``v<VERSION>`` pointing at the tip of the
release branch, with notes extracted from the matching section of
``docs/releases.md``. The tag push then triggers
``.github/workflows/release.yml``, which builds the wheel/sdist + ``.deb``,
attaches the ``.deb`` to the release created here, publishes to PyPI, and
uploads a source package per Ubuntu series to ppa:episode6/stable.

Mirrors scripts/ship-release.py in the sibling episode6 repos
(podcast-hacker, the library repos); see RELEASE_CHECKLIST.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

PYPROJECT = "pyproject.toml"
INIT_PY = "collins/__init__.py"
DEBIAN_CHANGELOG = "debian/changelog"
CHANGELOG = "docs/releases.md"
METAINFO = "data/com.episode6.Collins.metainfo.xml"
SPEC = "packaging/fedora/collins.spec"


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def get_current_branch():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        fail(f"getting current git branch: {e.stderr.strip()}")
    return result.stdout.strip()


def read_file(path):
    if not os.path.exists(path):
        fail(f"{path} not found (run from the repo root).")
    with open(path, encoding="utf-8") as f:
        return f.read()


def get_version():
    """The version from pyproject.toml, cross-checked against every other
    committed copy — a mismatch means a version-bump PR only landed halfway."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', read_file(PYPROJECT), re.MULTILINE)
    if not match:
        fail(f"could not find the version in {PYPROJECT}")
    version = match.group(1)

    init_match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', read_file(INIT_PY), re.MULTILINE
    )
    if not init_match or init_match.group(1) != version:
        found = init_match.group(1) if init_match else "<missing>"
        fail(f"{INIT_PY} says {found} but {PYPROJECT} says {version}.")

    deb_match = re.match(r"^collins \(([^)]+)\) (\S+);", read_file(DEBIAN_CHANGELOG))
    if not deb_match:
        fail(f"could not parse the top entry of {DEBIAN_CHANGELOG}")
    if deb_match.group(1) != version:
        fail(
            f"{DEBIAN_CHANGELOG} top entry is {deb_match.group(1)} but "
            f"{PYPROJECT} says {version}. The PPA upload version derives from "
            "the changelog, so this would publish the wrong version — "
            "permanently."
        )
    if deb_match.group(2) != "UNRELEASED":
        fail(
            f"{DEBIAN_CHANGELOG} top entry targets {deb_match.group(2)}; it "
            "must stay at UNRELEASED in git (build-ppa-source.sh stamps the "
            "series in its temp tree only)."
        )

    meta_match = re.search(r'<release version="([^"]+)"', read_file(METAINFO))
    if not meta_match or meta_match.group(1) != version:
        found = meta_match.group(1) if meta_match else "<missing>"
        fail(
            f"the top <release> in {METAINFO} is {found}, expected {version}. "
            "The release-finalization PR adds it (see RELEASE_CHECKLIST.md)."
        )

    spec_match = re.search(r"^Version:\s*(\S+)", read_file(SPEC), re.MULTILINE)
    if not spec_match or spec_match.group(1) != version:
        found = spec_match.group(1) if spec_match else "<missing>"
        fail(
            f"{SPEC} says {found} but {PYPROJECT} says {version}. The COPR "
            "upload version derives from the spec, so this would publish the "
            "wrong version."
        )

    return version


def get_changelog_notes(version):
    """The body of the ``### v<VERSION>`` section of docs/releases.md."""
    lines = read_file(CHANGELOG).splitlines(keepends=True)

    header_pattern = re.compile(rf"^###\s+v{re.escape(version)}(\s|$)")
    any_header_pattern = re.compile(r"^###\s+v\d")
    # The changelog ends where the upstream-history section begins.
    end_pattern = re.compile(r"^(##\s|---)")

    notes = []
    header_line = None
    for line in lines:
        if header_line is not None:
            if any_header_pattern.match(line) or end_pattern.match(line):
                break
            notes.append(line)
        elif header_pattern.match(line):
            header_line = line.rstrip()

    if header_line is None:
        fail(f"could not find a '### v{version}' section in {CHANGELOG}")
    if "unreleased" in header_line.lower():
        fail(
            f"the v{version} section of {CHANGELOG} is still titled "
            "'UNRELEASED' — finalize it (ship date in the heading, complete notes) "
            "before "
            "shipping."
        )

    content = "".join(notes).strip()
    if not content:
        print(f"Warning: changelog notes for v{version} are empty.", file=sys.stderr)
    return content


def check_release_branch(branch, dry_run):
    # main always carries the *next* release's plain version, so guard against
    # accidentally shipping from anything but a release branch.
    if not branch.startswith("release/"):
        message = f"target branch '{branch}' is not a release/* branch."
        if dry_run:
            print(f"[DRY-RUN] Warning: {message}", file=sys.stderr)
        else:
            fail(f"{message} Releases must ship from a release branch.")


def run_gh_release(version, notes, target_branch, dry_run=False):
    tag_name = f"v{version}"

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", delete=False
    ) as temp_notes:
        temp_notes.write(notes)
        temp_notes_path = temp_notes.name

    try:
        cmd = [
            "gh", "release", "create", tag_name,
            "--title", tag_name,
            "--notes-file", temp_notes_path,
            "--target", target_branch,
        ]

        if dry_run:
            print("[DRY-RUN] Would execute command:")
            print(" ".join(cmd))
            print("\n[DRY-RUN] Release Notes:")
            print("-" * 40)
            print(notes)
            print("-" * 40)
            return {
                "success": True,
                "dry_run": True,
                "tag": tag_name,
                "title": tag_name,
                "branch": target_branch,
                "command": " ".join(cmd),
                "notes": notes,
            }

        print(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            fail(f"executing gh release: {e.stderr.strip()}")
        release_url = result.stdout.strip()
        print(f"Success! Created release: {release_url}")
        print(
            "The tag push now triggers release.yml: .deb attached to this "
            "release, PyPI publish, and a PPA source upload per Ubuntu series."
        )
        return {
            "success": True,
            "dry_run": False,
            "tag": tag_name,
            "title": tag_name,
            "branch": target_branch,
            "url": release_url,
            "notes": notes,
        }
    finally:
        if os.path.exists(temp_notes_path):
            os.remove(temp_notes_path)


def main():
    parser = argparse.ArgumentParser(
        description="Ship a release branch by publishing it on GitHub."
    )
    parser.add_argument(
        "--branch",
        help="Target branch to point the release to (defaults to current "
        "branch; must match the checked-out branch, since the version and "
        "notes are read from the working tree)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print details of the release without publishing",
    )
    parser.add_argument(
        "--output", help="Path to write a JSON report of the release results"
    )
    args = parser.parse_args()

    if not args.output:
        fail("--output <file_path> is required to capture the execution results.")

    current_branch = get_current_branch()
    branch = args.branch if args.branch else current_branch
    if branch != current_branch:
        # The version and notes are read from the working tree, so tagging a
        # different branch would silently ship this checkout's content under
        # that branch's name.
        message = (
            f"--branch '{branch}' does not match the checked-out branch "
            f"'{current_branch}'. Check out the release branch and re-run."
        )
        if args.dry_run:
            print(f"[DRY-RUN] Warning: {message}", file=sys.stderr)
        else:
            fail(message)
    check_release_branch(branch, args.dry_run)
    version = get_version()
    notes = get_changelog_notes(version)

    result = run_gh_release(version, notes, branch, dry_run=args.dry_run)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Execution results written to: {args.output}")


if __name__ == "__main__":
    main()
