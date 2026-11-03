#!/usr/bin/env bash
# Hard-limited shadPS4 test launcher: 118 s TERM + 2 s SIGKILL fallback.
# Does not disconnect or terminate the invoking SSH shell.
set -Eeuo pipefail

ROOT="${HOME}/projects/shadps4"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
GOOD_SHA="260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d"
GOOD="${ROOT}/GhostOfTsushima/candidates/c925523b49ef39434024b16b2b014d120471ee1a-strided-precise-image-readbacks-${GOOD_SHA}/shadps4"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

# Only stop the verified previous GoT candidate for the exact GoT game path.
# Other titles, other shadPS4 builds, ES-DE, SSH, and saves are untouched.
stop_previous_got() {
    local proc pid exe matched=0
    local -a targets=()
    for proc in /proc/[0-9]*/exe; do
        [[ -e "$proc" ]] || continue
        pid="${proc%/exe}"
        pid="${pid##*/}"
        exe="$(readlink -f -- "$proc" 2>/dev/null || true)"
        [[ "$exe" == "$GOOD" ]] || continue
        [[ -r "/proc/${pid}/cmdline" ]] || continue
        if ! tr '\0' '\n' < "/proc/${pid}/cmdline" 2>/dev/null |
             grep -Fx -- "$GAME" >/dev/null; then
            continue
        fi
        if kill -TERM "$pid" 2>/dev/null; then
            printf 'Stopping previous unbounded GoT test (PID %s)\n' "$pid"
            targets+=("$pid")
            ((matched+=1))
        fi
    done
    if (( matched )); then
        sleep 2
        for pid in "${targets[@]}"; do
            exe="$(readlink -f -- "/proc/${pid}/exe" 2>/dev/null || true)"
            if [[ "$exe" == "$GOOD" ]] && kill -0 "$pid" 2>/dev/null; then
                printf 'Forcing previous GoT test to stop (PID %s)\n' "$pid"
                kill -KILL "$pid" 2>/dev/null || true
            fi
        done
    fi
}

case "${1:-}" in
    --stop-only)
        (( $# == 1 )) || fail '--stop-only does not accept other arguments'
        stop_previous_got
        printf 'Previous GoT test cleanup completed. SSH remains open.\n'
        exit 0
        ;;
    --stop-previous)
        shift
        stop_previous_got
        ;;
esac

# With no arguments run verified PR #52 + #57 for a maximum of two minutes.
# Any other test build can use the same guard:
#   bash shadps4_test_120s.sh /absolute/path/to/shadps4 --game ... --fullscreen true
if (( $# == 0 )); then
    stop_previous_got
    set -- "$GOOD" --game "$GAME" --fullscreen true --show-fps
fi

command -v timeout >/dev/null 2>&1 || fail 'GNU timeout is missing'
[[ -x "$1" ]] || fail "Emulator binary not executable: $1"

if [[ "$1" == "$GOOD" ]]; then
    actual="$(sha256sum "$GOOD" | awk '{print $1}')"
    [[ "$actual" == "$GOOD_SHA" ]] ||
        fail 'Known-good emulator hash differs from archived baseline'
fi

printf 'TEST_LIMIT=118s TERM + 2s SIGKILL (120s maximum)\n'
printf 'TEST_BINARY=%s\n' "$1"
start=$SECONDS
set +e
timeout -s TERM -k 2s 118s "$@"
rc=$?
set -e
printf 'TEST_ELAPSED_SECONDS=%s\nTEST_EXIT=%s\n' "$((SECONDS-start))" "$rc"
if (( rc == 124 || rc == 137 )); then
    printf 'TEST_TIME_LIMIT_REACHED=YES\n'
fi
printf 'SSH_SESSION=OPEN\n'
exit "$rc"
