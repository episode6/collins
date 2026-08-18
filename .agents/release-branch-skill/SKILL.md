---
name: release-branch-skill
description: >-
  Cut a new Collins release branch and prepare the version-bump PRs, as
  defined in RELEASE_CHECKLIST.md. Use whenever the user asks to "cut a
  release branch" (or to cut/create/start a new release, release branch, or
  version): verifies main is green, creates release/v<VERSION>, and opens the
  next-version-on-main and release-on-branch PRs that update pyproject.toml,
  collins/__init__.py, debian/changelog, docs/releases.md, the AppStream
  metainfo, and the AUR PKGBUILD.
---

# Cut Release Branch Skill

Automates cutting a new release branch and preparing the version bumps, as
defined in [`RELEASE_CHECKLIST.md`](../../RELEASE_CHECKLIST.md). Mirrors the
release-branch skills in the sibling episode6 repos.

**No `-SNAPSHOT`-style markers in this repo**: `main` always carries the
*next* release's plain `MAJOR.MINOR.PATCH` version, so the release branch
inherits the correct version when cut — the "release" PR only finalizes docs.
`scripts/verify_versions.py` (run by CI) checks that every committed copy of
the version agrees; run it after each edit below.

## Steps to Execute

### 1. Pre-check
- Ensure the `main` branch is passing all CI checks including the e2e gate
  (is "green").
- `<VERSION>` = the current `version` in `pyproject.toml` on `main`. This is
  the version being released.

### 2. Cut new Release Branch
- Checkout the `main` branch and pull the latest changes.
- Create a new branch: `git checkout -b release/v<VERSION>`
- Push the empty branch and set it to be tracked:
  `git push -u origin release/v<VERSION>`

### 3. Version Bump PRs
Create two separate Pull Requests (as drafts, per repo convention).

#### PR 1: Next version on `main`
- **Target Branch:** `main`
- **PR Title:** `[VERSION] Next v<NEXT_VERSION>`
- **Changes:**
    - **(VITAL)** Bump `version` in `pyproject.toml` AND `__version__` in
      `collins/__init__.py` to `<NEXT_VERSION>`. When computing
      `<NEXT_VERSION>`, bump **the patch to the next multiple of 10**
      (`0.1.10` → `0.1.20`). Never automatically increment the major or minor
      version — those bumps require explicit human decision — and never hand
      out the 9 patch values above a release: they're reserved for hotfixing
      it.
    - **(VITAL)** Add a new top `debian/changelog` entry at `<NEXT_VERSION>`
      targeting `UNRELEASED` (`dch -v <NEXT_VERSION> -D UNRELEASED "..."`, or
      by hand matching the existing entries — author line
      `Geoff Hackett <ghackett@episode6.com>`).
    - **(VITAL)** In `docs/releases.md`: add a new
      `### v<NEXT_VERSION> — Unreleased` section atop the Changelog, and give
      the outgoing `v<VERSION>` section its real title (replacing
      `Unreleased`) and complete notes — that section becomes the GitHub
      release notes verbatim.
    - Mirror the outgoing release into the released-version files: add a
      `<release version="<VERSION>" date="<planned ship date>">` entry atop
      the `<releases>` list in `data/com.episode6.Collins.metainfo.xml`, and
      set `pkgver=<VERSION>` in `packaging/aur/PKGBUILD` + the matching
      `pkgver`/`source` lines in `packaging/aur/.SRCINFO` (the sha256 refresh
      waits until the tag exists — post-ship step in the checklist).

#### PR 2: Release Finalization on Release Branch
- **Target Branch:** `release/v<VERSION>`
- **PR Title:** `[VERSION] Release v<VERSION>`
- **Changes:**
    - **(VITAL)** Make the same outgoing-release edits as PR 1: finalize the
      `docs/releases.md` `v<VERSION>` section (real title; ensure all changes
      since the last release are documented), the metainfo `<release>` entry,
      and the AUR `pkgver`.
    - Verify `pyproject.toml`, `collins/__init__.py`, and the top
      `debian/changelog` entry already agree on `<VERSION>` (no version change
      expected — main carried the right version at cut time), e.g. by running
      `python3 scripts/verify_versions.py`.

### 4. Create Pull Requests
- Use `gh pr create` (as drafts) or the GitHub UI to create the two PRs from
  the branches prepared in step 3.

## Verification
- After these steps, the project is ready for the "Harden Release Branch"
  phase (CI + e2e on the branch, a real `.deb` install sanity pass, license
  check), which requires manual verification and cherry-picking of bug fixes
  (via the `cherry-pick-pr` skill). See `RELEASE_CHECKLIST.md`.
