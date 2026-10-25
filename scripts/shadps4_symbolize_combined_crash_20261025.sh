#!/usr/bin/env bash
# Read-only, exact-binary symbolization of the 2026-10-25 combined shadPS4
# startup NULL read. Does not launch a game, alter Ollama, compile, or touch
# ES-DE, saves, SSH, the original working emulator, or shader caches.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
BIN="$STAGE/build/shadps4"
EXPECT="793a76912b4647677bc7c6c2b5087ae1f1b1c530c5664aaae623d689cdc6e847"
COMMIT="104cec069b7f53a69d9f8d9760d3adc5bcae892b"
RIP="0x7f2f7cb075c6"
ELF_ADDR="0x100f5c6"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
RUN="gow-native-crash-symbols-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$LOGS"
OUT="$LOGS/$RUN"
mkdir -m 700 "$OUT"
LOG="$OUT/symbolization.txt"
archive="$LOGS/$RUN.tar.gz"
{
    echo "STAGE=EXACT_PIE_CRASH_SYMBOLIZATION"
    echo "CRASH_RIP=$RIP"
    echo "CRASH_ELF_VADDR=$ELF_ADDR"
    echo "MAPPING=7f2f7c05c000-7f2f7d8d5000 r-xp 00564000"
    echo "ELF_BASE=0x7f2f7baf8000"
    echo "SOURCE_COMMIT=$COMMIT"
    echo "SHADPS4_SHA256_EXPECTED=$EXPECT"
    echo "BIN=$BIN"
} | tee "$LOG"
for cmd in sha256sum python3 git tar; do
    command -v "$cmd" >/dev/null || {
        echo "MISSING_REQUIRED_TOOL=$cmd" | tee -a "$LOG"; exit 3;
    }
done
[[ -f "$BIN" && ! -L "$BIN" ]] || {
    echo "CANDIDATE_ELF_MISSING_OR_SYMLINK" | tee -a "$LOG"; exit 3;
}
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$EXPECT" ]] || {
    echo "WRONG_CANDIDATE_ELF_SHA; refusing misleading address" | tee -a "$LOG"
    sha256sum "$BIN" | tee -a "$LOG"
    exit 3
}
[[ -f "$PROTECTED" && "$(sha256sum "$PROTECTED" | awk '{print $1}')" == "$PROTECTED_SHA" ]] || {
    echo "PROTECTED_GAMEPLAY_BINARY_MISMATCH" | tee -a "$LOG"; exit 3;
}
[[ "$(git -C "$STAGE/source" rev-parse HEAD)" == "$COMMIT" ]] || {
    echo "SOURCE_COMMIT_MISMATCH_NO_EDIT" | tee -a "$LOG"; exit 3;
}
echo "ORIGINAL_GOOD_GOW_ELF_UNCHANGED=YES" | tee -a "$LOG"

if command -v file >/dev/null; then
    { file "$BIN"; } > "$OUT/elf-file.txt" 2>&1 || true
fi
if command -v readelf >/dev/null; then
    { readelf -W -h "$BIN"; readelf -W -l "$BIN"; } > "$OUT/elf-segments.txt" 2>&1 || true
    { readelf -W -S "$BIN" | grep -E '(debug_info|debug_line|symtab|strtab|dynsym)' || true; } \
      > "$OUT/symbol-sections.txt" 2>&1
fi
if command -v addr2line >/dev/null; then
    addr2line -f -i -C -p -e "$BIN" \
      "0x100f5c6" "0x100f5c1" "0x100f5bb" "0x100f5b0" > "$OUT/addr2line.txt" 2>&1 || true
fi
if command -v llvm-symbolizer >/dev/null; then
    llvm-symbolizer --obj "$BIN" -f -i -C "0x100f5c6" "0x100f5c1" \
      > "$OUT/llvm-symbolizer.txt" 2>&1 || true
fi
if command -v gdb >/dev/null; then
    # Static symbol read only: NEVER attach to or resume the game.
    gdb --nx --quiet --batch \
      -ex 'set pagination off' \
      -ex "file $BIN" \
      -ex "info symbol $ELF_ADDR" \
      -ex "info line *$ELF_ADDR" \
      -ex 'info files' \
      -ex 'disassemble /r 0x100f480,0x100f680' \
      > "$OUT/gdb-static.txt" 2>&1 || true
fi
if command -v objdump >/dev/null; then
    objdump --demangle --disassemble \
      --start-address=0x100f480 --stop-address=0x100f680 "$BIN" \
      > "$OUT/objdump-disassembly.txt" 2>&1 || true
fi
if command -v nm >/dev/null; then
    # One symbol window around the crash; do not upload huge binary/symbol tables.
    python3 - "$BIN" "$ELF_ADDR" > "$OUT/nearest-symbols.txt" 2>&1 <<'PY'
from bisect import bisect_right
import subprocess, sys
binary=sys.argv[1]; target=int(sys.argv[2],0)
try:
    proc=subprocess.Popen(["nm","-n","-C","--defined-only",binary],text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
except OSError as e:
    print(type(e).__name__+": "+str(e)); raise SystemExit(0)
symbols=[]
for line in proc.stdout:
    cols=line.rstrip("\n").split(maxsplit=2)
    if len(cols)<3:
        continue
    try:
        addr=int(cols[0],16)
    except ValueError:
        continue
    if addr>=target+0x4000:
        break
    if addr<=target and target-addr<0x4000 or target<addr<target+0x500:
        symbols.append((addr,cols[1],cols[2]))
proc.stdout.close()
proc.terminate()
try: proc.wait(timeout=3)
except subprocess.TimeoutExpired: proc.kill()
prev=[x for x in symbols if x[0]<=target]
following=[x for x in symbols if x[0]>target]
for addr, typ, name in prev[-16:]+following[:8]:
    print(f"0x{addr:016x} {typ} {name} DISTANCE={target-addr:+#x}")
PY
fi
python3 - "$OUT" "$STAGE/source" <<'PY'
from pathlib import Path
import re,sys
out=Path(sys.argv[1]); source=Path(sys.argv[2]).resolve()
symbol=out/'addr2line.txt'
parts=[]
if symbol.is_file():
    text=symbol.read_text(errors='replace')
    parts.append(text)
    for filename,linenostr in re.findall(r'(/[^\s()]+?\.(?:cpp|cc|c|h)):(\d+)',text):
        p=Path(filename)
        if not p.is_file():
            continue
        try: p.resolve().relative_to(source)
        except ValueError: continue
        line=int(linenostr)
        if line<=0: continue
        lines=p.read_text(errors='replace').splitlines()
        if line>len(lines): continue
        low=max(1,line-18); high=min(len(lines),line+18)
        parts.append(f"\n=== {p}:{line} ===\n")
        parts.extend(f"{i+1:6d} {lines[i]}\n" for i in range(low-1,high))
(out/'source-context.txt').write_text(''.join(parts))
PY
for name in addr2line.txt nearest-symbols.txt source-context.txt; do
    [[ ! -f "$OUT/$name" ]] || {
        echo "=== $name ===" | tee -a "$LOG"
        head -70 "$OUT/$name" | tee -a "$LOG"
    }
done
{
    echo "RUN_ID=$RUN"
    echo "RESULT=READ_ONLY_SYMBOLIZATION_COMPLETED"
    echo "NO_GAME_EXECUTED=YES"
    echo "SHADPS4_COMPILED_ELF_PRESERVED=YES"
    echo "OLLAMA_AND_SAVES_UNCHANGED=YES"
} | tee -a "$LOG"
tar -czf "$archive" -C "$LOGS" "$RUN"
echo "UPLOAD_THIS=$archive"
