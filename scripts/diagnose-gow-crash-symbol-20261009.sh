#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
binary="${SHADPS4_SYMBOL_BIN:-$HOME/Applications/shadps4/shadps4}"
expected_sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
fault_vma="0xf83686"
stamp="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d "$HOME/.gow-symbols-XXXXXX")"
archive="$HOME/shadps4-gow-symbols-$stamp.tar.gz"
finish() {
  rc=$?
  trap - EXIT
  set +e
  printf 'EXIT_CODE=%s\nBINARY=%s\nFAULT_VMA=%s\nSSH_SESSION=UNCHANGED\n' "$rc" "$binary" "$fault_vma" > "$work/result.txt"
  tar -czf "$archive" -C "$work" . || true
  rm -rf -- "$work"
  printf '\nREPORT=%s\nSSH_SESSION=UNCHANGED\n' "$archive"
}
trap finish EXIT
if [[ ! -f "$binary" ]]; then
  printf 'The installed emulator binary is missing: %s\n' "$binary" > "$work/verification.txt"
  exit 1
fi
for cmd in sha256sum readelf nm addr2line objdump python3; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd" >> "$work/verification.txt"; exit 1; }
done
actual_sha="$(sha256sum "$binary" | awk '{print $1}')"
printf 'EXPECTED_SHA256=%s\nACTUAL_SHA256=%s\n' "$expected_sha" "$actual_sha" > "$work/verification.txt"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo 'The executable has changed since the captured crash. Symbolization would be unreliable.' | tee -a "$work/verification.txt"
  exit 1
fi
readelf -hW "$binary" > "$work/elf-header.txt" 2>&1
readelf -lW "$binary" > "$work/elf-segments.txt" 2>&1
printf 'Fault ELF virtual address: %s\nTwo guest runs: RIP 0x7f17c692d686 / 0x7f26a792d686\n' "$fault_vma" > "$work/address-proof.txt"
for sym in addr2line llvm-addr2line-19 llvm-symbolizer-19 eu-addr2line; do
  if command -v "$sym" >/dev/null; then
    { echo "=== $sym ==="; "$sym" -C -f -i -e "$binary" "$fault_vma" 2>&1 || true; } >> "$work/symbolize.txt"
  fi
done
python3 - "$binary" "$fault_vma" > "$work/nearest-symbols.txt" 2>&1 <<'PY'
import collections, subprocess, sys
binary, target = sys.argv[1], int(sys.argv[2], 16)
previous = collections.deque(maxlen=12)
following = []
proc = subprocess.Popen(['nm', '-anC', '--defined-only', binary],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
for line in proc.stdout:
    fields = line.strip().split(maxsplit=2)
    if len(fields) < 3 or fields[1] not in {'t','T','w','W'}:
        continue
    try:
        address = int(fields[0], 16)
    except ValueError:
        continue
    symbol = (address, fields[2])
    if address <= target:
        previous.append(symbol)
    elif len(following) < 8:
        following.append(symbol)
proc.wait()
print('NM_EXIT_CODE', proc.returncode)
print('Nearest executable symbols around', hex(target))
for address, name in [*previous, *following]:
    print(f'{address:016x} delta={address-target:+#x} {name}')
if not previous and not following:
    print('No useful symbols; a matching debug-symbol build may be necessary.')
PY
objdump -dC --start-address=0xf835e0 --stop-address=0xf83700 "$binary" > "$work/fault-disassembly.txt" 2>&1 || true
if command -v gdb >/dev/null; then
  timeout 12s gdb -nx -q -batch -ex 'set print asm-demangle on' \
    -ex 'info symbol 0xf83686' -ex 'info files' "$binary" > "$work/gdb-symbol.txt" 2>&1 || true
fi
printf 'BIN_VERIFIED=YES\nFAULT_VMA=%s\n' "$fault_vma"
echo '--- Nearest symbol ---'
grep -E 'delta=\+?0x0|delta=-0x|delta=\+0x' "$work/nearest-symbols.txt" | tail -n 10 || true
printf 'NO_EMULATOR_RESTART_OR_MODIFICATIONS=YES\n'
