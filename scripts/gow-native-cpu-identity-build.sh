#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# An isolated, SSH-safe build and CPU regression test. Never launches or kills a game.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok"
BASE="$ROOT/tests/main-proof-20261015-151529-3107006/source"
STAGE="$ROOT/tests/native-cpu-identity-merged-20261016"
LOGROOT="$HOME/projects/shadps4/logs"
REPO="https://github.com/Chreece/shadPS4.git"
BRANCH="fix/native-cpu-identity-merged-20261016"
mkdir -p "$STAGE" "$LOGROOT"
LOG="$LOGROOT/gow-native-cpu-build-$(date +%Y%m%d-%H%M%S)-$$.log"
exec > >(tee "$LOG") 2>&1
on_exit() {
    code=$?
    printf 'BUILD_EXIT=%d\nLOG=%s\nINSTALLED_EMULATOR=UNCHANGED\nOTHER_GAMES=NOT_LAUNCHED\nSSH_SESSION=UNCHANGED\n' "$code" "$LOG"
}
trap on_exit EXIT
printf 'CPU_NATIVE_BRANCH=%s\nSTAGE=%s\n' "$BRANCH" "$STAGE"
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "This GoW gameplay trial requires Linux x86-64. Portable CPU policy is platform-neutral."
    exit 2
fi
if ! git -C "$BASE" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Previously tested source checkout was not found: $BASE"
    exit 2
fi

# Download only the development branch into the existing source repository.
# The previous source checkout, currently running binaries and user config stay unchanged.
git -C "$BASE" -c http.version=HTTP/1.1 fetch --no-tags "$REPO" "refs/heads/$BRANCH"
WANT="$(git -C "$BASE" rev-parse FETCH_HEAD)"
if [[ -e "$STAGE/source" ]]; then
    GOT="$(git -C "$STAGE/source" rev-parse HEAD)"
    if [[ "$GOT" != "$WANT" ]]; then
        echo "The existing isolated source has another revision. Refusing to overwrite it."
        exit 3
    fi
else
    git -C "$BASE" worktree add --detach "$STAGE/source" "$WANT"
fi
printf 'SOURCE=%s\n' "$WANT"
git -C "$STAGE/source" submodule update --init --recursive --jobs 4
mkdir -p "$STAGE/bin"

# No external emulator, driver, DynamoRIO, or root rights needed for CPU-policy tests.
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$STAGE/source/src" \
    "$STAGE/source/tests/cpu_id/test_guest_cpuid.cpp" \
    -o "$STAGE/bin/test_guest_cpuid"
"$STAGE/bin/test_guest_cpuid"
g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$STAGE/source/src" \
    "$STAGE/source/tests/cpu_id/test_rdpid_trap_table.cpp" \
    -o "$STAGE/bin/test_rdpid_trap_table"
"$STAGE/bin/test_rdpid_trap_table"

# Build an independent native emulator. The installed/ES-DE executable is untouched.
cmake -S "$STAGE/source" -B "$STAGE/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF -DENABLE_CPU_ID_TRANSLATION=OFF
JOBS="$(nproc)"
if (( JOBS > 8 )); then JOBS=8; fi
cmake --build "$STAGE/build" --target shadps4 --parallel "$JOBS"
test -x "$STAGE/build/shadps4"
sha256sum "$STAGE/build/shadps4"
printf 'CANDIDATE=%s\nCPU_ID_BACKEND=BUILTIN_IN_PROCESS\nNEXT=GAMEPLAY_REGRESSION_NOT_YET_RUN\n' \
    "$STAGE/build/shadps4"
