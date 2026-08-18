<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-18. Full change history: git log for this file.
-->
# Packaging

How Collins is packaged for each channel. (The upstream project this fork is
based on publishes its own AUR/PPA/PyPI packages under the old
agent-session-manager name; the channels below are for this fork and only
exist once set up for it.)

| Channel | Files | Notes |
| --- | --- | --- |
| **`.deb`** (GitHub releases) | `scripts/build_deb.sh` | Hand-rolled binary deb attached to each release. |
| **AUR** | `packaging/aur/` | `PKGBUILD` + `.SRCINFO`; see `packaging/aur/README.md`. |
| **PyPI** | `pyproject.toml` + `.github/workflows/release.yml` | Auto-published on tag via trusted publishing (once configured). |
| **Ubuntu PPA** | `debian/` + `packaging/build-ppa-source.sh` | Source upload to Launchpad; see below. |

## Ubuntu PPA

PPA: **`ppa:episode6/stable`**
(<https://launchpad.net/~episode6/+archive/ubuntu/stable>)

The archive is named for the *channel*, not the app: it holds every episode6
Linux desktop application, so users add it once and `apt install` whatever they
want from it. Two consequences worth knowing before you upload anything to it:

- **Never put a rebuilt system library in it.** Everyone subscribed gets every
  package in the archive at elevated apt priority. Leaf applications are safe;
  a newer `libadwaita` would silently upgrade for every user of every episode6
  app. If that is ever needed, it gets its own archive.
- Version numbers can't collide between apps — Launchpad scopes version
  uniqueness to the source package name.

The `debian/` directory uses the **native** source format and builds with
`dh` + `pybuild`, installing the desktop entry, icons, and metainfo on top of
the wheel.

### Supported series

**noble** (24.04 LTS) and **resolute** (26.04 LTS). Jammy (22.04) is out of
scope permanently: it ships libadwaita 1.1 and GTK 4.6, and Collins uses
libadwaita APIs up to 1.5 across ~81 call sites plus `Gtk.FileDialog` /
`Gtk.FontDialog` (4.10) across ~24 more.

### The version scheme

`debian/changelog` stays at a plain version targeted at `UNRELEASED`, and the
per-series upload version is stamped by `build-ppa-source.sh` in its temp tree
only — never in git:

| | Version | Distribution |
| --- | --- | --- |
| in git | `0.1.0` | `UNRELEASED` |
| uploaded | `0.1.0~noble1` | `noble` |
| uploaded | `0.1.0~resolute1` | `resolute` |

`~` sorts *below* a plain `0.1.0`, so the unsuffixed version stays free. No
hyphens — the native format forbids them.

**Launchpad never permits reusing a version string for a source package**, across
all series, forever. A rejected upload or a failed build burns that version;
recover by bumping `--revision` (`0.1.0~noble2`), not by re-uploading. Keeping
git at `UNRELEASED` means an accidental `debuild -S` in a checkout produces
something Launchpad rejects rather than something that quietly burns a number.

### Releasing a new version

1. Bump the version in `pyproject.toml` / `__init__.py` as usual, and add a new
   top entry to `debian/changelog` — at the plain version, targeting
   `UNRELEASED`:
   ```bash
   dch -v <VER> -D UNRELEASED "New upstream release."   # or edit by hand
   ```
   Commit it.
2. Build a signed source package per series, from the committed HEAD:
   ```bash
   packaging/build-ppa-source.sh --series noble
   packaging/build-ppa-source.sh --series resolute

   dput ppa:episode6/stable \
     /tmp/collins-ppa/noble/collins_<VER>~noble1_source.changes
   dput ppa:episode6/stable \
     /tmp/collins-ppa/resolute/collins_<VER>~resolute1_source.changes
   ```
   Output is scoped per series (`/tmp/collins-ppa/<series>/`), so the two
   builds don't clobber each other and the upload order is up to you. Each run
   wipes only its own series directory. Pass `-k <keyid>` to pick a signing key
   explicitly; `--series` accepts only the supported series above, so a typo
   fails immediately rather than after an upload round trip.

   (Requires `debhelper dh-python pybuild-plugin-pyproject devscripts dput` and
   the signing key in the keyring.)

Launchpad emails an acceptance notice, then builds and publishes the `.deb`.
Expect minutes to hours in the build queue, plus ~20 minutes for the publisher.

### Automated uploads on a tag

`.github/workflows/release.yml` has a `ppa` job, matrixed over the supported
series, that does steps 1–2 above on every `v*` tag. It exports git HEAD itself
rather than consuming the `build` job's artifacts, but gates on that job so a
broken wheel stops the whole release.

Two required secrets:

| Secret | Contents |
| --- | --- |
| `PPA_GPG_KEY` | `gpg --export-secret-subkeys --armor '3EBBA2410EE1077E!'` — the trailing `!` matters, it stubs the primary out to `gnu-dummy` so a leaked runner secret cannot certify uids or mint subkeys |
| `GPG_PASS` | that key's passphrase |

Because a version string is burned in the archive forever, the job refuses
rather than guesses:

- It compares `debian/changelog` against the tag and **fails** if they differ —
  the upload version comes from the changelog, not the tag, so a forgotten
  changelog entry would otherwise publish the wrong version permanently.
- It asks Launchpad whether the version is already published and **skips** that
  series if so, which makes re-running a tag's workflow harmless instead of a
  confusing failure.

Signing is non-interactive without relying on `gpg-agent` caching: the job
writes a small wrapper that calls `gpg --batch --pinentry-mode loopback
--passphrase-file`, and passes it to `debuild` as `-p<wrapper>` alongside
`--prepend-path`, since `debuild` normalizes `PATH` and `debsign` takes the GPG
program by name. That is what the script's `--` passthrough is for.

### Testing before you upload

A `debuild` on a dev box proves very little — a source-only build does not
exercise the builder's dependency resolution, and the box is probably not
running the series you are targeting. Build in a real chroot for the target
series (`sbuild` or `pbuilder`) and run `lintian` on the resulting `.changes`
before the first upload of any series.

Verify afterwards in a clean container for that series:

```bash
add-apt-repository ppa:episode6/stable
apt install collins
dpkg -L collins        # desktop entry, scalable app icon, action icons, metainfo
```
