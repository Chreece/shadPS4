#!/usr/bin/env bash
# Combined PR79 physical sparse packing + PR80 exact GCN 64-bit byte range.
# Compatibility overlay uses production helper directly on the old PR78 rasterizer,
# avoiding edits to widely-included resource.h and preserving all cached objects.
# Preserve last-good installs, saves, ES-DE, SSH and original experimental objects/ELF.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

PR78=bfe756faeec1231e51ae43728c20ce964d92c954
PR79=6273c4e1255e3b8e3643f4a523ffe81c556ccb11
PR80=1ebbab62fa1d7899f7875cca19bc45b6aeb15ade
BASE_CACHE_BLOB=e39ad2273b985a59ac6e0b61d49171a45037aa5c
BASE_RASTER_BLOB=6b5f5d1ab67f73760b509b42ecdaa6c50be247a9
PATCH_CACHE_BLOB=ae6a448f80d3d116b4e10a38983c7bbdfa77615c
PATCH_RASTER_BLOB=09c320ab9a6cb4c356cbcfd0b42524054d84fa40
PATCH_MATH_BLOB=32cafcb5b5afd45619d17d64538b5939b143fbf8
PATCH_MATH_TEST_BLOB=a1e60bbd695a62a367de2867aca216f13b066913
PATCH_HELPER_BLOB=5825150bb9f10dad0679fc59210edc2adbcb59fb
PATCH_TEST_BLOB=49a38eee89635024e3b7d6d7bdf3d287233b54cf
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
EXPERIMENT="$ROOT/experiments/mixed-types-82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104"
SRC="$EXPERIMENT/source"
BUILD="$EXPERIMENT/build"
BIN="$BUILD/shadps4"
GAME=/mnt/roms-all/ps4-games/CUSA11456/eboot.bin
LOGS="$HOME/projects/shadps4/logs"
OUT="$LOGS/pr80-wide-gcn-got-118s-$(date +%Y%m%d-%H%M%S)"
CACHE="$SRC/src/video_core/buffer_cache/buffer_cache.cpp"
RASTER="$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
HELPER="$SRC/src/video_core/buffer_cache/sparse_allocation_ranges.h"
MATH="$SRC/src/video_core/amdgpu/buffer_size.h"
OBJ_CACHE="$BUILD/CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o"
OBJ_RASTER="$BUILD/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
MUTATED=0
BACKUP=""
ORIGINAL_BIN_SHA=""
GAME_EXIT=""
CANDIDATE=""
VRAM_JSON="$OUT/info/ollama-unloaded.json"
STATUS=PREFLIGHT
mkdir -p "$OUT/info" "$OUT/input" "$LOGS"
die() { echo "STOP: $*" >&2; exit 2; }
stage() { echo "STAGE=$1" | tee -a "$OUT/info/progress.log"; }
restore_models() {
 [[ -s "$VRAM_JSON" ]] || return 0
 python3 - "$VRAM_JSON" <<'PY' || true
import json,sys,urllib.request
for item in json.load(open(sys.argv[1])):
    model=item.get("name") or item.get("model")
    if not model: continue
    opts={"model":model,"prompt":"","stream":False,"keep_alive":"5m"}
    if isinstance(item.get("context_length"),int) and item["context_length"]>0:
        opts["options"]={"num_ctx":item["context_length"]}
    try:
        req=urllib.request.Request("http://127.0.0.1:7869/api/generate",
            json.dumps(opts).encode(),{"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=60).read()
        print("OLLAMA_RESTORED="+model)
    except Exception as exc:
        print("OLLAMA_RESTORE_FAILED="+model+" "+str(exc),file=sys.stderr)
PY
}
finish() {
 local rc=$?
 trap - EXIT
 restore_models
 local restore="NOT_NEEDED"
 if (( MUTATED )); then
  restore=PASS
  cp -p -- "$BACKUP/buffer_cache.cpp" "$CACHE" || restore=FAILED
  cp -p -- "$BACKUP/vk_rasterizer.cpp" "$RASTER" || restore=FAILED
  rm -f -- "$HELPER" || restore=FAILED
   rm -f -- "$MATH" || restore=FAILED
  for obj in buffer_cache.cpp.o vk_rasterizer.cpp.o; do
   local dest="$OBJ_CACHE"
   [[ "$obj" == vk_rasterizer.cpp.o ]] && dest="$OBJ_RASTER"
   if [[ -f "$BACKUP/$obj" ]]; then
    cp -p -- "$BACKUP/$obj" "$dest" || restore=FAILED
   else
    rm -f -- "$dest" || restore=FAILED
   fi
  done
  cp -p -- "$BACKUP/shadps4" "$BIN" || restore=FAILED
  [[ "$(git -C "$SRC" hash-object "$CACHE")" == "$BASE_CACHE_BLOB" ]] || restore=FAILED
  [[ "$(git -C "$SRC" hash-object "$RASTER")" == "$BASE_RASTER_BLOB" ]] || restore=FAILED
  [[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$ORIGINAL_BIN_SHA" ]] || restore=FAILED
  [[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || restore=FAILED
 fi
 echo "RESTORE_STATE=$restore" | tee "$OUT/info/restore-status.txt"
 if [[ "$restore" != FAILED && -n "$BACKUP" && "$BACKUP" == "$EXPERIMENT/.pr80-preserve."* ]]; then
   rm -rf -- "$BACKUP"
 else
   [[ "$restore" != FAILED ]] || echo "ORIGINALS_BACKUP=$BACKUP" >> "$OUT/info/restore-status.txt"
 fi
 python3 - "$OUT" "$LOGS" "$STATUS" "$rc" "$GAME_EXIT" "$CANDIDATE" "$restore" <<'PY' || true
import json,sys
from pathlib import Path
folder,logs,status,exitcode,game_exit,candidate,restored=sys.argv[1:]
entry={"schema_version":2,"record_type":"LOCAL_NATIVE_FULL_LINK_GPU_TRIAL",
"game":"CUSA11456","base":"bfe756faeec1231e51ae43728c20ce964d92c954",
"generic_patch":"6273c4e1255e3b8e3643f4a523ffe81c556ccb11",
 "gcn_byte_length_patch":"1ebbab62fa1d7899f7875cca19bc45b6aeb15ade",
"status":status,"runner_exit_code":int(exitcode),
"game_exit_code":int(game_exit) if game_exit.isdigit() else None,
"binary":candidate,"restored":restored,"game_visual":"PENDING_UPLOADED_FRAMES_REVIEW"}
line=json.dumps(entry,separators=(",",":"))
Path(folder,"info","trial.jsonl").write_text(line+"\n")
with Path(logs,"emulator-trials-local.jsonl").open("a") as f:f.write(line+"\n")
PY
 [[ ! -f "$OUT/native-build.log" ]] || gzip -1 "$OUT/native-build.log" || true
 echo "$rc" > "$OUT/info/runner-exit-code.txt"
 tar -C "$OUT" -czf "$OUT.tar.gz" . || true
 printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
 if [[ "$restore" == FAILED ]]; then exit 11; fi
 exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for cmd in git curl cmake ninja c++ python3 timeout sha256sum tar gzip; do
 command -v "$cmd" >/dev/null 2>&1 || die "Missing dependency $cmd"
done
[[ -e "$SRC/.git" && -f "$SRC/CMakeLists.txt" && -f "$BUILD/build.ninja" ]] ||
 die "Exact isolated source/build tree not found"
[[ -x "$BIN" && -f "$GAME" ]] || die "Original emulator or game missing"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PR78" ]] || die "Unexpected source commit"
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || die "Dirty source; refusing patch"
[[ "$(git -C "$SRC" hash-object "$CACHE")" == "$BASE_CACHE_BLOB" ]] || die "Buffer cache blob mismatch"
[[ "$(git -C "$SRC" hash-object "$RASTER")" == "$BASE_RASTER_BLOB" ]] || die "Rasterizer blob mismatch"
[[ ! -e "$HELPER" && ! -e "$MATH" ]] || die "New helper already exists"
ORIGINAL_BIN_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
case "$ORIGINAL_BIN_SHA" in
  09091aa54dc27307b736bbc12c3c816a86f0450b6ada4f731eee2ef28b1d3c75|40045dfaa0b8b4c289ee9cc1ed5746287847e7ca3a6ecff6bc4f29205c277901) ;;
  *) die "Experimental ELF SHA changed: $ORIGINAL_BIN_SHA. Preserving without test";;
esac
printf 'BASE=%s\nPACKING_PATCH=%s\nGCN_64_PATCH=%s\nORIGINAL_BINARY=%s\n' \
 "$PR78" "$PR79" "$PR80" "$ORIGINAL_BIN_SHA" > "$OUT/info/source.txt"
echo "COMPATIBILITY=PR78 original flags preserved; only correct PR80 64-bit shared arithmetic in raw binder" >> "$OUT/info/source.txt"

stage FETCH_PINNED_PR80_SOURCE
while read -r path expected; do
 target="$OUT/input/$(basename "$path")"
 curl -4 --http1.1 -fLsS --retry 3 \
 "https://raw.githubusercontent.com/Chreece/shadPS4/$PR80/$path" -o "$target" ||
 die "Download failed for $path"
 [[ "$(git hash-object "$target")" == "$expected" ]] || die "Source blob mismatch: $path"
done <<EOF
src/video_core/buffer_cache/buffer_cache.cpp $PATCH_CACHE_BLOB
src/video_core/renderer_vulkan/vk_rasterizer.cpp $PATCH_RASTER_BLOB
src/video_core/buffer_cache/sparse_allocation_ranges.h $PATCH_HELPER_BLOB
tests/sparse_allocation_ranges_test.cpp $PATCH_TEST_BLOB
src/video_core/amdgpu/buffer_size.h $PATCH_MATH_BLOB
tests/gcn_buffer_size_64_test.cpp $PATCH_MATH_TEST_BLOB
EOF

stage VERIFY_COMPILED_PACKED_GAP_FIXTURE
# The test includes the real new helper by its repository-relative path.
# Compile before mutating the source tree by mirroring the include hierarchy.
mkdir -p "$OUT/test-include/video_core/buffer_cache"
cp -p -- "$OUT/input/sparse_allocation_ranges.h" \
  "$OUT/test-include/video_core/buffer_cache/sparse_allocation_ranges.h"
c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
 -I "$OUT/test-include" -I "$SRC/src" \
 "$OUT/input/sparse_allocation_ranges_test.cpp" \
 -o "$OUT/info/packed-gap-fixture" > "$OUT/info/fixture-build.log" 2>&1 || die "Fixture compiler failure"
"$OUT/info/packed-gap-fixture" > "$OUT/info/fixture-result.txt" 2>&1 || die "Packed sparse coverage failed"

stage VERIFY_COMPILED_REAL_GCN_64BIT_FIXTURE
mkdir -p "$OUT/test-include/video_core/amdgpu"
cp -p -- "$OUT/input/buffer_size.h" "$OUT/test-include/video_core/amdgpu/buffer_size.h"
c++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
 -I "$OUT/test-include" "$OUT/input/gcn_buffer_size_64_test.cpp" \
 -o "$OUT/info/gcn-u64-fixture" > "$OUT/info/gcn-u64-fixture-build.log" 2>&1 || die "GCN 64-bit fixture compiler failure"
"$OUT/info/gcn-u64-fixture" > "$OUT/info/gcn-u64-fixture-result.txt" 2>&1 ||
 die "GCN 64-bit descriptor math fixture failed"

stage GENERATE_TWO_MINIMAL_GENERIC_PATCHES
python3 - "$CACHE" "$RASTER" "$OUT/input/buffer_cache.cpp" \
 "$OUT/input/vk_rasterizer.cpp" "$OUT/input" <<'PY'
from pathlib import Path
import sys
old_cache,old_raster,patch_cache,patch_raster,output=(Path(x) for x in sys.argv[1:])
def method(text):
 a=text.index("void BufferCache::EnsureResident(")
 b=text.index("\nbool BufferCache::SynchronizeMemory(",a)
 return a,b,text[a:b]
base_cache=old_cache.read_text()
a,b,_=method(base_cache)
_,_,implementation=method(patch_cache.read_text())
assert 'packed_blocks == resident_blocks' in implementation
assert 'SparseBlocksPerAllocation(' in implementation
cache=base_cache[:a]+implementation+base_cache[b:]
anchor='#include "video_core/buffer_cache/buffer_cache.h"\n'
assert cache.count(anchor)==1
cache=cache.replace(anchor,anchor+'#include "video_core/buffer_cache/sparse_allocation_ranges.h"\n',1)
if "#include <vector>\n" not in cache:
 assert "#include <algorithm>\n" in cache
 cache=cache.replace("#include <algorithm>\n","#include <algorithm>\n#include <vector>\n",1)
output.joinpath("composed-buffer_cache.cpp").write_text(cache)

# PR80's actual main-branch method calls Buffer::GetSize64(), which is absent
# in PR78's old resource.h. In this isolated test use the EXACT same committed
# AmdGpu::BufferSize::DeclaredBytes helper directly in the one observed Vulkan
# buffer binder rather than modifying resource.h and rebuilding unrelated TUs.
pr=patch_raster.read_text()
pr_start=pr.index("            const auto vsharp = desc.GetSharp(stage);")
pr_end=pr.index("                const auto [buffer, offset] = buffer_cache.ObtainBuffer(",pr_start)
prologue=pr[pr_start:pr_end]
assert prologue.count("vsharp.GetSize64()")==1
assert "GuestBufferMappedRange:" in prologue
assert "AmdGpu::BufferSize::FitsU32(size)" in prologue
prologue=prologue.replace("vsharp.GetSize64()",
 "AmdGpu::BufferSize::DeclaredBytes(vsharp.stride, vsharp.num_records)",1)

raster=old_raster.read_text()
orig_start=raster.index("            const auto vsharp = desc.GetSharp(stage);")
orig_flags=raster.index("                VideoCore::ObtainBufferFlags flags = {};",orig_start)
orig_binding=raster.index("                const auto [buffer, offset] =",orig_flags)
orig_tail=raster.index("                const u64 offset_aligned = Common::AlignDown(offset, alignment);",orig_binding)
flag_implementation=raster[orig_flags:orig_binding]
assert "IsWritten" in flag_implementation and "IsTexelBuffer" in flag_implementation
bind=(
"                const auto [buffer, offset] =\n"
"                    buffer_cache.ObtainBuffer(vsharp.base_address,\n"
"                                              static_cast<u32>(size), flags);\n"
)
raster=raster[:orig_start]+prologue+flag_implementation+bind+raster[orig_tail:]
inc='#include "video_core/amdgpu/liverpool.h"\n'
assert raster.count(inc)==1
raster=raster.replace(inc,inc+'#include "video_core/amdgpu/buffer_size.h"\n',1)
assert "AmdGpu::BufferSize::DeclaredBytes" in raster
assert "GuestBufferMappedRange:" in raster
assert "BufferSize::FitsU32(size)" in raster
output.joinpath("composed-vk_rasterizer.cpp").write_text(raster)
PY

BACKUP="$(mktemp -d "$EXPERIMENT/.pr80-preserve.XXXXXXXX")"
cp -p -- "$CACHE" "$BACKUP/buffer_cache.cpp"
cp -p -- "$RASTER" "$BACKUP/vk_rasterizer.cpp"
cp -p -- "$BIN" "$BACKUP/shadps4"
[[ ! -f "$OBJ_CACHE" ]] || cp -p -- "$OBJ_CACHE" "$BACKUP/buffer_cache.cpp.o"
[[ ! -f "$OBJ_RASTER" ]] || cp -p -- "$OBJ_RASTER" "$BACKUP/vk_rasterizer.cpp.o"
MUTATED=1
cp -- "$OUT/input/composed-buffer_cache.cpp" "$CACHE"
cp -- "$OUT/input/composed-vk_rasterizer.cpp" "$RASTER"
cp -- "$OUT/input/sparse_allocation_ranges.h" "$HELPER"
cp -- "$OUT/input/buffer_size.h" "$MATH"

stage FULL_NATIVE_CMAKE_NINJA_LINK
STATUS=COMPILING_FULL_NATIVE
cmake --build "$BUILD" --parallel 8 -- -k 0 > "$OUT/native-build.log" 2>&1 || {
 grep -nE 'error:|fatal error:|FAILED:|undefined reference|ninja: build stopped' "$OUT/native-build.log" |
 tail -n 90 >&2 || true
 STATUS=BUILD_FAILED
 exit 7
}
[[ -x "$BIN" ]] || { STATUS=LINK_PASSED_BINARY_MISSING; exit 7; }
STATUS=NATIVE_FULL_LINK_PASS
sha256sum "$BIN" > "$OUT/info/new-binary-sha256.txt"
grep -nE 'warning:|error:|FAILED:' "$OUT/native-build.log" > "$OUT/info/build-warnings.txt" || true

CANDIDATE="$ROOT/candidates/pr80-on-pr78plus79-$PR80-$(date +%Y%m%d-%H%M%S)/shadps4"
mkdir -p "$(dirname "$CANDIDATE")"
cp -p -- "$BIN" "$CANDIDATE"
[[ "$(sha256sum "$CANDIDATE" | awk '{print $1}')" == \
   "$(sha256sum "$BIN" | awk '{print $1}')" ]] || { STATUS=CANDIDATE_COPY_FAILED; exit 7; }
echo "CANDIDATE=$CANDIDATE" >> "$OUT/info/source.txt"

stage CHECK_8_GIB_GPU_HEADROOM
VRAM=""
for gpu in /sys/class/drm/card[0-9]*/device; do
 [[ -r "$gpu/vendor" ]] || continue
 [[ "$(<"$gpu/vendor")" == 0x1002 ]] || continue
 [[ -r "$gpu/mem_info_vram_total" && -r "$gpu/mem_info_vram_used" ]] || continue
 VRAM="$gpu"; break
done
[[ -n "$VRAM" ]] || { STATUS=VRAM_UNVERIFIED; exit 5; }
FREE_BYTES=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
echo "VRAM_FREE_BEFORE=$FREE_BYTES" > "$OUT/info/vram.txt"
if (( FREE_BYTES < 8589934592 )); then
 python3 - "$VRAM_JSON" <<'PY' || { STATUS=OLLAMA_UNLOAD_ERROR; exit 5; }
import json,sys,urllib.request
url="http://127.0.0.1:7869"
models=json.load(urllib.request.urlopen(url+"/api/ps",timeout=8)).get("models",[])
with open(sys.argv[1],"w") as file:json.dump(models,file)
for model in models:
 name=model.get("name") or model.get("model")
 if not name:continue
 data=json.dumps({"model":name,"prompt":"","stream":False,"keep_alive":0}).encode()
 req=urllib.request.Request(url+"/api/generate",data,{"Content-Type":"application/json"})
 urllib.request.urlopen(req,timeout=30).read()
 print("UNLOADED_OLLAMA="+name)
PY
 sleep 2
 FREE_BYTES=$(( $(cat "$VRAM/mem_info_vram_total") - $(cat "$VRAM/mem_info_vram_used") ))
 echo "VRAM_FREE_AFTER=$FREE_BYTES" >> "$OUT/info/vram.txt"
fi
(( FREE_BYTES >= 8589934592 )) || { STATUS=VRAM_UNDER_8_GIB_NO_LAUNCH; exit 5; }

if [[ -z "$(printenv DISPLAY || true)" && -d /tmp/.X11-unix ]]; then
 display="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sed 's/^X//' | sort -V | head -1)"
 [[ -z "$display" ]] || export DISPLAY=":$display"
fi

cat > "$OUT/info/filter.py" <<'PY'
import sys,gzip,re,json
from pathlib import Path
from collections import Counter,deque
root=Path(sys.argv[1])
patterns={"wide_vsharp":"GuestBufferRange:",
"mapped":"GuestBufferMappedRange:","clamp":"Clamped size from","typed":"GuestIndexedImageDimensions:",
"sampler":"IndexedUniformSamplerBound:","binding":"IndexedBindingDecision:",
"srt":"SRT dependency resolution stalled","device_loss":"DEVICE_LOST",
"radv_soft_recovery":"context is lost","crash":"<Critical>",
"sparse":"Making range start="}
counts=Counter();shown=Counter();tail=deque(maxlen=100)
with gzip.open(root/"game-console.log.gz","wt",compresslevel=1,encoding="utf-8") as full, \
 (root/"shader-events.txt").open("w") as events:
 for line in sys.stdin:
  full.write(line);tail.append(line[-600:])
  for name,needle in patterns.items():
   if needle in line:
    counts[name]+=1
    if shown[name]<48 and events.tell()<300000:
     events.write("["+name+"] "+line[:1200])
     shown[name]+=1
    break
(root/"info"/"runtime-counters.json").write_text(json.dumps(counts,indent=2)+"\n")
(root/"console-tail.txt").write_text("".join(tail))
print("RUNTIME_COUNTERS="+json.dumps(counts),flush=True)
PY

stage GAME_118S_TERM_PLUS_2S_KILL
STATUS=GPU_RUN_STARTED
date --iso-8601=seconds > "$OUT/info/game-start-time.txt"
(
 set -o pipefail
 timeout -s TERM -k 2s 118s "$CANDIDATE" \
     --game "$GAME" --fullscreen true --show-fps 2>&1 |
    python3 -u "$OUT/info/filter.py" "$OUT"
) &
JOB=$!
previous=0
for second in 40 55 85 112; do
 sleep "$((second-previous))"
 previous="$second"
 kill -0 "$JOB" 2>/dev/null || break
 if command -v scrot >/dev/null; then
  scrot "$OUT/menu-$second.png" >/dev/null 2>&1 || true
 elif command -v import >/dev/null; then
  import -window root "$OUT/menu-$second.png" >/dev/null 2>&1 || true
 fi
done
code=0
wait "$JOB" || code=$?
GAME_EXIT="$code"
STATUS=GPU_RUN_CAPTURED
echo "GAME_EXIT=$code" > "$OUT/info/game-exit-code.txt"
if command -v journalctl >/dev/null; then
 journalctl -k --since "$(cat "$OUT/info/game-start-time.txt")" --no-pager 2>/dev/null |
   grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
   tail -n 120 > "$OUT/info/gpu-kernel.txt" || true
fi
python3 - "$OUT" <<'PY'
from pathlib import Path
import sys,hashlib
root=Path(sys.argv[1])
shots=[]
for x in (40,55,85,112):
 p=root/f"menu-{x}.png"
 if p.exists():
  shots.append(f"{x} {p.stat().st_size} {hashlib.sha256(p.read_bytes()).hexdigest()}")
(root/"info"/"screenshots.txt").write_text("\n".join(shots)+"\n")
PY
if [[ "$code" == 124 || "$code" == 0 ]]; then
 STATUS=GPU_GAME_EXIT_NONFATAL_VISUAL_REVIEW_REQUIRED
 echo "VISUAL_ACCEPTANCE=NEEDS_SCREEN_REVIEW"
else
 STATUS=GPU_GAME_CRASH
 echo "GAME_CRASH_EXIT=$code"
 exit 10
fi
