#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build"
QEMU_SRC="$BUILD/qemu"
QEMU_PREFIX="$BUILD/qemu-install"
FW="$DIR/test/firmware"

# -- system deps --
echo "Installing system deps..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git build-essential ninja-build pkg-config \
    libglib2.0-dev libpixman-1-dev libfdt-dev libslirp-dev \
    gcc-arm-none-eabi libnewlib-arm-none-eabi \
    device-tree-compiler python3-venv

# -- python deps --
echo "Installing python deps..."
pip install cocotb cocotb-bus cocotbext-axi

# -- build patched qemu --
if [ -x "$QEMU_PREFIX/bin/qemu-system-arm" ]; then
    echo "QEMU already built, skipping."
else
    echo "Building patched QEMU..."
    mkdir -p "$BUILD"

    if [ ! -d "$QEMU_SRC" ]; then
        git clone --depth 1 --branch v10.0.0 https://github.com/qemu/qemu.git "$QEMU_SRC"
    fi

    cd "$QEMU_SRC"
    git checkout -- .
    git apply "$(dirname "$DIR")/qemu-patch/qemu.patch"

    ./configure \
        --target-list=arm-softmmu \
        --prefix="$QEMU_PREFIX" \
        --enable-slirp \
        --disable-docs \
        --disable-werror
    make -j"$(nproc)"
    make install
    cd "$DIR"
fi

# -- compile PL device tree --
echo "Compiling PL device tree..."
mkdir -p "$FW"
dtc -I dts -O dtb -o "$FW/pl.dtb" "$FW/pl.dts"

# -- compile test firmware --
echo "Compiling test firmware..."
arm-none-eabi-gcc -mcpu=cortex-a9 -marm -O2 -nostdlib -ffreestanding \
    -T "$FW/link.ld" \
    "$FW/startup.s" "$FW/test_fw.c" \
    -o "$FW/test_fw.elf"

echo "Done. Run ./run.sh to start the co-simulation."
