#!/usr/bin/env bash
# Run as a child bash, not by sourcing into an SSH login shell.
main() {
    local src="/home/chreece/src/shadps4-waitstage-fix"
    local state="/home/chreece/.local/state/shadps4-ngs2-probe"
    local dest="/home/chreece/Applications/shadps4/releases/ngs2-probe"
    local vol="shadps4-clang19-build"
    local image="shadps4-local-clang19-gcc14:ngs2-v1"
    local log expected base rc
    base="0a7790aaa11c5ec0009cc66976bb90a8ce7078e5"
    expected="${1:-}"
    if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then echo 'EXPECTED_COMMIT_REQUIRED'; return 1; fi
    command -v python3 >/dev/null && command -v docker >/dev/null || return 1
    docker info >/dev/null 2>&1 || { echo 'DOCKER_ACCESS=FAIL'; return 1; }
    [[ -z "$(git -C "$src" status --porcelain 2>/dev/null)" ]] || {
        echo 'SOURCE_HAS_LOCAL_CHANGES: nothing was reset'; return 1;
    }
    if [[ -n "$(docker ps -q --filter "volume=$vol")" ]]; then
        echo 'BUILD_VOLUME_BUSY: allow the other build to finish'; return 1;
    fi
    git -C "$src" cat-file -e "$expected^{commit}" || return 1
    git -C "$src" merge-base --is-ancestor "$base" "$expected" || return 1
    git -C "$src" checkout --detach "$expected" || return 1
    git -C "$src" submodule update --init --recursive || return 1
    mkdir -p "$state" "$dest" || return 1
    log="$state/build-$(date +%Y%m%d-%H%M%S).log"
    echo "BUILD_LOG=$log"
    python3 -B "$src/tools/ngs2_probe/test_instrument.py" || return 1
    python3 -B "$src/tools/ngs2_probe/instrument.py" \
        "$src/src/core/libraries/ngs2/ngs2.cpp" "$state/ngs2.cpp" || return 1
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo 'Preparing reusable compiler image...'
        if ! docker build -t "$image" "$src/tools/ngs2_probe" >"$log" 2>&1; then
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
fi
