<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-28. Full change history: git log for this file.
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
| **Fedora COPR** | `packaging/fedora/` | SRPM upload to COPR, which builds for every Fedora and RHEL 10; see below. |

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

Releases ride the repo's release-branch flow — cut `release/v<VER>`, harden,
ship — described in [../RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md):
shipping pushes tag `v<VER>`, and the release workflow's `ppa` job (below)
uploads a source package per series automatically. The version bump and
`debian/changelog` entry land in the version-bump PRs when the branch is cut.
The steps below are the manual equivalent, for recovery or bootstrapping a new
series:

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

   (Requires `build-essential devscripts dput debhelper dh-python
   pybuild-plugin-pyproject python3-all` and the signing key in the keyring.)

Launchpad emails an acceptance notice, then builds and publishes the `.deb`.
Expect minutes to hours in the build queue, plus ~20 minutes for the publisher.

### Automated uploads on a tag

`.github/workflows/release.yml` has a `ppa` job, matrixed over the supported
series, that does step 2 above on every `v*` tag (normally pushed by
`scripts/ship-release.py` from a release branch). It exports git HEAD itself
rather than consuming the `build` job's artifacts, but gates on that job so a
broken wheel stops the whole release. Both jobs run inside the CI images
(below), so the release builds in exactly the environment every PR rehearsed.

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

CI (`.github/workflows/ci.yml`, job `ppa-source`) runs the same build unsigned
on every PR and push to `main`/`release/**`, so a missing build-dep shows up
there rather than on a published tag.

### The CI image

CI and release jobs that use image dependencies run inside prebuilt container
images built from `.github/docker/ci.Dockerfile` — the **canonical list of
build dependencies** for every channel (packaging tools, the gir stack for
e2e). Add a build-dep there, not in a workflow. One Dockerfile, three stages,
three tags under `ghcr.io/episode6/collins-ci`, all named by the first 12 hex
of the Dockerfile's hash:

- `:<hash>` — the **resolute full** image (~355 MB compressed): the packaging
  toolchain plus the GTK/Xvfb/dbus stack. Runs `test`, `packaging`,
  `ppa-source (resolute)` and `e2e`, and the release workflow's `build` and
  `ppa (resolute)`.
- `:<hash>-noble-pkg` — the **noble packaging-only** image (~200 MB
  compressed): the same packaging toolchain on `ubuntu:24.04`, no GTK. Runs
  only the noble source builds — `ppa-source (noble)` in CI, `ppa (noble)` at
  release — since a source build compiles nothing.
- `:<hash>-fedora-pkg` — the **Fedora packaging** image: `fedora:latest`
  with rpm-build, rpmlint, copr-cli, the spec's BuildRequires, and the
  package's runtime dependencies (so `dnf install` of the built RPM resolves
  without a download). Runs `rpm` in CI and `copr` at release. Its own
  `FROM`, not a stage of the Ubuntu chain, and root throughout — installing
  the RPM needs it, and no unit test runs there.

`.github/workflows/ci-image.yml` builds and pushes a tag only when it is
missing, so a PR that edits the Dockerfile tests on its own images and one
that doesn't builds nothing. A tag never rebuilds on its own; bump the
`refreshed:` date in the Dockerfile to pick up package updates. Tiny jobs with
no image dependencies (`lint`, `verify-versions`) stay on the bare runner —
ruff is a pinned 3-second pip install in `ci.yml`.

Note what this makes CI *mean*: tests, e2e and the wheel run on resolute (the
newest supported stack, the one dev machines run), and nothing routinely
exercises the noble floor except the noble source build. That is deliberate —
Launchpad still builds and installs the noble binary against noble's real
dependencies at release time; if a noble-only regression class ever appears,
the fix is a `test` matrix leg on a noble image, not a base flip.

The package is public, so `docker pull` needs no login (and the container
jobs pass no credentials). The full image reproduces the e2e job on any
machine with Docker, typelibs installed or not:

```sh
docker run --rm -it --init -v "$PWD:/src" -w /src ghcr.io/episode6/collins-ci:<tag> \
  xvfb-run -a -s "-screen 0 1920x1200x24" python3 scripts/run_e2e.py
```

(`<tag>` is in the image job's step summary of any recent run. `--init`
matters for the unit suite: without it the command is pid 1 and a proctree
test fails — Actions execs steps into a running container, so CI never sees
this.)

A tag's run is frozen on the workflow file as it was at that tag, so a fix to
the job cannot reach an already-shipped release by re-running it. Instead,
dispatch the workflow manually from the branch carrying the fix and name the
tag (`gh workflow run release.yml --ref <branch> -f tag=v<VERSION>`); the
`ppa` job checks out that tag and uploads from it. The images come from the
dispatching branch's Dockerfile, like the workflow file itself — so a fix to
the image reaches a shipped tag the same way. (With the tag's version already
in the archive, that dispatch is also a no-upload rehearsal of the whole job.)

Signing is non-interactive without relying on `gpg-agent` caching: the job
writes a small wrapper that calls `gpg --batch --pinentry-mode loopback
--passphrase-file`, and passes it to `debuild` as `-p<wrapper>` alongside
`--prepend-path`, since `debuild` normalizes `PATH` and `debsign` takes the GPG
program by name. That is what the script's `--` passthrough is for.

### Testing before you upload (PPA)

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

## Fedora COPR

COPR: **`episode6/stable`**
(<https://copr.fedorainfracloud.org/coprs/episode6/stable/>)

The PPA's twin on Fedora, named for the channel for the same reason and under
the same rule: **leaf applications only, never a rebuilt system library** —
everyone who enabled the repository gets every package in it. Users run
`dnf copr enable episode6/stable` once and install whatever they want from it.

### What COPR does and doesn't do

COPR builds **binary RPMs on its own builders from a source RPM** (the spec
plus the source tarball) in every chroot the project enables, and signs the
resulting repository itself — no GPG key on our side. The project is set to
*follow Fedora branching* (a new Fedora's chroots appear at branch time) with
the `x86_64` and `aarch64` chroots of every maintained Fedora, rawhide, and
`epel-10` (RHEL 10, AlmaLinux 10, Rocky 10, CentOS Stream 10: their base
repositories carry the whole GTK stack at or above Collins' floors, so the
same noarch SRPM builds there unchanged; RHEL 9 does not qualify). A noarch
package still needs both architectures' chroots: dnf puts `$basearch` in the
repository URL, so a package built only for x86_64 is invisible on ARM.

Unlike Launchpad, **COPR does not burn version strings**: a failed build can
be resubmitted, and a re-upload of the same version supersedes the previous
build. The release job still checks and skips rather than guesses, so that a
re-run stays boring.

### The files

- `packaging/fedora/collins.spec` — the RPM spec. Mirrors the AUR `PKGBUILD`
  (the wheel carries the action icons, so only the desktop entry, app icon
  and metainfo are installed on top of it) and `debian/control`'s
  dependency floors. `Version:` is the plain next version, bumped in the
  version-bump PRs with everything else, and `%changelog` gets one short
  entry per release in the same PRs; `scripts/verify_versions.py` checks
  both. `Release:` stays `1%{?dist}`; bump it to `2` only to re-upload a
  botched build of a shipped version, and reset it at the next version bump.
- `packaging/fedora/build-copr-srpm.sh` — the `build-ppa-source.sh` analog:
  exports git HEAD as `collins-<VER>.tar.gz`, runs `rpmbuild -bs`, and
  `rpmlint`s the spec and the SRPM into `/tmp/collins-copr/`. `--rebuild`
  goes on to build the binary RPM from that SRPM the way a COPR chroot
  would, which is what CI does.

### Releasing a new version

Shipping a release branch (see [../RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md))
pushes tag `v<VER>`, and the release workflow's `copr` job uploads the SRPM
and waits for every chroot to build. The manual equivalent, for recovery:

```bash
packaging/fedora/build-copr-srpm.sh
copr-cli build episode6/stable /tmp/collins-copr/collins-<VER>-1.src.rpm
```

Both need Fedora tooling (`dnf install rpm-build rpmlint copr-cli`, and
`~/.config/copr` from <https://copr.fedorainfracloud.org/api/> for the
upload). On another distro, run them in the CI image's Fedora stage:

```bash
docker run --rm -it -v "$PWD:/src" -w /src -v ~/.config/copr:/root/.config/copr:ro \
  ghcr.io/episode6/collins-ci:<tag>-fedora-pkg bash
```

### Automated uploads on a tag

`.github/workflows/release.yml` has a `copr` job that runs on every `v*`
tag beside `ppa`, in the Fedora stage of the CI image. It:

- **fails** if the spec's `Version:` disagrees with the tag (the SRPM's
  version is what COPR publishes);
- signs in with `copr-cli whoami` and **names the fix** when the token is
  rejected (below);
- **skips** the upload when COPR's latest succeeded build of `collins` is
  already this version, so a re-run of a tag's workflow is harmless;
- builds the SRPM and runs `copr-cli build` *without* `--nowait`, so a chroot
  that fails to build fails the job.

One secret:

| Secret | Contents |
| --- | --- |
| `COPR_API_CONFIG` | the whole `[copr-cli]` block from <https://copr.fedorainfracloud.org/api/>, verbatim |

**That token expires 180 days after it is minted** — the only secret in the
release pipeline that rots on a schedule. The `/api` page shows the expiry
date and writes it into the block as a comment; the job warns when fewer
than 30 days remain, and an expired token fails the sign-in step with the
rotation instructions. Put the date on a calendar when minting one. To
rotate: generate a new token on the `/api` page, replace the secret
(`gh secret set COPR_API_CONFIG --repo episode6/collins < ~/.config/copr`),
then dispatch the workflow for the tag that failed
(`gh workflow run release.yml --ref <branch> -f tag=v<VERSION>`).

CI (`.github/workflows/ci.yml`, job `rpm`) rehearses all of it short of the
upload on every PR: the SRPM, `rpmlint`, a rebuild into the binary RPM with
the spec's `%check` (desktop entry + AppStream validation), and a real
`dnf install` of the result — so a renamed dependency or a wrong path fails
the PR, not the first install after a release.

### Testing before you upload (COPR)

The `rpm` CI job is most of it. For the rest, once a build has published:

```bash
docker run --rm -it fedora:latest bash -c \
  'dnf -y copr enable episode6/stable && dnf -y install collins && rpm -ql collins'
```

and the same on `quay.io/centos/centos:stream10` for the EPEL 10 chroot.
