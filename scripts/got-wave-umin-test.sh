#!/usr/bin/env bash
# Whole-wave unsigned-minimum general shader fix: isolated build + unattended GoT runtime proof.
# No Moonlight, no system-driver changes, no ES-DE/Sunshine/X11 manipulation.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
NAME="got-wave-umin-$(date +%Y%m%d-%H%M%S)"
JOB="$LOGS/$NAME.sh"
mkdir -p "$LOGS"
cat > "$JOB" <<'WORKER'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
LOGS="$ROOT/logs"
NAME="$1"
WORK="$GOT/tests/got-wave-umin-20261016"
SOURCE="$WORK/source"
BUILD="$WORK/build"
BASE="$GOT/tests/got-atomic-view-20261016-130607/source"
BIN="$BUILD/shadps4"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
PROFILE="$GOT/play/np-init-affinity-main-20261015-195552"
EXPERIMENT="a81ff9b7cdb62efb51513fd6428218761054c42e"
ATOMIC="054915dda44c304e6e760559a2cf3734c247b538"
SIRIT="ea1c8221304006c918439413ed135933d673a0d8"
RUN="$LOGS/.$NAME.runtime"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS="STARTED"
mkdir -p "$RUN/info" "$RUN/screenshots"
finish() {
  rc=$?
  trap - EXIT
  printf 'JOB_EXIT=%s\nSTATUS=%s\n' "$rc" "$STATUS" | tee "$RUN/info/final.txt"
  echo "SSH=UNCHANGED MOONLIGHT=NOT_USED SUNSHINE=UNCHANGED X11=UNCHANGED ESDE=UNCHANGED"
  if tar --exclude='*/user' --exclude='*/.cache' -czf "$ARCHIVE" \
      -C "$LOGS" "$(basename "$RUN")" "$NAME.log" "$NAME.sh"; then
    rm -rf -- "$RUN"
    echo "REPORT=$ARCHIVE"
  else
    echo "REPORT_FAILED_RETAINED=$RUN"
  fi
}
trap finish EXIT

test "$(git -C "$BASE" rev-parse HEAD)" = "$ATOMIC"
test -x "$GOT/tests/got-atomic-view-20261016-130607/build/shadps4"
test -f "$GAME"
test -d "$PROFILE/user"
test -d "$BASE/externals/sirit"

if [ ! -d "$SOURCE/.git" ]; then
  if [ -e "$SOURCE" ]; then
    echo "UNEXPECTED_SOURCE_DIRECTORY=$SOURCE"
    exit 1
  fi
  mkdir -p "$WORK"
  echo "PREPARING_ISOLATED_GIT_CLONE"
  git clone --quiet --shared --no-checkout --no-recurse-submodules "$BASE" "$SOURCE"
  git -C "$SOURCE" -c submodule.recurse=false checkout --detach "$EXPERIMENT"
fi

# Share the already compiled third-party source checkout without altering it.
  for dep in "$BASE"/externals/*; do
    [ -d "$dep" ] || continue
    name=$(basename "$dep")
    [ "$name" != sirit ] || continue
    dest="$SOURCE/externals/$name"
    if [ -d "$dest" ] && [ -z "$(ls -A "$dest")" ]; then rmdir "$dest"; fi
    if [ ! -e "$dest" ]; then ln -s "$dep" "$dest"; fi
  done
  if [ ! -e "$SOURCE/externals/sirit/.git" ]; then
    rmdir "$SOURCE/externals/sirit" 2>/dev/null || true
    git clone --quiet --shared --no-checkout "$BASE/externals/sirit" \
        "$SOURCE/externals/sirit"
  fi
  if ! git -C "$SOURCE/externals/sirit" cat-file -e "$SIRIT^{commit}" 2>/dev/null; then
    git -C "$SOURCE/externals/sirit" -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false fetch --no-tags --depth=3 \
        https://github.com/shadps4-emu/sirit.git "$SIRIT"
  fi
  git -C "$SOURCE/externals/sirit" checkout --detach "$SIRIT"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$EXPERIMENT"
test "$(git -C "$SOURCE/externals/sirit" rev-parse HEAD)" = "$SIRIT"
git -C "$SOURCE" diff --quiet --ignore-submodules=all HEAD --
echo "SOURCE=$EXPERIMENT" > "$RUN/info/provenance.txt"
echo "SIRIT=$SIRIT" >> "$RUN/info/provenance.txt"
echo "BASELINE=$ATOMIC" >> "$RUN/info/provenance.txt"
echo "EXTERNAL_SOURCE=nikmparis217-ux/shadPS4@b6d8c00deca90fdb8596678637776a30b50f2a88" >> "$RUN/info/provenance.txt"

echo "CONFIGURE_START"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release
echo "BUILD_START"
cmake --build "$BUILD" --target shadps4 --parallel 8
test -x "$BIN"
STATUS="BUILD_PASS"
sha256sum "$BIN" | tee "$RUN/info/binary.sha256"

# A failing Vulkan preflight must never be reported as a successful emulator run.
command -v vulkaninfo >/dev/null
command -v xdpyinfo >/dev/null
DISPLAY_OK=""
for d in "${DISPLAY:-}" :0 :1 :2; do
  [ -n "$d" ] || continue
  if DISPLAY="$d" timeout 5s xdpyinfo -display "$d" >/dev/null 2>&1; then
    DISPLAY_OK="$d"
    break
  fi
done
if [ -z "$DISPLAY_OK" ]; then STATUS="DISPLAY_UNAVAILABLE"; exit 0; fi
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_EMULATOR_ACTIVE"
  exit 0
fi
export DISPLAY="$DISPLAY_OK"
(
  unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
  timeout 20s vulkaninfo --summary
) > "$RUN/info/vulkan-preflight.txt" 2>&1 || {
  STATUS="VULKAN_PRECHECK_FAILED"
  exit 0
}
grep -Eqi 'deviceName[[:space:]]*=.*(RX 7900 XTX|RADV NAVI31)' \
    "$RUN/info/vulkan-preflight.txt" || { STATUS="GPU_NOT_FOUND"; exit 0; }

echo "RUNTIME_START DISPLAY=$DISPLAY_OK"
STATUS="RUNTIME_STARTED"
mkdir -p "$RUN/info/shaders"
tar -C "$PROFILE" --exclude='user/shader/dumps' --exclude='user/cache' \
  --exclude='user/log' --exclude='user/logs' -cf - user | tar -C "$RUN" -xf -
export HOME="$RUN"
export RADV_DEBUG="hang,allbos,noumr"
unset VK_DRIVER_FILES VK_ICD_FILENAMES VK_INSTANCE_LAYERS
started=$(date +%s)
(
  cd "$RUN"
  timeout --foreground -s TERM -k 8s 105s "$BIN" \
      --game "$GAME" --fullscreen true > "$RUN/game.log" 2>&1
) &
pid=$!
for at in 4 8 16 24 36 50 70 92; do
  elapsed=$(( $(date +%s) - started ))
  if [ "$elapsed" -lt "$at" ]; then sleep "$((at-elapsed))"; fi
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  if command -v import >/dev/null; then
    timeout 6s import -display "$DISPLAY_OK" -window root \
       "$RUN/screenshots/$at.png" >/dev/null 2>&1 || true
  elif command -v ffmpeg >/dev/null; then
    dims=$(xdpyinfo -display "$DISPLAY_OK" | awk '/dimensions:/{print $2;exit}')
    timeout 7s ffmpeg -nostdin -hide_banner -loglevel error \
       -f x11grab -video_size "$dims" -i "$DISPLAY_OK" \
       -frames:v 1 -y "$RUN/screenshots/$at.png" >/dev/null 2>&1 || true
  fi
done
set +e
wait "$pid"
game_exit=$?
set -e
{
  echo "GAME_EXIT=$game_exit"
  echo "RUNTIME_SECONDS=$(( $(date +%s) - started ))"
  echo "GPU_HANGS=$(grep -ac 'GPU hang detected' "$RUN/game.log" || true)"
  echo "INVALID_SPIRV=$(grep -ac 'VUID-VkShaderModuleCreateInfo-pCode-08737' "$RUN/game.log" || true)"
  echo "GRAPHICS_PIPELINES=$(grep -ac 'Compiling gs shader' "$RUN/game.log" || true)"
  echo "COMPUTE_PIPELINES=$(grep -ac 'Compiling cs shader' "$RUN/game.log" || true)"
  echo "SCREENSHOTS=$(find "$RUN/screenshots" -type f -name '*.png' | wc -l)"
} | tee "$RUN/info/result.txt"
grep -aE 'VUID-|GPU hang detected|Unhandled signal|Assertion Failed|Device lost' \
     "$RUN/game.log" > "$RUN/info/validation-errors.txt" || true
if [ -d "$RUN/user/shader/dumps" ]; then
  find "$RUN/user/shader/dumps" -maxdepth 1 -type f \
    \( -name '*fs_*.spv' -o -name '*ps_*.spv' \) \
    -exec cp -n -- {} "$RUN/info/shaders/" \; || true
fi
python3 - "$RUN" <<'PY'
import collections, pathlib, struct, sys
root = pathlib.Path(sys.argv[1])
paths = list(root.glob("radv_dumps_*/*.spv"))
paths += list((root/"info"/"shaders").glob("*.spv"))
with (root/"info"/"spirv-operations.txt").open("w") as out:
    for path in paths:
        data = path.read_bytes()
        if len(data) % 4 or len(data) < 20:
            continue
        words = struct.unpack("<%dI" % (len(data)//4), data)
        if words[0] != 0x07230203:
            continue
        pos = 5
        ops = collections.Counter()
        while pos < len(words):
            wc = words[pos] >> 16
            if wc == 0:
                break
            ops[words[pos] & 65535] += 1
            pos += wc
        print(f"{path.name} GroupUMin={ops[354]} ShuffleXor={ops[346]} "
              f"Loops={ops[246]} ImageSample={ops[87]+ops[88]}", file=out)
PY
journalctl -k --since "@$started" -o short-iso --no-pager 2>/dev/null \
  | grep -Ei 'amdgpu|drm|ring|GPU fault|reset|timeout' \
  | tail -n 180 > "$RUN/info/kernel-gpu-events.txt" || true
STATUS="RUNTIME_CAPTURED"
echo "PROOF_COMPLETE=$RUN/info/result.txt"
WORKER
chmod +x "$JOB"
nohup bash "$JOB" "$NAME" >"$LOGS/$NAME.log" 2>&1 </dev/null &
printf 'JOB_PID=%s\nMONITOR=tail -f %s\nREPORT=%s.tar.gz\nSSH_SESSION=UNCHANGED\nMOONLIGHT=NOT_USED\n' \
  "$!" "$LOGS/$NAME.log" "$LOGS/$NAME"
