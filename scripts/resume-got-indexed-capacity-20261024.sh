#!/usr/bin/env bash
# Resume a proven source worktree after a transient Git submodule transfer failure.
# No source reset, no save deletion, no ES-DE changes, no SSH shutdown.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

ROOT="${HOME}/projects/shadps4/GhostOfTsushima"
OLD="${ROOT}/fresh-source/got-descriptor-identity-20261024-200312"
RUN="20261024-213908"
WORK="${ROOT}/fresh-source/got-indexed-capacity-${RUN}"
BUILD="${ROOT}/fresh-build/got-indexed-capacity-${RUN}"
LOGROOT="${HOME}/projects/shadps4/logs"
CAPTURE="${LOGROOT}/got-indexed-capacity-${RUN}"
BASE="53b27cbc75b3511efca0c258c129241477f0aae8"
VULKAN_HEADERS_SHA="ee3b5caaa7e372715873c7b9c390ee1c3ca5db25"
VULKAN_PATH="externals/vulkan-headers"
SUB="${WORK}/${VULKAN_PATH}"
PREVIOUS_SUB="${OLD}/${VULKAN_PATH}"

mkdir -p "${CAPTURE}/info" "${LOGROOT}"
echo "GOT_INDEXED_RESUME_STAGE=VERIFY_EXISTING_PATCHED_SOURCE"
if ! git -C "${WORK}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FAIL: existing patched worktree not found: ${WORK}" >&2
  exit 2
fi
REV="$(git -C "${WORK}" rev-parse HEAD)"
PARENT="$(git -C "${WORK}" rev-parse HEAD^)"
if [[ "${PARENT}" != "${BASE}" ]]; then
  echo "FAIL: expected patched build based on ${BASE}, found parent ${PARENT}" >&2
  exit 2
fi
DIFF="$(git -C "${WORK}" diff --name-only "${BASE}..${REV}")"
if [[ "${DIFF}" != "src/video_core/renderer_vulkan/vk_rasterizer.cpp" ]]; then
  echo "FAIL: source changes do not match the single intended rasterizer patch" >&2
  printf 'Changed files: %s\n' "${DIFF}" >&2
  exit 2
fi
git -C "${WORK}" diff "${BASE}..${REV}" --check
if ! grep -q "Indexed image table: shader=" "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp" ||
   ! grep -q "Indexed sampler table: shader=" "${WORK}/src/video_core/renderer_vulkan/vk_rasterizer.cpp"; then
  echo "FAIL: intended indexed-descriptor corrections absent" >&2
  exit 2
fi
ACTUAL_GITLINK="$(git -C "${WORK}" ls-tree HEAD "${VULKAN_PATH}" | awk '{print $3}')"
if [[ "${ACTUAL_GITLINK}" != "${VULKAN_HEADERS_SHA}" ]]; then
  echo "FAIL: unexpected pinned Vulkan-Headers revision: ${ACTUAL_GITLINK}" >&2
  exit 2
fi
echo "GOT_INDEXED_RESUME_REVISION=${REV}"

echo "GOT_INDEXED_RESUME_STAGE=RECOVER_SUBMODULE"
SUBLOG="${CAPTURE}/submodule-recovery.log"
# Reuse the exact Git object from a previously compiled source, if available.
if git -C "${PREVIOUS_SUB}" cat-file -e "${VULKAN_HEADERS_SHA}^{commit}" 2>/dev/null; then
  echo "Local source has required Vulkan-Headers commit; importing without network."
  if git -C "${SUB}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if ! git -C "${SUB}" -c protocol.file.allow=always fetch --no-tags \
         "${PREVIOUS_SUB}" "${VULKAN_HEADERS_SHA}" >> "${SUBLOG}" 2>&1; then
      echo "Local object import failed, trying the normal checkout path."
    fi
  else
    git -C "${WORK}" -c protocol.file.allow=always \
      submodule update --init --reference-if-able "${PREVIOUS_SUB}" \
      -- "${VULKAN_PATH}" >> "${SUBLOG}" 2>&1 || true
  fi
else
  echo "Prior source has no suitable local Vulkan-Headers Git object; network fallback enabled."
fi

# One request at a time; retry incomplete transfers without recloning/resetting.
OK=0
for attempt in 1 2 3 4 5; do
  echo "GOT_INDEXED_RESUME_SUBMODULE_ATTEMPT=${attempt}/5"
  if git -C "${WORK}" -c http.version=HTTP/1.1 -c http.maxRequests=1 \
    submodule update --init --recursive --jobs 1 >> "${SUBLOG}" 2>&1; then
    OK=1
    break
  fi
  echo "Submodule transfer interrupted (attempt ${attempt}); retrying preserved checkout."
  tail -n 7 "${SUBLOG}" || true
  if (( attempt < 5 )); then sleep $((attempt * 3)); fi
done
if (( ! OK )); then
  echo "FAIL: remaining Git submodule transfers did not complete." >&2
  tail -n 90 "${SUBLOG}" >&2 || true
  echo "RECOVERY_LOG=${SUBLOG}"
  exit 5
fi
if ! git -C "${SUB}" cat-file -e "${VULKAN_HEADERS_SHA}^{commit}" 2>/dev/null; then
  echo "FAIL: required Vulkan-Headers commit absent after update" >&2
  exit 5
fi
UNREADY="$(git -C "${WORK}" submodule status --recursive | grep -E '^[-+U]' || true)"
if [[ -n "${UNREADY}" ]]; then
  echo "FAIL: submodules still out of sync:" >&2
  printf '%s\n' "${UNREADY}" >&2
  exit 5
fi
echo "GOT_INDEXED_RESUME_SUBMODULES=OK"

echo "GOT_INDEXED_RESUME_STAGE=CONFIGURE_NATIVE"
if ! cmake -S "${WORK}" -B "${BUILD}" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  > "${CAPTURE}/configure.log" 2>&1; then
  tail -n 90 "${CAPTURE}/configure.log" >&2
  echo "CONFIGURE_LOG=${CAPTURE}/configure.log"
  exit 6
fi
echo "GOT_INDEXED_RESUME_STAGE=BUILD_NATIVE"
if ! cmake --build "${BUILD}" --parallel 8 > "${CAPTURE}/full-build.log" 2>&1; then
  tail -n 120 "${CAPTURE}/full-build.log" >&2
  echo "BUILD_LOG=${CAPTURE}/full-build.log"
  exit 7
fi

BIN="${BUILD}/shadps4"
if [[ ! -x "${BIN}" ]]; then
  BIN="$(find "${BUILD}" -type f -name shadps4 -executable -print -quit)"
fi
if [[ -z "${BIN}" || ! -x "${BIN}" ]]; then
  echo "FAIL: native build completed but no shadps4 executable was found." >&2
  exit 8
fi
HASH="$(sha256sum "${BIN}" | awk '{print $1}')"
CANDIDATE="${ROOT}/candidates/${REV}-${HASH}"
mkdir -p "${CANDIDATE}"
install -m 755 "${BIN}" "${CANDIDATE}/shadps4"
printf '%s\n' "${BASE}" > "${CAPTURE}/info/base-revision.txt"
printf '%s\n' "${REV}" > "${CAPTURE}/info/revision.txt"
printf '%s\n' "${HASH}" > "${CAPTURE}/info/binary-sha256.txt"
printf '%s\n' "${CANDIDATE}/shadps4" > "${CAPTURE}/info/candidate-path.txt"
git -C "${WORK}" diff "${BASE}..${REV}" > "${CAPTURE}/info/verified-commit-diff.patch"
for file in \
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
  src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
  src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
  src/video_core/renderer_vulkan/vk_runtime.cpp \
  src/video_core/renderer_vulkan/vk_rasterizer.cpp; do
  if [[ -f "${WORK}/${file}" ]]; then
    mkdir -p "${CAPTURE}/source/$(dirname "${file}")"
    cp -p "${WORK}/${file}" "${CAPTURE}/source/${file}"
  fi
done
echo "GOT_INDEXED_RESUME_CANDIDATE=${CANDIDATE}/shadps4"

echo "GOT_INDEXED_RESUME_STAGE=RUN_GAME"
if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  XSOCKET="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' -printf '%f\n' | sort -V | head -1)"
  [[ -z "${XSOCKET}" ]] || export DISPLAY=":${XSOCKET#X}"
fi
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
if [[ ! -f "${GAME}" ]]; then
  echo "GAME_MISSING=${GAME}; build is ready, no game launch performed."
  exit 9
fi
echo "Manual menu and gameplay input only; no automatic input injection."
EXIT=0
timeout -k 10s 420s "${CANDIDATE}/shadps4" \
  --game "${GAME}" --fullscreen true --show-fps \
  > "${CAPTURE}/game-console.log" 2>&1 &
GAME_PID=$!
previous=0
for elapsed in 20 40 60 100 150 200 260 320 380; do
  sleep "$((elapsed - previous))"
  previous="${elapsed}"
  kill -0 "${GAME_PID}" 2>/dev/null || break
  shot="${CAPTURE}/game-screen-${elapsed}.png"
  if command -v scrot >/dev/null 2>&1; then
    scrot "${shot}" >/dev/null 2>&1 || true
  elif command -v import >/dev/null 2>&1; then
    import -window root "${shot}" >/dev/null 2>&1 || true
  elif command -v grim >/dev/null 2>&1; then
    grim "${shot}" >/dev/null 2>&1 || true
  fi
done
wait "${GAME_PID}" || EXIT=$?
printf '%s\n' "${EXIT}" > "${CAPTURE}/info/game-exit-code.txt"
echo "GOT_INDEXED_RESUME_GAME_EXIT=${EXIT}"

echo "GOT_INDEXED_RESUME_STAGE=COLLECT_LOGS"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since "10 minutes ago" --no-pager -o short-iso 2>/dev/null |
    grep -Eai 'amdgpu|GPU reset|ring.*timeout|drm.*(fault|hang)' |
    tail -n 300 > "${CAPTURE}/gpu-kernel-events.txt" || true
fi
grep -Eai \
  'Indexed image table|Indexed sampler table|SRT|Sharp source|PhiDescriptor|BindBuffers|Clamped size|GPU fault|CopyMip|context is lost|soft recovery|Validation' \
  "${CAPTURE}/game-console.log" | awk '!seen[$0]++' |
  head -c 10000000 > "${CAPTURE}/graphics-events.txt" || true
grep -E 'warning:|error:' "${CAPTURE}/full-build.log" |
  grep -v '/externals/' | head -c 1000000 \
  > "${CAPTURE}/project-build-warnings.txt" || true

# Preserve the FULL logs, compressed, rather than silently truncating evidence.
gzip -1 -f "${CAPTURE}/game-console.log"
gzip -1 -f "${CAPTURE}/full-build.log"
ARCHIVE="${LOGROOT}/got-indexed-capacity-${RUN}-resumed.tar.gz"
tar -C "${CAPTURE}" -czf "${ARCHIVE}" .
echo "GOT_INDEXED_RESUME_STAGE=DONE"
echo "UPLOAD_THIS=${ARCHIVE}"
echo "Candidate preserved: ${CANDIDATE}/shadps4"
echo "SSH session stays open."
