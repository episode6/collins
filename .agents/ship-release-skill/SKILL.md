---
name: ship-release-skill
description: >-
  Ship a Collins release branch by publishing a GitHub release with
  scripts/ship-release.py. Use whenever the user asks to "ship the release"
  (or publish/finish a release): verifies every committed copy of the version
  agrees, extracts release notes from docs/releases.md, and creates the
  release + tag v<VERSION>; the tag push triggers release.yml to attach the
  .deb, publish to PyPI, and upload to ppa:episode6/stable.
---

# Ship Release Branch Skill

Ships a release branch by creating a GitHub release pointing at the tip of
the release branch, per [`RELEASE_CHECKLIST.md`](../../RELEASE_CHECKLIST.md).
The `./scripts/ship-release.py` script keeps it consistent:

1. Resolves the release version from `pyproject.toml` on the target release
   branch (never assume the branch name matches the version — hotfixes append
   to existing release branches, e.g. `release/v0.1.1` may be shipping
   `0.1.1.1`), cross-checking `collins/__init__.py`, the top `debian/changelog`
   entry (which must target `UNRELEASED` — the PPA series is stamped at build
   time), and the metainfo's top `<release>` entry.
2. Extracts release notes from the matching `### v<VERSION>` section of
   `docs/releases.md`, refusing a section still titled `Unreleased`.
3. Publishes the GitHub release via `gh release create` with tag and title
   `v<VERSION>`, `--target` the release branch.
4. The tag push triggers `.github/workflows/release.yml`: it builds the
   wheel/sdist + `.deb`, attaches the `.deb` to the release created in step 3,
   publishes to PyPI (trusted publishing), and uploads a signed source package
   per Ubuntu series (noble, resolute) to `ppa:episode6/stable`.

## Prerequisites
- **Merge PRs via GitHub**: the release-finalization PR (and any hardening
  cherry-picks) must be merged via `gh pr merge` / the GitHub UI. **NEVER**
  use local merge commits on the release branch.
- **Pull Latest**: check out the release branch locally and pull from
  `origin` so the release points at the true branch tip.
- **Versions agree**: `python3 scripts/verify_versions.py` passes (CI enforces
  it on the branch too; the ship script re-checks).

## Quick Start
Dry-run first, to eyeball the notes and version:
```bash
./scripts/ship-release.py --dry-run --output /tmp/release-dry-run.json
```

Ship the current release branch:
```bash
./scripts/ship-release.py --output /tmp/release-result.json
```

### Arguments
- `--branch <branch>`: Target branch to point the release to (defaults to
  current branch). Must be a `release/*` branch AND match the checked-out
  branch — the version and notes are read from the working tree, so a
  mismatch would ship this checkout's content under another branch's name
  (both are warnings only on dry-run).
- `--dry-run`: Prints release details and the `gh` command without executing.
- `--output <file_path>`: (Required) Path to write a JSON report of the
  results (`success`, `tag`, `branch`, `url`, `notes`).

## After Shipping
- Watch the `release.yml` run triggered by the new tag; when it finishes,
  verify the `.deb` is attached to the release with the right version and the
  PyPI publish succeeded.
- The PPA jobs only *upload*: Launchpad then emails an acceptance notice per
  series and builds/publishes the binaries (minutes to hours in the queue,
  plus ~20 minutes for the publisher). Confirm the builds go green on
  <https://launchpad.net/~episode6/+archive/ubuntu/stable>.
- If the AUR package is published: refresh `sha256sums` from the now-existing
  tag tarball, regenerate `.SRCINFO`, and push (`packaging/aur/README.md`).

## Common Mistakes
1. **Shipping from a non-release branch**: `main` always carries a
   releasable-looking plain version in this repo, so the script refuses unless
   the target is a `release/*` branch.
2. **Half-landed version bump**: `pyproject.toml`, `__init__.py`, and
   `debian/changelog` disagree — the script fails; land the version-bump PR
   fully first. The `debian/changelog` check matters doubly: the PPA version
   derives from it, and Launchpad burns version strings permanently.
3. **Unfinalized changelog**: the `docs/releases.md` section still says
   `Unreleased` — merge the release-finalization PR first.
4. **Hand-typed release notes**: always let the script extract them from
   `docs/releases.md` so the release, the docs site, and the metainfo tell the
   same story.
5. **Stale local branch**: forgetting to pull after merging the finalization
   PR ships an outdated tip.
