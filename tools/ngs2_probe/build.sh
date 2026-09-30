#!/usr/bin/env bash
# Run as a child bash, not by sourcing into an SSH login shell.
main() {
    # Never switch branches or clean the user's working source tree.
    local seed="/home/chreece/src/shadps4-waitstage-fix"
    local src="/home/chreece/src/shadps4-ngs2-probe-source"
    local remote="https://github.com/Chreece/shadPS4.git"
    local state="/home/chreece/.local/state/shadps4-ngs2-probe"
    local dest="/home/chreece/Applications/shadps4/releases/ngs2-probe"
    local vol="shadps4-clang19-build"
    local image="shadps4-local-clang19-gcc14:ngs2-v2"
    local log expected base rc status
    base="0a7790aaa11c5ec0009cc66976bb90a8ce7078e5"
    expected="${1:-}"
    if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then echo 'EXPECTED_COMMIT_REQUIRED'; return 1; fi
    command -v git >/dev/null && command -v python3 >/dev/null &&
        command -v docker >/dev/null || return 1
    docker info >/dev/null 2>&1 || { echo 'DOCKER_ACCESS=FAIL'; return 1; }
    if [[ -n "$(docker ps -q --filter "volume=$vol")" ]]; then
        echo 'BUILD_VOLUME_BUSY: allow the other build to finish'; return 1;
    fi
    mkdir -p "$state" "$dest" "$(dirname "$src")" || return 1
    log="$(mktemp "$state/build-$(date +%Y%m%d-%H%M%S)-XXXXXX.log")" || return 1
    echo "BUILD_LOG=$log"
    echo "ORIGINAL_SOURCE_PRESERVED=$seed"
    echo "ISOLATED_SOURCE=$src"

    if [[ ! -e "$src" && ! -L "$src" ]]; then
        echo 'Preparing an independent checkout; borrowing committed objects only during clone.'
        if ! git -c submodule.alternateLocation=superproject \
            -c submodule.alternateErrorStrategy=info clone \
            --reference-if-able "$seed" --dissociate --recurse-submodules --jobs 4 \
            --branch diagnostics/ngs2-bounded-trace "$remote" "$src" >>"$log" 2>&1; then
            echo 'ISOLATED_CLONE=FAIL; partial checkout retained'
            tail -70 "$log"; return 1
        fi
    fi
    if [[ -L "$src" || ! -d "$src/.git" || -L "$src/.git" ]]; then
        echo 'ISOLATED_SOURCE_INVALID: expected a separate regular clone'; return 1;
    fi
    status="$(git -C "$src" status --porcelain --untracked-files=normal)" || return 1
    if [[ -n "$status" ]]; then
        echo "ISOLATED_SOURCE_HAS_LOCAL_CHANGES=$src"
        printf '%s\n' "$status" | head -30
        echo 'Nothing was reset, stashed, or removed.'; return 1
    fi
    if ! git -C "$src" cat-file -e "$expected^{commit}" 2>/dev/null; then
        git -C "$src" fetch --no-recurse-submodules "$remote" "$expected" >>"$log" 2>&1 || {
            tail -70 "$log"; return 1;
        }
    fi
    git -C "$src" merge-base --is-ancestor "$base" "$expected" || return 1
    git -C "$src" checkout --detach "$expected" >>"$log" 2>&1 || {
        tail -70 "$log"; return 1;
    }
    git -C "$src" submodule update --init --recursive --jobs 4 >>"$log" 2>&1 || {
        tail -70 "$log"; return 1;
    }
    echo "SOURCE_COMMIT=$(git -C "$src" rev-parse HEAD)"

    # Preserve timestamps only for byte-identical tracked source files. The same
    # /src container path and /work volume allow Ninja to reuse unaffected objects.
    # Differing files retain their new timestamps and will be rebuilt.
    python3 -B - "$seed" "$src" <<'PY'
from pathlib import Path
import filecmp
import os
import subprocess
import sys

seed, src = map(Path, sys.argv[1:])
paths = subprocess.check_output([
    'git', '-C', str(src), 'ls-files', '--recurse-submodules', '-z'
]).split(b'\0')
matched = 0
for raw in paths:
    if not raw:
        continue
    rel = Path(os.fsdecode(raw))
    old, new = seed / rel, src / rel
    try:
        if old.is_symlink() or new.is_symlink() or not old.is_file() or not new.is_file():
            continue
        before = old.stat()
        if before.st_size != new.stat().st_size or not filecmp.cmp(old, new, shallow=False):
            continue
        after = old.stat()
        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (
            after.st_mtime_ns, after.st_ctime_ns, after.st_size
        ):
            continue
        os.utime(new, ns=(new.stat().st_atime_ns, before.st_mtime_ns))
        matched += 1
    except OSError:
        # Failure to reuse a timestamp only costs a rebuild, not source correctness.
        continue
print(f'UNCHANGED_SOURCE_TIMESTAMPS_REUSED={matched}')
PY
    rc=$?
    if [[ "$rc" -ne 0 ]]; then return "$rc"; fi
    python3 -B "$src/tools/ngs2_probe/test_toolchain.py" || return 1
    python3 -B "$src/tools/ngs2_probe/test_instrument.py" || return 1
    python3 -B "$src/tools/ngs2_probe/instrument.py" \
        "$src/src/core/libraries/ngs2/ngs2.cpp" "$state/ngs2.cpp" || return 1
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo 'Preparing reusable compiler image...'
        if ! docker build -t "$image" "$src/tools/ngs2_probe" >>"$log" 2>&1; then
            tail -70 "$log"; return 1;
        fi
    fi
    docker volume create "$vol" >/dev/null || return 1
    echo 'Building diagnostic binary; existing object cache retained.'
    docker run --rm --cpus=6 \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -v "$src:/src:ro" \
        -v "$state/ngs2.cpp:/src/src/core/libraries/ngs2/ngs2.cpp:ro" \
        -v "$vol:/work" -v "$dest:/out" "$image" bash -c '
            set -e
            bash /src/tools/ngs2_probe/toolchain_preflight.sh
            clang++-19 --version
            g++-14 --version
            clang++-19 -std=c++23 -Wall -Wextra -Werror -pthread \
                /src/tools/ngs2_probe/test_probe.cpp -o /work/ngs2-probe-test
            /work/ngs2-probe-test
            SHADPS4_NGS2_TRACE=1 /work/ngs2-probe-test
            cmake -S /src -B /work/build -G Ninja \
                -DCMAKE_BUILD_TYPE=Release \
                -DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON \
                -DCMAKE_C_COMPILER=clang-19 -DCMAKE_CXX_COMPILER=clang++-19 \
                -DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold \
                -DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold
            cmake --build /work/build --parallel 6
            test -x /work/build/shadps4
            install -m 755 /work/build/shadps4 /out/shadps4.new
            install -m 755 /work/ngs2-probe-test /out/probe-test
            chown "$HOST_UID:$HOST_GID" /out/shadps4.new /out/probe-test
        ' >>"$log" 2>&1
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
        echo "NGS2_PROBE_BUILD=FAIL rc=$rc"; tail -90 "$log"; return 1;
    fi
    grep -F 'TOOLCHAIN_PREFLIGHT=PASS' "$log" | tail -1 || true
    "$dest/probe-test" || return 1
    SHADPS4_NGS2_TRACE=1 "$dest/probe-test" >>"$log" 2>&1 || return 1
    timeout 10s "$dest/shadps4.new" --help >>"$log" 2>&1 || return 1
    if [[ -e "$dest/shadps4" ]]; then
        cp -p "$dest/shadps4" "$dest/shadps4.previous.$(date +%Y%m%d-%H%M%S)" || return 1
    fi
    mv "$dest/shadps4.new" "$dest/shadps4" || return 1
    printf '%s\n' "$expected" > "$dest/source-commit.txt" || return 1
    sha256sum "$dest/shadps4"
    python3 -B "$src/tools/ngs2_probe/install_entry.py" || return 1
    echo 'NGS2_PROBE_BUILD=PASS'
    echo 'ESDE_ENTRY=Red Dead Redemption [NGS2 trace]'
    echo 'TRACE_REPORT=/home/chreece/.local/state/shadps4-ngs2-probe/latest/report.txt'
}
if main "$@"; then
    printf '\nREADY: no game launched by this installer.\n'
else
    printf '\nNGS2_PROBE_SETUP=FAIL; existing sparse-queue binary unchanged.\n'
    false
fi
