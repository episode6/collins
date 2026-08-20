<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-19. Full change history: git log for this file.
-->
# AUR packaging

`PKGBUILD` and `.SRCINFO` for the [AUR](https://aur.archlinux.org/) package
`collins`.

## Updating for a new release

The `pkgver` bump itself lands in the release version-bump PRs (see
[../../RELEASE_CHECKLIST.md](../../RELEASE_CHECKLIST.md)); `pkgver` tracks the
newest *released* version, since the source URL points at a real `v*` tag. The
hash refresh below can only happen after the release ships and the tag exists.

1. Bump `pkgver` in `PKGBUILD` and refresh the source hash:
   ```bash
   curl -sL https://github.com/episode6/collins/archive/refs/tags/v<VER>.tar.gz | sha256sum
   ```
   Put the hash in `sha256sums=(...)`.
2. Regenerate `.SRCINFO` (on an Arch system): `makepkg --printsrcinfo > .SRCINFO`
   — or edit the `pkgver`/`source`/`sha256sums` lines by hand to match.
3. (Recommended) test the build on Arch: `makepkg -si`.

## Publishing to the AUR

One-time: create an [AUR account](https://aur.archlinux.org/) and add your
SSH public key under *My Account*.

```bash
git clone ssh://aur@aur.archlinux.org/collins.git aur-csm
cp PKGBUILD .SRCINFO aur-csm/
cd aur-csm
git add PKGBUILD .SRCINFO
git commit -m "Update to v<VER>"
git push
```

> If the package name `collins` is already taken on the AUR, rename
> `pkgname`/`pkgbase` to `collins-gtk` and re-clone the matching AUR repo.
> Every other channel -- PyPI, the PPA, the `.deb` -- is plain `collins`.
