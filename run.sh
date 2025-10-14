#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
FW="$DIR/test/firmware"
QEMU="$DIR/build/qemu-install/bin/qemu-system-arm"
SOCK="/tmp/cocotemu.sock"

for f in "$QEMU" "$FW/pl.dtb" "$FW/test_fw.elf"; do
    [ -f "$f" ] || { echo "Missing: $f  (run ./setup.sh first)"; exit 1; }
done

rm -f "$SOCK"

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "${QEMU_PID:-}" ] && kill "$QEMU_PID" 2>/dev/null && wait "$QEMU_PID" 2>/dev/null
    [ -n "${SIM_PID:-}" ]  && kill "$SIM_PID"  2>/dev/null && wait "$SIM_PID"  2>/dev/null
    rm -f "$SOCK"
}
trap cleanup EXIT

# start verilator / cocotb (PL side)
echo "Starting PL sim..."
cd "$DIR/test"
COCOTEMU_SOCK="$SOCK" make MODULE=test_cosim 2>&1 | \
    stdbuf -oL sed 's/^/  [PL] /' &
SIM_PID=$!

# wait for the unix socket
echo "Waiting for cosim socket..."
for i in $(seq 1 60); do
    [ -S "$SOCK" ] && break
    sleep 0.5
done
[ -S "$SOCK" ] || { echo "Socket never appeared after 30s."; exit 1; }
echo "Socket ready."

# start QEMU (PS side)
echo "Starting QEMU..."
"$QEMU" \
    -M xilinx-zynq-a9,pl-dtb="$FW/pl.dtb" \
    -m 512M \
    -nographic \
    -chardev socket,id=cosim0,path="$SOCK",server=off \
    -device loader,file="$FW/test_fw.elf",cpu-num=0 \
    2>&1 | stdbuf -oL sed 's/^/  [PS] /' &
QEMU_PID=$!

echo "Co-simulation running."

# timeout after 30s
( sleep 30 && echo "" && echo "Timeout (30s)." && kill $$ 2>/dev/null ) &
TIMER_PID=$!

wait "$SIM_PID" 2>/dev/null || true
sleep 1
kill "$QEMU_PID" 2>/dev/null && wait "$QEMU_PID" 2>/dev/null
kill "$TIMER_PID" 2>/dev/null || true
