#!/bin/sh
# Build an .rpm:  scripts/build-rpm.sh  → dist/powerdeck-<version>-1.<dist>.noarch.rpm
# From this working tree (packaging/rpm/powerdeck.spec); without rpmbuild, in a Fedora container.
set -eu
umask 022
cd "$(dirname "$0")/.."
V=$(python3 -c "import re;print(re.search(r'version = \"(.+?)\"', open('pyproject.toml').read()).group(1))")
rm -rf dist/rpm && mkdir -p dist/rpm/SOURCES
tar --exclude=./.git --exclude=./dist --exclude=__pycache__ --exclude=./.pytest_cache \
    --transform "s,^\.,PowerDeck-$V," -czf "dist/rpm/SOURCES/PowerDeck-$V.tar.gz" .
build='rpmbuild -bb --quiet --define "_topdir $PWD/dist/rpm" packaging/rpm/powerdeck.spec && cp dist/rpm/RPMS/noarch/*.rpm dist/'
if command -v rpmbuild >/dev/null; then
    sh -c "$build"
else
    podman run --rm -v "$PWD:/src:Z" -w /src registry.fedoraproject.org/fedora:latest \
        sh -c "dnf -y -q install rpm-build python3-devel >/dev/null && $build"
fi
ls dist/powerdeck-"$V"-*.noarch.rpm
