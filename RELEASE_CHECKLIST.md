# Collins Release Checklist

This mirrors the episode6 library/app repos' release process — cut a release
branch, harden it, ship from it — adapted to a Python/GTK app whose one tag
push fans out to a GitHub release with a `.deb`, a PyPI publish, and a source
upload per Ubuntu series to `ppa:episode6/stable`. Agent skills in
[.agents/](./.agents) automate most of it (`release-branch-skill`,
`ship-release-skill`).

## Versioning

- `version` in `pyproject.toml` is the reference copy: plain
  `MAJOR.MINOR.PATCH`, no suffixes. `collins/__init__.py` (`__version__`) and
  the top `debian/changelog` entry must match it exactly, and
  `docs/releases.md` must carry a `### v<VERSION>` section for it —
  `scripts/verify_versions.py` enforces all of that in CI (the `ci.yml`
  verify-versions job), and `scripts/ship-release.py` re-checks it at ship
  time.
- Two files track *released* versions instead and may lag behind main, but
  never run ahead (also CI-checked): the AppStream metainfo
  (`data/com.episode6.Collins.metainfo.xml` — its top `<release>` entry is the
  newest *shipped* release) and the AUR `packaging/aur/PKGBUILD` / `.SRCINFO`
  (`pkgver` points at a real `v*` tag).
- **`main` always carries the *next* release's version**, so the release
  branch inherits the correct version when cut — the release-finalization PR
  only finalizes docs. There are no `-SNAPSHOT` markers; a build from main is
  simply a version that hasn't shipped yet.
- **Cutting a release branch bumps the patch to the next multiple of 10**
  (`0.1.10` → `0.1.20`; and from the pre-scheme `0.1.1`, → `0.1.10`), so
  regular releases land on multiples of 10 and the 9 values above each release
  are reserved for hotfixing it (`0.1.10` → hotfixes `0.1.11`–`0.1.19`).
  Major/minor bumps are an explicit human decision (never automatic) and reset
  the lower segments to 0.
- `debian/changelog` stays targeted at `UNRELEASED` in git, forever — the
  per-series PPA version (`0.1.10~noble1`) is stamped by
  `packaging/build-ppa-source.sh` in its temp tree only. See
  `packaging/README.md` for the scheme.
- **Launchpad burns version strings permanently** (a rejected upload or failed
  build included, across all series). The release workflow refuses to upload
  when `debian/changelog` disagrees with the tag, and skips a series whose
  version the archive already has — but treat every version number as
  single-use anyway.

## Cut new Release Branch

1. Ensure the `main` branch is green (CI + e2e).
2. `<VERSION>` = the current `version` in `pyproject.toml` on `main`.
3. `git checkout -b release/v<VERSION>`
4. Push/track the empty branch: `git push -u origin release/v<VERSION>`

CI and the e2e gate run on pushes to `release/**`, so the branch stays
verified while it hardens.

## Version bump PRs

Create 2 PRs (as drafts, per repo convention):

- `[VERSION] Next v<NEXT_VERSION>` points at `main`
    - Bump `version` in `pyproject.toml` and `__version__` in
      `collins/__init__.py` to `<NEXT_VERSION>` (VITAL — patch to the next
      multiple of 10, see Versioning above).
    - Add a new top `debian/changelog` entry:
      `dch -v <NEXT_VERSION> -D UNRELEASED` (or by hand, matching the existing
      entries).
    - `docs/releases.md`: add a new `### v<NEXT_VERSION> — Unreleased` section
      atop the changelog, and give the outgoing `v<VERSION>` section its real
      title and complete notes (this section becomes the GitHub release
      notes).
    - Mirror the outgoing release into the released-version files so main's
      copies stay current once it ships: add the `<release
      version="<VERSION>" date="...">` entry to the metainfo (date = the
      planned ship date), and set the AUR `pkgver` to `<VERSION>` in
      `PKGBUILD` + `.SRCINFO` (sha256 gets refreshed after the tag exists —
      see `packaging/aur/README.md`).
- `[VERSION] Release v<VERSION>` points at the new release branch
    - Make the same outgoing-release edits as above: finalize the
      `docs/releases.md` section (real title, all changes since the last
      release documented), metainfo `<release>` entry, AUR `pkgver`.
    - Verify `pyproject.toml` / `__init__.py` / `debian/changelog` already
      agree on `<VERSION>` — no version change expected; main carried the
      right version at cut time.

## Harden Release Branch

- CI + e2e green on the release branch.
- Sanity pass on a real install: `./scripts/build_deb.sh`,
  `sudo apt install ./dist/collins_<VERSION>_all.deb`, launch it, open a
  session, then remove/reinstall your dev setup as needed.
- `THIRD_PARTY_LICENSES.md` still matches what actually ships.
- Fix any bugs on the `main` branch first, then cherry-pick into the release
  branch via PR (the `cherry-pick-pr` skill: a 🍒-prefixed draft PR based off
  the release branch, cherry-picking the squashed main commit).

## Release

1. From the up-to-date release branch:
   `./scripts/ship-release.py --output /tmp/release-result.json`
   (`--dry-run` first to eyeball the notes). It verifies the version copies
   agree, refuses a still-`Unreleased` changelog section, and creates the
   GitHub release + tag `v<VERSION>` pointing at the release branch, with
   notes extracted from `docs/releases.md`.
2. The tag push triggers `.github/workflows/release.yml`:
   - builds the wheel/sdist + `.deb` and attaches the `.deb` to the release,
   - publishes to PyPI via trusted publishing,
   - uploads a signed source package per Ubuntu series (noble, resolute) to
     `ppa:episode6/stable` — Launchpad then builds and publishes the binaries
     (minutes to hours in the queue, plus ~20 minutes for the publisher).
3. Verify: the `.deb` is attached and carries the right version; Launchpad
   sends an acceptance email per series and the builds go green; `apt install
   collins` from the PPA on a covered series picks up the new version.
4. AUR (once the package is published there): refresh `sha256sums` from the
   now-existing tag tarball, regenerate `.SRCINFO`, and push to the AUR repo
   (`packaging/aur/README.md`).

## Hotfixes

- We do not cut new release branches for hotfixes; append to the affected
  release branch and ship a new tag from it.
- All fixes (including hotfixes) land on `main` first whenever possible and
  are cherry-picked onto the release branch (via PR).
- A hotfix needs its own version bump PR on the release branch: bump the
  patch by 1 within the release's reserved range (`0.1.10` → `0.1.11`, up to
  `0.1.19`), add the `debian/changelog` entry, and give `docs/releases.md` a
  `### v<HOTFIX_VERSION>` section plus the metainfo/AUR updates. No
  coordination with `main` is needed — its next-release version already
  outranks the whole hotfix range — but cherry-pick the docs updates back so
  main's history stays complete.
