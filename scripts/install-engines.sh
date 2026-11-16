#!/bin/sh
# Installs the scan engines DiskSprawl uses besides GNU du: the static builds of ncdu and gdu,
# pinned by version and SHA-256. Used by the Dockerfile and by CI.
#
#   scripts/install-engines.sh DEST [amd64|arm64]
#
# The architecture defaults to this machine's. NCDU_BASE_URL and GDU_BASE_URL can point at a mirror;
# the checksums are verified either way.
set -eu

NCDU_VERSION=2.9.1
GDU_VERSION=5.39.0

dest=${1:?usage: install-engines.sh DEST [amd64|arm64]}
arch=${2:-$(uname -m)}
ncdu_base=${NCDU_BASE_URL:-https://dev.yorhel.nl/download}
gdu_base=${GDU_BASE_URL:-https://github.com/dundee/gdu/releases/download/v$GDU_VERSION}

case "$arch" in
    amd64 | x86_64)
        ncdu_file="ncdu-$NCDU_VERSION-linux-x86_64.tar.gz"
        ncdu_sha256=0c6c84b3f763c33baa051c8327f0e736f46f33a8cc061bb951d24517a8c5d73e
        gdu_file=gdu_linux_amd64_static.tgz
        gdu_binary=gdu_linux_amd64_static
        gdu_sha256=7e1036cba1696ecceda40ff79dacd43db9c157fa137ea5d50e4a198b047418a1
        ;;
    arm64 | aarch64)
        ncdu_file="ncdu-$NCDU_VERSION-linux-aarch64.tar.gz"
        ncdu_sha256=5bf4a37e8b5e1ecc6863475dc800c6a0d73b9cd1452e37d744edd3df0b58dac0
        gdu_file=gdu_linux_arm64.tgz
        gdu_binary=gdu_linux_arm64
        gdu_sha256=95900a16b9f9dad97f975b667644ddc073a1b377e95a0b4dfdf50045bf556ad0
        ;;
    *)
        echo "install-engines.sh: unsupported architecture $arch (amd64 or arm64)" >&2
        exit 1
        ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 --proto '=https' -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

fetch "$ncdu_base/$ncdu_file" "$work/ncdu.tar.gz"
echo "$ncdu_sha256  $work/ncdu.tar.gz" | sha256sum -c -
fetch "$gdu_base/$gdu_file" "$work/gdu.tgz"
echo "$gdu_sha256  $work/gdu.tgz" | sha256sum -c -

tar -xzf "$work/ncdu.tar.gz" -C "$work" ncdu
tar -xzf "$work/gdu.tgz" -C "$work" "$gdu_binary"
mkdir -p "$dest"
install -m 755 "$work/ncdu" "$dest/ncdu"
install -m 755 "$work/$gdu_binary" "$dest/gdu"
echo "Installed ncdu $NCDU_VERSION and gdu $GDU_VERSION ($arch) in $dest"
