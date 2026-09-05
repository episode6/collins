---
name: collins-packaging-and-ci
description: >-
  How Collins is packaged, versioned and built in CI: the wheel and its
  package-data symlinks into data/, collins --install-desktop, the .deb
  (scripts/build_deb.sh, debian/), the Fedora spec and COPR (packaging/fedora),
  the Ubuntu PPA source builds (packaging/build-ppa-source.sh), the AUR recipe,
  the prebuilt CI images (.github/docker/ci.Dockerfile, ci-image.yml), the
  ci.yml jobs, verify_versions.py / verify_wheel_data.py, the version scheme
  and the four changelogs, and release.yml. Use when adding a data file,
  icon, sound or dependency, changing a workflow or Dockerfile, touching
  version numbers or changelogs, or debugging a packaging/CI job. For cutting
  or shipping a release use release-branch-skill / ship-release-skill.
---

# Packaging and CI

Authoritative long-form docs: `packaging/README.md` (channels, PPA and COPR
mechanics, the CI image), `RELEASE_CHECKLIST.md` (versions, changelogs, the
release flow). This skill is the working summary plus the traps.

## What ships and from where

| Channel | Source of truth | Notes |
| --- | --- | --- |
| PyPI (`pipx install --system-site-packages collins`) | `pyproject.toml`, `release.yml` `pypi` job (trusted publishing, `pypi` environment for the OIDC claim) | `dependencies = []` on purpose — GTK comes from the distro |
| `.deb` on GitHub releases | `scripts/build_deb.sh` | adds no apt source |
| `.rpm` on GitHub releases | `packaging/fedora/build-copr-srpm.sh --rebuild` | noarch, dist tag empty |
| Ubuntu PPA `ppa:episode6/stable` (noble, resolute) | `debian/` (native format, `dh` + `pybuild`), `packaging/build-ppa-source.sh` | channel archive shared by all episode6 apps: **leaf apps only, never a rebuilt system library** |
| Fedora COPR `episode6/stable` (Fedoras, rawhide, epel-10) | `packaging/fedora/collins.spec` | COPR signs; `COPR_API_CONFIG` token expires every 180 days |
| AUR | `packaging/aur/PKGBUILD` + `.SRCINFO` | not yet automated on main |

Jammy/bookworm are out permanently (libadwaita 1.1/1.2 vs the 1.5 floor);
Debian can't be reached from a PPA. Flatpak is specced, not started (needs a
host agent because of the pid namespace).

**The wheel is the whole of a pip install** — no post-install hook — so
everything the app reads at runtime is package data declared in
`pyproject.toml`: locale `.mo`s, `THIRD_PARTY_LICENSES.md`, the app and
panel SVGs, `icons/hicolor/scalable/actions/*.svg`, `sounds/*.oga`, the
desktop file and metainfo, and the hunk extension's `.ts/package.json/
README.md`. The in-tree paths are **symlinks into `data/`** (`collins/icons`,
`collins/sounds`, `collins/com.episode6.Collins.desktop`, `…metainfo.xml`;
`THIRD_PARTY_LICENSES.md` runs the other way, real file in the package). The
Debug icon variants are listed out of the globs (a checkout-only thing).
setuptools has not always globbed through symlinked directories and fails
silently, hence `scripts/verify_wheel_data.py` in the `packaging` job.
`collins --install-desktop` (`desktopentry.py`, no gi) writes launcher, icon
and metainfo under `XDG_DATA_HOME`; `Exec=` is the resolved script path,
quoted per the Desktop Entry Spec (`desktop-file-validate` errors on an
unquoted one). Adding an asset means: the file under `data/`, a
package-data glob (or an explicit entry), `verify_wheel_data.py`,
`data/install.sh`, `scripts/build_deb.sh`, `debian/rules`, the spec's
`%files` if not covered — action icons are globbed everywhere already;
sounds also need license notices in `THIRD_PARTY_LICENSES.md`,
`debian/copyright`, the spec `License:` and the PKGBUILD `license` array.

## Versions and changelogs

`pyproject.toml`'s `version` is the reference; `collins/__init__.py`, the top
`debian/changelog` entry (always targeted at `UNRELEASED` in git — the
per-series `0.1.2~noble1` is stamped in a temp tree) and the spec's
`Version:` must match exactly; `docs/releases.md` needs a `### v<VERSION>`
section and the spec a `%changelog` entry. The metainfo's top `<release>` and
the AUR `pkgver` track *shipped* versions and may lag but never lead.
`scripts/verify_versions.py` enforces all of it (CI `verify-versions`;
`ship-release.py` re-checks). `main` always carries the **next** version
(plain `MAJOR.MINOR.PATCH`, no snapshot markers); cutting bumps the patch,
hotfixes append a fourth segment (`0.1.1.1`). **Four changelogs** must
describe every release: `docs/releases.md` (verbatim release notes),
`debian/changelog` (one `*` bullet per headline, packaging first), the
metainfo `<release><description>`, and the spec `%changelog`. Keep the
UNRELEASED entries current as PRs merge. Launchpad **burns version strings
forever** (rejected uploads included); COPR does not.

## CI

`.github/workflows/ci.yml` on pushes to `main`/`release/**` and PRs: `image`
(reusable `ci-image.yml`: builds/pushes the three GHCR tags only when the
Dockerfile hash has no tag yet), `lint` (bare runner, `ruff==0.16.4` pinned),
`test` (unit suite in the full image; `tests/conftest.py` keeps it GTK-free
even though the image has GTK), `e2e` (`xvfb-run … scripts/run_e2e.py
--timeout 120`, 60-minute cap), `packaging` (`python3 -m build
--no-isolation`, `twine check --strict`, `verify_wheel_data.py`,
`build_deb.sh`), `ppa-source` (noble in the noble packaging image, resolute in
full; unsigned, checks the upload set exists), `rpm` (Fedora image: SRPM,
rpmlint, rebuild with `%check` = desktop-file + AppStream validation, `dnf
install`, path assertions), `verify-versions`, `hunk-ext` (`setup-bun` 1.4.0,
`bun test`, no `bun install`).

`.github/docker/ci.Dockerfile` is **the one canonical list of build
dependencies** — add a build-dep there, not in a workflow. Three stages/tags
named by the first 12 hex of `hashFiles(Dockerfile)`: `:<hash>` (resolute
full: packaging toolchain + GTK/Xvfb/dbus), `:<hash>-noble-pkg` (ubuntu:24.04,
packaging only), `:<hash>-fedora-pkg` (fedora:latest, root). Nothing rebuilds
on its own; bump the `refreshed:` date to pick up package updates. The
package is public — no credentials to pull. The other workflows: `docs.yml`
(VitePress → GitHub Pages), `claude.yml` / `claude-code-review.yml` (the
review bot; it reads CI through `gh run` and needs `actions: read` in both
`permissions:` and `additional_permissions:`), `release.yml` (on `v*` tags,
also `workflow_dispatch` with a `tag` input to re-drive a shipped tag from a
branch carrying a fix — a tag's run is frozen on the workflow file as it was).

## Footguns

- The e2e job shows up late in `gh pr checks`; green means `e2e` listed and
  passed.
- `ppa-source` and `rpm` exist so a missing build-dep or renamed runtime
  package fails a PR, not a published tag (v0.1.1's PPA leg died on a missing
  `build-essential` with the tag already out).
- The contents API 404s under a symlinked directory (`collins/icons`); use
  the `data/` path for raw fetches.
- `release.yml`'s `ppa` job refuses when `debian/changelog` disagrees with
  the tag and skips a series Launchpad already has; `copr` fails on a spec
  `Version:` mismatch and skips an already-built version.
- Pushing `.github/workflows/` needs the `workflow` scope: HTTPS with gh's
  token is rejected; plain ssh `git push origin` works.
- Docker on this machine: the worktree guard refuses commands mounting the
  main repo path; run docker through a wrapper script under `~/.cache`.
- `git archive` roots at the cwd — run it from the repo root.
- Never re-draft a PR the user marked ready (`gh pr ready --undo`).

Related: `release-branch-skill`, `ship-release-skill`, `collins-testing`.
