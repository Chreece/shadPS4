#!/usr/bin/env bash
# Source-controlled PR117 GPU-indirect sizing acceptance. No game/window launch.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
BASE="$ROOT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$BASE/source"
BUILD="$BASE/build"
TAG="gds-pr117-gpu-indirect-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$ROOT/logs/$TAG"
STAGE=PRECHECK
STATUS=FAILED_OR_INCOMPLETE
mkdir -p "$OUT"
finish() {
    rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\nGAME_TEST_READY=NO\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' "$STATUS" "$STAGE" "$rc" >"$OUT/result.txt"
    tar -czf "$OUT.tar.gz" -C "$ROOT/logs" "$TAG" 2>"$OUT/archive-error.txt"
    echo "STATUS=$STATUS EXIT=$rc EVIDENCE=$OUT.tar.gz"
}
trap finish EXIT
for cmd in git cmake python3 g++ clang++-17 sha256sum tar flock; do
    command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; exit 2; }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo ISOLATED_BUILD_MISSING; exit 3;
}
git -C "$SRC" rev-parse HEAD >"$OUT/source-sha.txt"
git -C "$SRC" status --porcelain=v1 --untracked-files=no >"$OUT/source-status.txt"
[[ ! -s "$OUT/source-status.txt" ]] || { echo CHECKOUT_DIRTY_NOT_TOUCHING; exit 4; }
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
  "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo NORMAL_EXECUTION_GATE_MISSING; exit 5;
}
STAGE=PRODUCTION_SOURCE_AND_INDIRECT_GPU_ABI
(cd "$SRC" && python3 tests/check_ordered_gds_ir.py) >"$OUT/source-contract.log" 2>&1 || {
    tail -85 "$OUT/source-contract.log"; exit 6;
}
grep -Fq 'REAL_GPU_INDIRECT_DISPATCH_EXTENT_BEFORE_GDS_EPOCH_ALLOCATION' "$OUT/source-contract.log" || exit 7
FMT=""
for x in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    [[ -f "$x/fmt/base.h" ]] && { FMT="$x"; break; }
done
[[ -n "$FMT" ]] || { echo FMT_MISSING; exit 8; }
STAGE=DECODE_REAL_INDIRECT_GPU_COMMAND_OFFLINE
if [[ "${PR117_PRIOR_INDIRECT_ABI_VERIFIED:-0}" == 1 ]]; then
    # The uploaded 2026-10-30T191244Z archive already verified this exact
    # unchanged 12-byte C++ dispatch ABI with BOTH GCC and Clang. The failed
    # stage was the subsequent native Vulkan namespace compile, not the ABI.
    printf 'PRIOR_GCC_AND_CLANG_INDIRECT_ABI=PASS_FROM_191244_ARCHIVE\n' \
        >"$OUT/prior-abi-evidence.txt"
else
for compiler in g++ clang++-17; do
    case "$compiler" in g++) name=gcc;; *) name=clang;; esac
    (
      cd "$SRC"
      "$compiler" -std=c++23 -O2 -Wall -Wextra -Werror -pedantic -pthread \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT" tests/gcn_ordered_gds_indirect_dispatch.cpp \
        -o "$OUT/indirect-$name"
      "$OUT/indirect-$name"
    ) >"$OUT/indirect-$name.log" 2>&1 || {
        tail -85 "$OUT/indirect-$name.log"; exit 9;
    }
    grep -Fq 'GDS_GPU_INDIRECT_DISPATCH_ABI=PASS' "$OUT/indirect-$name.log" || exit 10
done
fi
STAGE=INCREMENTAL_PR117_NATIVE_LINK
exec 9>"$BASE/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
       >"$OUT/native-build.log" 2>&1; then
    tail -110 "$OUT/native-build.log"; exit 11
fi
[[ -s "$BUILD/shadps4" ]] || exit 12
sha256sum "$BUILD/shadps4" >"$OUT/elf.sha256"
STAGE=REAL_AMD_GPU_INDIRECT_TRANSFER
if command -v pkg-config >/dev/null && pkg-config --exists vulkan && \
   ! pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
    cat >"$OUT/vram.py" <<'PY'
import glob,json,pathlib,sys,time,urllib.request
cards=[]
for p in glob.glob('/sys/class/drm/card*/device/mem_info_vram_total'):
    f=pathlib.Path(p)
    try:
        total=int(f.read_text());used=int(f.with_name('mem_info_vram_used').read_text())
        if total>=10*1024**3:cards.append((total-used,f,total))
    except (ValueError,OSError):pass
if not cards:print('GPU_VRAM_NOT_MEASURABLE');sys.exit(1)
free,path,total=max(cards)
print(f'GPU_VRAM_FREE_BEFORE_GIB={free/1024**3:.3f}',flush=True)
if free<8*1024**3:
    # Conditional unload only, no Ollama restart, no network changes.
    try:
        response=urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=3)
        loaded=json.load(response).get('models',[])
        for model in loaded:
            name=model.get('name') or model.get('model')
            if not name: continue
            payload=json.dumps({'model':name,'keep_alive':0,'stream':False}).encode()
            req=urllib.request.Request('http://127.0.0.1:11434/api/generate',
                data=payload,headers={'Content-Type':'application/json'})
            urllib.request.urlopen(req,timeout=30).close()
            print(f'OLLAMA_CONDITIONALLY_UNLOADED={name}',flush=True)
    except Exception as exc:
        print(f'OLLAMA_CONDITIONAL_UNLOAD_UNAVAILABLE={type(exc).__name__}',flush=True)
    time.sleep(2)
    free=total-int(path.with_name('mem_info_vram_used').read_text())
print(f'GPU_VRAM_FREE_AFTER_GIB={free/1024**3:.3f}',flush=True)
sys.exit(0 if free>=8*1024**3 else 1)
PY
    if python3 "$OUT/vram.py" >"$OUT/vram.log" 2>&1; then
        (
          cd "$SRC"
          g++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic -pthread \
             -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
             "-I$SRC/externals/vulkan-headers/include" \
             tests/gcn_ordered_gds_indirect_gpu_smoke.cpp \
             -lvulkan -o "$OUT/gpu-indirect"
        ) >"$OUT/gpu-build.log" 2>&1 || { tail -80 "$OUT/gpu-build.log"; exit 13; }
        if ! timeout -k 3 20 "$OUT/gpu-indirect" >"$OUT/gpu.log" 2>&1; then
            cat "$OUT/gpu.log"; exit 14
        fi
        grep -Fq 'GDS_REAL_AMD_GPU_INDIRECT_READBACK=PASS' "$OUT/gpu.log" || exit 15
        GPU="PASS_ACTUAL_AMD_VULKAN_INDIRECT_TRANSFER"
    else
        GPU="SKIP_GPU_INSUFFICIENT_OR_UNKNOWN_VRAM"
    fi
else
    GPU="SKIP_GPU_VULKAN_UNAVAILABLE_OR_EMULATOR_RUNNING"
fi
printf 'GPU_PROBE=%s\n' "$GPU" >"$OUT/gpu-status.txt"
STAGE=DONE_SHADER_REENTRY_STILL_GATED
STATUS=PASS_INDIRECT_DIMENSIONS_AND_NATIVE
echo "GPU_PROBE=$GPU GAME_TEST_READY=NO"
