#!/bin/sh
# Build an Arch/Manjaro package:  scripts/build-arch.sh  → dist/powerdeck-<version>-1-any.pkg.tar.zst
# From this working tree (packaging/arch/PKGBUILD); without makepkg, in a Manjaro container.
set -eu
umask 022
cd "$(dirname "$0")/.."
V=$(python3 -c "import re;print(re.search(r'version = \"(.+?)\"', open('pyproject.toml').read()).group(1))")
rm -rf dist/arch && mkdir -p dist/arch
cp packaging/arch/PKGBUILD packaging/arch/powerdeck.install dist/arch/
tar --exclude=./.git --exclude=./dist --exclude=__pycache__ --exclude=./.pytest_cache \
    --transform "s,^\.,PowerDeck-$V," -czf "dist/arch/PowerDeck-$V.tar.gz" .
if command -v makepkg >/dev/null && [ "$(id -u)" -ne 0 ]; then
    (cd dist/arch && makepkg -fd --noconfirm >/dev/null) && cp dist/arch/powerdeck-*.pkg.tar.* dist/
else
    # makepkg refuses root: build as an unprivileged user inside the container
    podman run --rm -v "$PWD/dist:/dist:Z" docker.io/manjarolinux/base:latest sh -c '
        pacman -Sy --noconfirm -q --needed base-devel python >/dev/null 2>&1 &&
        useradd -m b && cp -r /dist/arch /home/b/arch && chown -R b /home/b/arch &&
        su b -c "cd ~/arch && makepkg -fd --noconfirm >/dev/null" && cp /home/b/arch/powerdeck-*.pkg.tar.* /dist/'
fi
ls dist/powerdeck-"$V"-*-any.pkg.tar.*
