#!/usr/bin/env bash
# Safe native C++ build / bounded GoT test for the genuine 2D/3D/Cube shader path.
# Uses a separate Git worktree; never replaces ES-DE, the previous binary or saves.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
BASE="$HOME/projects/shadps4/GhostOfTsushima/fresh-source/got-indexed-capacity-20261024-213908"
PIN="404dc672286deb016c1438fbe9e822790ba983db"
PREVIOUS_PIN="82cc0cdd2e996cc747106b0a0bb9a6ebc5f4a104"
LAST_TESTED_PIN="52350d53f6613fbfe3b70378a35174f02525b8d4"
# Reuse the previous isolated worktree and ~2535 Ninja objects / FFmpeg cache.
# Original game source, installed game binaries, saves and SSH are untouched.
ROOT="$HOME/projects/shadps4/GhostOfTsushima/experiments/mixed-types-$PREVIOUS_PIN"
SOURCE="$ROOT/source"
BUILD="$ROOT/build"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
CAPTURE="$HOME/projects/shadps4/logs/got-mixed-types-120s-$(date +%Y%m%d-%H%M%S)"
VRAM_JSON="$CAPTURE/info/ollama-unloaded.json"
mkdir -p "$CAPTURE/info" "$ROOT"
stage() { printf 'STAGE=%s\n' "$1" | tee -a "$CAPTURE/info/progress.log"; }
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
restore_models() {
  [[ -f "$VRAM_JSON" ]] || return 0
  python3 - "$VRAM_JSON" <<'PY' || true
import json,sys,urllib.request
data=json.load(open(sys.argv[1],encoding='utf-8'))
for model in data:
 name=model.get('name') or model.get('model')
 if not name:continue
 settings={"model":name,"prompt":"","stream":False,"keep_alive":"5m"}
 if isinstance(model.get("context_length"),int) and model["context_length"]>0:
  settings["options"]={"num_ctx":model["context_length"]}
 try:
  req=urllib.request.Request("http://127.0.0.1:7869/api/generate",
   json.dumps(settings).encode(),{"Content-Type":"application/json"})
  urllib.request.urlopen(req,timeout=60).read()
  print("OLLAMA_RESTORED="+name,flush=True)
 except Exception as e: print("OLLAMA_RESTORE_ERROR="+name+" "+str(e),file=sys.stderr)
PY
}
finish() {
  local status=$?
  trap - EXIT
  restore_models
  printf '%s\n' "$status" > "$CAPTURE/info/runner-exit-code.txt"
  local suffix=""
  if (( status != 0 )); then suffix="-failure"; fi
  local archive="$CAPTURE$suffix.tar.gz"
  tar -C "$CAPTURE" -czf "$archive" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$archive"
  exit "$status"
}
trap finish EXIT
[[ -e "$BASE/.git" && -f "$BASE/CMakeLists.txt" ]] || fail "Original source missing; nothing changed"
[[ -f "$GAME" ]] || fail "Game executable not found; nothing launched"
for app in git cmake ninja timeout sha256sum python3; do
 command -v "$app" >/dev/null 2>&1 || fail "Missing $app"
done
stage FETCH_VERIFIED_SOURCE
git -C "$BASE" -c http.version=HTTP/1.1 fetch --no-tags \
 "https://github.com/Chreece/shadPS4.git" \
 refs/heads/feature/generic-mixed-indexed-2d-3d-cube-20261026 \
 > "$CAPTURE/info/git-fetch.log" 2>&1 || {
  tail -n 60 "$CAPTURE/info/git-fetch.log" >&2; exit 3;
 }
git -C "$BASE" merge-base --is-ancestor "$PIN" FETCH_HEAD ||
 fail "Reviewed typed-image source is not an ancestor of fetched branch"
if [[ -e "$SOURCE" ]]; then
 [[ -d "$SOURCE" && -e "$SOURCE/.git" ]] ||
  fail "Experiment path in use by another project"
 CURRENT_PIN="$(git -C "$SOURCE" rev-parse HEAD)"
 [[ "$CURRENT_PIN" == "$PIN" || "$CURRENT_PIN" == "$PREVIOUS_PIN" ||
    "$CURRENT_PIN" == "$LAST_TESTED_PIN" ]] ||
  fail "Unexpected experiment HEAD $CURRENT_PIN; preserving this separate worktree"
 [[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]] ||
  fail "Existing experiment has uncommitted changes - preserving"
 if [[ "$CURRENT_PIN" != "$PIN" ]]; then
  stage FAST_FORWARD_EXISTING_ISOLATED_WORKTREE
  git -C "$SOURCE" checkout --detach "$PIN" \
    > "$CAPTURE/info/verified-worktree-update.log" 2>&1 || {
    cat "$CAPTURE/info/verified-worktree-update.log" >&2
    exit 3
  }
  echo "INCREMENTAL_CMAKE_BUILD_CACHE=REUSED" >> "$CAPTURE/info/source.txt"
 fi
else
 git -C "$BASE" worktree add --detach "$SOURCE" "$PIN" \
 > "$CAPTURE/info/worktree.log" 2>&1 || {
  cat "$CAPTURE/info/worktree.log" >&2;exit 3;
 }
fi
printf 'SOURCE=%s\nCOMMIT=%s\n' "$SOURCE" "$PIN" > "$CAPTURE/info/source.txt"
stage PREPARE_DEPENDENCIES
git -C "$SOURCE" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 8 \
 > "$CAPTURE/info/submodules.log" 2>&1 || {
  tail -n 80 "$CAPTURE/info/submodules.log" >&2; exit 4;
 }
stage VERIFY_TYPED_SHADER_AND_FRONTEND
grep -Fq 'GuestIndexedImageDimensions:' \
 "$SOURCE/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ||
 fail "Mixed dimension bindings absent"
grep -Fq 'GpuDescriptorTableCpuCoherence:' \
 "$SOURCE/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ||
 fail "Corrected buffer-table coherence absent"
grep -Fq 'image_id_3d' \
 "$SOURCE/src/shader_recompiler/backend/spirv/emit_spirv_image.cpp" ||
 fail "Typed SPIR-V sampling path absent"
grep -Fq 'u32 NumBindings(const auto&) const noexcept' \
 "$SOURCE/src/shader_recompiler/resource.h" ||
 fail "SamplerResource::NumBindings native compiler repair missing"
grep -Fq 'max_guest_mixed_indexed_image_entries' \
 "$SOURCE/src/shader_recompiler/profile.h" ||
 fail "Capacity-safe fallback profile fields absent"
grep -Fq 'is_guest_mixed_indexed' \
 "$SOURCE/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ||
 fail "Capacity-safe typed binding routing absent"
grep -Fq 'IsSplashVisible()' \
 "$SOURCE/src/video_core/renderer_vulkan/vk_presenter.cpp" ||
 fail "Loading screen missing"
grep -Fq 'SDL_GAMEPAD_BUTTON_GUIDE' \
 "$SOURCE/src/core/devtools/quit_dialog.h" ||
 fail "Gamepad exit missing"
grep -Fq 'HOST_QUIT action=confirmed' \
 "$SOURCE/src/core/devtools/layer.cpp" ||
 fail "Host quit confirmation missing"
stage PREFETCH_VERIFIED_FFMPEG
# The exact FFmpeg-core submodule commit has a published Linux x64 asset.
# CMake's file(DOWNLOAD) may fail on IPv4/proxy systems even when the
# artifact exists. Prepopulate the isolated build cache; do not modify
# system dependencies or use a binary from any other commit.
FFMPEG_COMMIT="94dde08c8a9e4271a93a2a7e4159e9fb05d30c0a"
FFMPEG_TAG="94dde08"
FFMPEG_ASSET_SHA256="aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd"
[[ "$(git -C "$SOURCE/externals/ffmpeg-core" rev-parse HEAD)" == "$FFMPEG_COMMIT" ]] ||
 fail "Unknown FFmpeg-core revision; refusing a mismatched binary"
mkdir -p "$BUILD/externals"
FFMPEG_ZIP="$BUILD/externals/ffmpeg-$FFMPEG_TAG.zip"
valid_zip() {
  [[ -s "$1" ]] || return 1
  [[ "$(sha256sum "$1" | awk '{print $1}')" == "$FFMPEG_ASSET_SHA256" ]] || return 1
  cmake -E tar tf "$1" > "$CAPTURE/info/ffmpeg-archive-contents.txt" 2>&1 || return 1
  grep -q 'libavcodec\.a' "$CAPTURE/info/ffmpeg-archive-contents.txt" &&
  grep -q 'libavformat\.a' "$CAPTURE/info/ffmpeg-archive-contents.txt"
}
if [[ -e "$FFMPEG_ZIP" ]] && ! valid_zip "$FFMPEG_ZIP"; then
 fail "Existing FFmpeg cache has wrong SHA256; preserving it for review"
fi
if [[ ! -f "$FFMPEG_ZIP" ]]; then
  stage CHECK_VERIFIED_LOCAL_FFMPEG_CACHE
  while IFS= read -r candidate; do
    [[ "$candidate" == "$FFMPEG_ZIP" ]] && continue
    if valid_zip "$candidate"; then
      cp -p -- "$candidate" "$FFMPEG_ZIP"
      echo "FFMPEG_CACHE_REUSED_FROM=$candidate" >> "$CAPTURE/info/ffmpeg-source.txt"
      break
    fi
  done < <(find "$HOME/projects/shadps4" -type f -name "ffmpeg-$FFMPEG_TAG.zip" -print 2>/dev/null)
fi
if [[ ! -f "$FFMPEG_ZIP" ]]; then
  stage DOWNLOAD_PINNED_FFMPEG_ASSET
  command -v curl >/dev/null || fail "curl needed for verified FFmpeg release asset"
  TMP_FFMPEG="$(mktemp "$BUILD/externals/ffmpeg-download-XXXXXXXX.zip")"
  rm -f -- "$TMP_FFMPEG"
  for url in \
    "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip" \
    "https://api.github.com/repos/shadps4-emu/ext-ffmpeg-core/releases/assets/327924109"; do
    echo "FFMPEG_ATTEMPT=$url" >> "$CAPTURE/info/ffmpeg-source.txt"
    if curl -4 --http1.1 -fLsS --retry 3 --retry-all-errors \
       --connect-timeout 15 --max-time 240 \
       -H "Accept: application/octet-stream" \
       -H "User-Agent: shadps4-generic-build" \
       "$url" -o "$TMP_FFMPEG" \
       2>> "$CAPTURE/info/ffmpeg-download-errors.txt" &&
       valid_zip "$TMP_FFMPEG"; then
      mv -- "$TMP_FFMPEG" "$FFMPEG_ZIP"
      break
    fi
    rm -f -- "$TMP_FFMPEG"
  done
  [[ -e "$FFMPEG_ZIP" ]] ||
    fail "Official FFmpeg asset unavailable from both endpoints; no unverified ZIP installed"
fi
valid_zip "$FFMPEG_ZIP" || fail "FFmpeg archive checksum/contents failed"
echo "FFMPEG_RELEASE_ASSET_SHA256=$FFMPEG_ASSET_SHA256" | tee -a "$CAPTURE/info/progress.log"

stage PREFLIGHT_VULKAN_FEATURE_DECLARATION_ORDER
# Verify the actual C++23 feature designators extracted from vk_instance.cpp
# against *VkPhysicalDeviceFeatures*, the aggregate C declaration in the
# pinned Vulkan headers. Vulkan-Hpp vk::PhysicalDeviceFeatures is NOT an
# aggregate and must not be used as an isolated designated-initializer test.
python3 - "$SOURCE/src/video_core/renderer_vulkan/vk_instance.cpp" \
  "$CAPTURE/info/vulkan-features-preflight.cpp" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
begin=s.index("vk::PhysicalDeviceFeatures2{")
begin=s.index(".features{",begin)+len(".features{")
end=s.index("\n            },",begin)
initializer=s[begin:end]
assert ".shaderImageGatherExtended =" in initializer
assert ".shaderSampledImageArrayDynamicIndexing =" in initializer
Path(sys.argv[2]).write_text(
    "#include <vulkan/vulkan.hpp>\n"
    "#include <type_traits>\n"
    "static_assert(std::is_aggregate_v<VkPhysicalDeviceFeatures>);\n"
    "int main() {\n"
    "  VkPhysicalDeviceFeatures features{};\n"
    "  VkPhysicalDeviceFeatures enabled{\n"
    + initializer + "\n  };\n  (void)enabled;\n  return 0;\n}\n")
PY
c++ -std=gnu++23 -fsyntax-only \
  -I "$SOURCE/externals/vulkan-headers/include" \
  "$CAPTURE/info/vulkan-features-preflight.cpp" \
  > "$CAPTURE/info/vulkan-features-preflight.log" 2>&1 || {
    cat "$CAPTURE/info/vulkan-features-preflight.log" >&2
    fail "Vulkan designated initializer failed exact-header compilation preflight"
  }
echo "VULKAN_DESIGNATOR_ORDER_PINNED_HEADER=PASS" | tee -a "$CAPTURE/info/progress.log"

stage CONFIGURE_CMAKE
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
 > "$CAPTURE/info/configure.log" 2>&1 || {
 tail -n 100 "$CAPTURE/info/configure.log" >&2;exit 5;
 }
stage BUILD_NATIVE_EMULATOR_COMPLETE_ERROR_SWEEP
# Ninja -k 0 compiles all independent translation units even if one fails,
# collecting the remaining compiler errors in ONE archive instead of
# repeating costly incremental runs for each newly exposed source file.
# A failed build still prevents game launch.
cmake --build "$BUILD" --parallel 8 -- -k 0 \
  > "$CAPTURE/full-build.log" 2>&1 || {
  grep -nE 'error:|fatal error:|FAILED:|undefined reference|ninja: build stopped|ninja: cannot make progress' \
    "$CAPTURE/full-build.log" | tail -n 160 >&2 || true
  exit 7
}
BIN="$BUILD/shadps4"
[[ -x "$BIN" ]] || BIN="$(find "$BUILD" -type f -name shadps4 -executable -print -quit)"
[[ -n "$BIN" && -x "$BIN" ]] || fail "Full build passed but shadps4 binary not found"
sha256sum "$BIN" > "$CAPTURE/info/binary-sha256.txt"
echo "NATIVE_FULL_LINK=PASS"
stage CHECK_VRAM
VRAM=""
for gpu in /sys/class/drm/card[0-9]*/device; do
 [[ -r "$gpu/vendor" ]] || continue
 [[ "$(<"$gpu/vendor")" == "0x1002" ]] || continue
 [[ -r "$gpu/mem_info_vram_total" && -r "$gpu/mem_info_vram_used" ]] || continue
 VRAM="$gpu"
 break
done
if [[ -n "$VRAM" ]]; then
 free_bytes="$(($(cat "$VRAM/mem_info_vram_total")-$(cat "$VRAM/mem_info_vram_used")))"
 echo "VRAM_FREE_BEFORE=$free_bytes" | tee -a "$CAPTURE/info/progress.log"
 if (( free_bytes < 8589934592 )); then
  python3 - "$VRAM_JSON" <<'PY'
import json,sys,urllib.request
uri="http://127.0.0.1:7869"
try:
 models=json.load(urllib.request.urlopen(uri+"/api/ps",timeout=8)).get("models",[])
except Exception as e:raise SystemExit("Low VRAM but Ollama state unavailable: "+str(e))
with open(sys.argv[1],"w",encoding="utf-8") as f:json.dump(models,f)
for model in models:
 name=model.get("name") or model.get("model")
 if not name:continue
 payload=json.dumps({"model":name,"prompt":"","stream":False,"keep_alive":0}).encode()
 req=urllib.request.Request(uri+"/api/generate",payload,{"Content-Type":"application/json"})
 urllib.request.urlopen(req,timeout=30).read()
 print("UNLOADED_MODEL="+name,flush=True)
PY
  sleep 2
  free_bytes="$(($(cat "$VRAM/mem_info_vram_total")-$(cat "$VRAM/mem_info_vram_used")))"
  echo "VRAM_FREE_AFTER=$free_bytes" | tee -a "$CAPTURE/info/progress.log"
  ((free_bytes >= 8589934592)) || fail "VRAM remains below 8 GiB; no game launched"
 fi
fi
stage START_118_SECONDS_TERM_2_SECONDS_KILL
if [[ -z "$(printenv DISPLAY || true)" && -d /tmp/.X11-unix ]]; then
 display="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -n 1)"
 [[ -z "$display" ]] || export DISPLAY=":$display"
fi
cat > "$CAPTURE/info/filter.py" <<'PY'
import sys,json,gzip
from collections import Counter,deque
from pathlib import Path
root=Path(sys.argv[1])
patterns={"types":"GuestIndexedImageDimensions:","coherence":"GpuDescriptorTableCpuCoherence:",
"stencil":"StencilReferenceExactMerged:","sampler":"IndexedUniformSamplerBound:",
"strided":"IndexedStridedSource:","sharp":"Sharp source was not flatenned",
"srt":"SRT dependency resolution stalled","device_loss":"DEVICE_LOST",
"crash":"<Critical>"}
counts=Counter();shown=Counter();last=deque(maxlen=80)
with (root/"shader-events.txt").open("w") as out, gzip.open(
        root/"game-console.log.gz","wt",compresslevel=1,encoding="utf-8") as full:
 for line in sys.stdin:
  full.write(line)
  last.append(line[-350:])
  for label,needle in patterns.items():
   if needle in line:
    counts[label]+=1
    if shown[label]<40 and out.tell()<250000:
     out.write("["+label+"] "+line[:1000])
     shown[label]+=1
    break
(root/"info"/"runtime-events.json").write_text(json.dumps(counts,indent=2)+"\n")
(root/"console-tail.txt").write_text("".join(last))
print("RUNTIME_EVENTS="+json.dumps(counts),flush=True)
PY
(
  set -o pipefail
  timeout -s TERM -k 2s 118s "$BIN" \
      --game "$GAME" --fullscreen true --show-fps 2>&1 |
    python3 -u "$CAPTURE/info/filter.py" "$CAPTURE"
) &
JOB=$!
prev=0
for second in 40 55 85 112; do
 sleep "$((second-prev))"
 prev="$second"
 kill -0 "$JOB" 2>/dev/null || break
 if command -v scrot >/dev/null; then
  scrot "$CAPTURE/menu-$second.png" >/dev/null 2>&1 || true
 elif command -v import >/dev/null; then
  import -window root "$CAPTURE/menu-$second.png" >/dev/null 2>&1 || true
 fi
done
code=0
wait "$JOB" || code=$?
echo "$code" > "$CAPTURE/info/game-exit-code.exit"
echo "GAME_EXIT=$code"
if command -v journalctl >/dev/null; then
 journalctl -k --since '3 minutes ago' --no-pager 2>/dev/null |
   grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
   tail -n 70 > "$CAPTURE/info/gpu-kernel.txt" || true
fi
gzip -1 "$CAPTURE/full-build.log" || true
echo "GAME_RUNTIME_LIMIT=118_TERM_2_KILL"
echo "SAFE_WORKTREE_TEST=YES"
