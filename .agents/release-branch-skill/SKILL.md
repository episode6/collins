---
name: release-branch-skill
description: >-
  Cut a new Collins release branch and prepare the version-bump PRs, as
  defined in RELEASE_CHECKLIST.md. Use whenever the user asks to "cut a
  release branch" (or to cut/create/start a new release, release branch, or
  version): verifies main is green, creates release/v<VERSION>, and opens the
  next-version-on-main and release-on-branch PRs that update pyproject.toml,
  collins/__init__.py, debian/changelog, the Fedora spec, docs/releases.md,
  the AppStream metainfo, and the AUR PKGBUILD.
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
- Ensure the `main` branch is passing all CI checks, the e2e job included
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
      `<NEXT_VERSION>`, bump **the patch by 1** (`0.1.1` → `0.1.2`). Never
      automatically increment the major or minor version — those bumps
      require explicit human decision. (Hotfix versions append a fourth
      segment on the release branch instead and never involve main — see
      `RELEASE_CHECKLIST.md`.)
    - **(VITAL)** Add a new top `debian/changelog` entry at `<NEXT_VERSION>`
      targeting `UNRELEASED` (`dch -v <NEXT_VERSION> -D UNRELEASED "..."`, or
      by hand matching the existing entries — author line
      `Geoff Hackett <ghackett@episode6.com>`).
    - **(VITAL)** In `packaging/fedora/collins.spec`: set `Version:` to
      `<NEXT_VERSION>` (and `Release:` back to `1%{?dist}` if it was bumped),
      and add a new top `%changelog` entry for it — same header shape as the
      existing ones (`* <Day Mon DD YYYY> Geoff Hackett
      <ghackett@episode6.com> - <NEXT_VERSION>-1`, the date being today's,
      weekday included), one `-` line saying what the release is.
    - **(VITAL)** In `docs/releases.md`: add a new
      `### v<NEXT_VERSION> — UNRELEASED` section atop the Changelog.
    - **(VITAL)** Finalize the outgoing `v<VERSION>` in **all three
      changelogs** (the checklist's Changelogs section lists them and their
      audiences). First list the PRs merged since the last release
      (`gh pr list --state merged --search "merged:>YYYY-MM-DD"`) and check
      each is reflected in every one:
        - `docs/releases.md`: the `v<VERSION>` section gets its ship date
          (`### v<VERSION> — YYYY-MM-DD`, replacing `UNRELEASED`) and complete
          notes — that section becomes the GitHub release notes verbatim.
        - `debian/changelog`: the `<VERSION>` entry gets a condensed `*`
          bullet per headline change (packaging first, then features and
          fixes) — it is what `apt changelog` and the PPA page show.
        - `data/com.episode6.Collins.metainfo.xml`: a
          `<release version="<VERSION>" date="<planned ship date>">` entry
          atop the `<releases>` list, with a `<description>` (a paragraph
          and a short `<ul>`) — software centers show it.
        - `packaging/fedora/collins.spec`: the `%changelog` entry for
          `<VERSION>` says what shipped, in a line or two — `rpm -q
          --changelog` and the COPR build page show it.
    - Mirror the outgoing release into the AUR files: set
      `pkgver=<VERSION>` in `packaging/aur/PKGBUILD` + the matching
      `pkgver`/`source` lines in `packaging/aur/.SRCINFO` (the sha256 refresh
      waits until the tag exists — post-ship step in the checklist).

#### PR 2: Release Finalization on Release Branch
- **Target Branch:** `release/v<VERSION>`
- **PR Title:** `[VERSION] Release v<VERSION>`
- **Changes:**
    - **(VITAL)** Make the same outgoing-release edits as PR 1: finalize all
      three changelogs for `v<VERSION>` (ship date in the `docs/releases.md`
      heading; every change since the last release in each), and the AUR
      `pkgver`.
    - Verify `pyproject.toml`, `collins/__init__.py`, the top
      `debian/changelog` entry and the spec's `Version:` already agree on
      `<VERSION>` (no version change expected — main carried the right
      version at cut time), e.g. by running `python3 scripts/verify_versions.py`.

### 4. Create Pull Requests
- Use `gh pr create` (as drafts) or the GitHub UI to create the two PRs from
  the branches prepared in step 3.

## Verification
- After these steps, the project is ready for the "Harden Release Branch"
  phase (CI green on the branch, a real `.deb` install sanity pass, license
  check), which requires manual verification and cherry-picking of bug fixes
  (via the `cherry-pick-pr` skill). See `RELEASE_CHECKLIST.md`.
