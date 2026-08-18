<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-21. Full change history: git log for this file.
-->
# AUR packaging

`PKGBUILD` and `.SRCINFO` for the [AUR](https://aur.archlinux.org/) package
[`collins`](https://aur.archlinux.org/packages/collins).

The AUR hosts recipes, not binaries: the package repo at
`ssh://aur@aur.archlinux.org/collins.git` holds just these two files, and each
user's machine (`makepkg` by hand, or a helper like `yay`/`paru`) fetches the
`v<pkgver>` tag tarball from GitHub and builds locally.

## Versioning

`pkgver` tracks the newest *released* version — the source URL points at a
real `v*` tag — so it may lag main's (next, unshipped) version but must never
run ahead. `scripts/verify_versions.py` enforces that in CI, along with
`PKGBUILD`/`.SRCINFO` agreeing on `pkgver`. The bump itself lands in the
release version-bump PRs (see
[../../RELEASE_CHECKLIST.md](../../RELEASE_CHECKLIST.md)).

`sha256sums` stays `SKIP` in git by design: the hash cannot exist before the
tag does. When changing any other PKGBUILD field, update `.SRCINFO` to match —
`makepkg --printsrcinfo` on an Arch box (keeping the notice header), or edit
the mirrored lines by hand.

## Automated publish on a tag

`.github/workflows/release.yml` has an `aur` job that runs on every `v*` tag
(normally pushed by `scripts/ship-release.py`, which creates the GitHub
release — and with it the tag tarball — before the tag reaches CI). In an
`archlinux` container it:

1. refuses to publish if `pkgver` disagrees with the tag;
2. downloads the tag tarball and fills the real hash into `sha256sums`;
3. regenerates `.SRCINFO` with `makepkg --printsrcinfo`, warning if the
   committed mirror has drifted from the PKGBUILD;
4. test-builds the package — pacman resolving every declared dependency
   catches a typo'd name, and makepkg re-verifies the tarball hash, before
   anything is published;
5. pushes `PKGBUILD` + `.SRCINFO` to the AUR repo. Re-running the workflow
   for an already-published tag finds nothing to commit and skips.

One required secret:

| Secret | Contents |
| --- | --- |
| `AUR_SSH_PRIVATE_KEY` | An OpenSSH private key whose public half is registered on the episode6 [AUR account](https://aur.archlinux.org/) (*My Account* → *SSH Public Key*) |

The job pins the AUR's ed25519 SSH host key next to where it is used; if the
AUR ever rotates it, refresh that line from `ssh-keyscan aur.archlinux.org`.

The very first publish needs no extra ceremony: there is no "register a
package" step on the AUR — cloning the not-yet-existing package's repo yields
an empty repo, and the first push creates the package.

## Manual publish (recovery / bootstrap)

One-time: an AUR account with your SSH public key added under *My Account*.

1. Fill in the hash for the released tag:
   ```bash
   curl -sL https://github.com/episode6/collins/archive/refs/tags/v<VER>.tar.gz | sha256sum
   ```
   Put it in `sha256sums=(...)` — but don't commit that; git keeps `SKIP`.
2. Regenerate `.SRCINFO` (on an Arch system): `makepkg --printsrcinfo > .SRCINFO`
   — or edit the `pkgver`/`source`/`sha256sums` lines by hand to match.
3. (Recommended) test the build on Arch: `makepkg -si`.
4. Push the two files:
   ```bash
   git clone ssh://aur@aur.archlinux.org/collins.git aur-collins
   cp PKGBUILD .SRCINFO aur-collins/
   cd aur-collins
   git add PKGBUILD .SRCINFO
   git commit -m "Update to v<VER>"
   git push
   ```

> If the package name `collins` turns out to be taken on the AUR, rename
> `pkgname`/`pkgbase` to `collins-gtk` and point the `aur` job's clone URL at
> the matching repo. Every other channel -- PyPI, the PPA, the `.deb` -- is
> plain `collins`.
