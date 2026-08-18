# Collins Release Checklist

This mirrors the episode6 library/app repos' release process — cut a release
branch, harden it, ship from it — adapted to a Python/GTK app whose one tag
push fans out to a GitHub release with a `.deb`, a PyPI publish, and a source
upload per Ubuntu series to `ppa:episode6/stable`. Agent skills in
[.agents/](./.agents) automate most of it (`release-branch-skill`,
`ship-release-skill`).

## Versioning

- `version` in `pyproject.toml` is the reference copy: plain dotted numerals,
  no suffixes — `MAJOR.MINOR.PATCH`, plus a fourth segment on hotfixes (see
  below). `collins/__init__.py` (`__version__`) and
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
- **Cutting a release branch bumps the patch by 1** (`0.1.1` → `0.1.2`).
  Hotfixes append a fourth segment to the release they fix (`0.1.1` →
  `0.1.1.1`), so they read as hotfixes at a glance and can never collide with
  main's next version. (Unlike podcast-hacker, no platform Collins ships to
  restricts versions to three segments — dpkg, PEP 440, pacman, and AppStream
  all order `0.1.1 < 0.1.1.1 < 0.1.2` correctly.) Major/minor bumps are an
  explicit human decision (never automatic) and reset the lower segments to 0.
- `debian/changelog` stays targeted at `UNRELEASED` in git, forever — the
  per-series PPA version (`0.1.2~noble1`) is stamped by
  `packaging/build-ppa-source.sh` in its temp tree only. See
  `packaging/README.md` for the scheme.
- **Launchpad burns version strings permanently** (a rejected upload or failed
  build included, across all series). The release workflow refuses to upload
  when `debian/changelog` disagrees with the tag, and skips a series whose
  version the archive already has — but treat every version number as
  single-use anyway.

## Changelogs

Collins keeps **three** changelogs, each read by a different audience, and a
release is not finalized until all three describe it. (Consolidating them
into one source is a future project; until then, every step below that says
"the changelogs" means all of these.)

| File | Who reads it | What goes in it |
| --- | --- | --- |
| `docs/releases.md` | The GitHub release page (the `### v<VERSION>` section becomes the release notes verbatim) and the docs site | Full notes: every user-visible change since the last release, in prose |
| `debian/changelog` | `apt changelog collins`, the Launchpad PPA page, and the `.deb`'s `changelog.gz` | One `*` bullet per headline change, condensed — packaging changes first, then features and fixes |
| `data/com.episode6.Collins.metainfo.xml` | GNOME Software and other AppStream software centers, via the `<release>` entry's `<description>` | A one-paragraph summary and a short `<ul>` of the headline changes |

Mismatched versions are caught by CI (`scripts/verify_versions.py`), but
nothing checks that the notes themselves are complete — before finalizing,
list the PRs merged since the last release (`gh pr list --state merged
--search "merged:>YYYY-MM-DD"`) and check each one is reflected in all three.

## Cut new Release Branch

1. Ensure the `main` branch is green (every CI job, e2e included).
2. `<VERSION>` = the current `version` in `pyproject.toml` on `main`.
3. `git checkout -b release/v<VERSION>`
4. Push/track the empty branch: `git push -u origin release/v<VERSION>`

CI (e2e included) runs on pushes to `release/**`, so the branch stays
verified while it hardens.

## Version bump PRs

Create 2 PRs (as drafts, per repo convention):

- `[VERSION] Next v<NEXT_VERSION>` points at `main`
    - Bump `version` in `pyproject.toml` and `__version__` in
      `collins/__init__.py` to `<NEXT_VERSION>` (VITAL — patch + 1, see
      Versioning above).
    - Add a new top `debian/changelog` entry:
      `dch -v <NEXT_VERSION> -D UNRELEASED` (or by hand, matching the existing
      entries).
    - `docs/releases.md`: add a new `### v<NEXT_VERSION> — UNRELEASED` section
      atop the changelog.
    - Finalize the outgoing `v<VERSION>` in **all three changelogs** (see
      Changelogs above): the `docs/releases.md` section gets its ship date
      (`### v<VERSION> — YYYY-MM-DD`) and complete notes; the `debian/changelog`
      `<VERSION>` entry gets a bullet per headline change, not just the
      packaging ones; and the metainfo gets a `<release version="<VERSION>"
      date="...">` entry (date = the planned ship date) with a `<description>`
      summarizing the release.
    - Mirror the outgoing release into the AUR files so main's copies stay
      current once it ships: set `pkgver` to `<VERSION>` in `PKGBUILD` +
      `.SRCINFO` (`sha256sums` stays `SKIP` in git; the release workflow's
      `aur` job fills it in at publish time — see
      `packaging/aur/README.md`).
- `[VERSION] Release v<VERSION>` points at the new release branch
    - Make the same outgoing-release edits as above: finalize all three
      changelogs for `v<VERSION>` (ship date in the `docs/releases.md`
      heading, every change since the last release in each), and the AUR
      `pkgver`.
    - Verify `pyproject.toml` / `__init__.py` / `debian/changelog` already
      agree on `<VERSION>` — no version change expected; main carried the
      right version at cut time.

## Harden Release Branch

- CI green (e2e included) on the release branch.
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
   agree, refuses a still-`UNRELEASED` changelog section, and creates the
   GitHub release + tag `v<VERSION>` pointing at the release branch, with
   notes extracted from `docs/releases.md`.
2. The tag push triggers `.github/workflows/release.yml`:
   - builds the wheel/sdist + `.deb` and attaches the `.deb` to the release,
   - publishes to PyPI via trusted publishing,
   - uploads a signed source package per Ubuntu series (noble, resolute) to
     `ppa:episode6/stable` — Launchpad then builds and publishes the binaries
     (minutes to hours in the queue, plus ~20 minutes for the publisher),
   - fills the tag tarball's hash into the AUR `PKGBUILD`, regenerates
     `.SRCINFO`, test-builds the package, and pushes both files to the AUR
     repo (`packaging/aur/README.md`).
   CI's `ppa-source` job rehearses the source build unsigned on every PR, in
   the same container images the `ppa` job uses, so a missing build-dep
   should already have surfaced before the tag.
   If a `ppa` or `aur` job fails for a reason fixed in the workflow itself,
   the tag's run cannot pick the fix up (it is frozen on the file as of the
   tag): land the fix on the release branch and dispatch it for the tag —
   `gh workflow run release.yml --ref release/v<X> -f tag=v<VERSION>`. Both
   jobs' already-published guards make this safe to repeat.
3. Verify: the `.deb` is attached and carries the right version; Launchpad
   sends an acceptance email per series and the builds go green; `apt install
   collins` from the PPA on a covered series picks up the new version; the
   [AUR page](https://aur.archlinux.org/packages/collins) shows the new
   `pkgver`.

## Hotfixes

- We do not cut new release branches for hotfixes; append to the affected
  release branch and ship a new tag from it.
- All fixes (including hotfixes) land on `main` first whenever possible and
  are cherry-picked onto the release branch (via PR).
- A hotfix needs its own version bump PR on the release branch: append or
  increment the fourth version segment (`0.1.1` → `0.1.1.1` → `0.1.1.2`) and
  describe the hotfix in all three changelogs — a `debian/changelog` entry, a
  `### v<HOTFIX_VERSION>` section in `docs/releases.md`, and a metainfo
  `<release>` entry with a description — plus the AUR `pkgver`. No
  coordination with `main` is needed — its next-release version already
  outranks any hotfix of the previous release — but cherry-pick the docs
  updates back so main's history stays complete.
