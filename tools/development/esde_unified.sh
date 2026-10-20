#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# One general ES-DE integration entry point (supersedes Oct18).
# Oct18 cd3cc802 + Oct05 fork main omitted tested CPU/UFFD/SRT fixes.
# The old source remains available in immutable Git history.
# NEVER replaces the selected ES-DE emulator.
set -Eeuo pipefail
umask 022

ROOT="$HOME/projects/shadps4"
ESDE="$ROOT/ESDE"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
PY="$SCRIPTS/esde_general_engine_source_merge_20261019.py"

MERGER_REF="717ecc4ec65d15ade039ee6498f7595b5336b8df"
MERGER_GIT_BLOB="f74a023f57e5a9984326349e84695566c3108dd1"
RESOLVER="$SCRIPTS/esde_general_semantic_resolver_20261019.py"
RESOLVER_REF="840b719aaf4d870e1e6b69e4d04c55224356597a"
RESOLVER_GIT_BLOB="9b6472f0d062b1e23d0e3376a9255ff4f7f8107c"
RESOLVER_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$RESOLVER_REF/tools/development/esde_general_semantic_resolver_20261019.py"
LATE_RESOLVER="$SCRIPTS/esde_general_late_semantic_resolver_20261020.py"
LATE_REF="15b7aa6b0bbddca580fa18ed04312692d3573c81"
LATE_GIT_BLOB="776addf1e4c594f29dac9ecb2590478a9a641a53"
LATE_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$LATE_REF/tools/development/esde_general_late_semantic_resolver_20261020.py"
MERGER_URL="https://raw.githubusercontent.com/Chreece/shadPS4/$MERGER_REF/tools/development/esde_general_engine_source_merge_20261019.py"

if [[ $# -gt 1 ]]; then
    echo 'Usage: esde_unified.sh [--self-test|--build]' >&2
    echo 'Retired resume cannot reopen the Oct18 source graph.' >&2
    exit 2
fi
MODE="--build"
if [[ $# -eq 1 ]]; then MODE="$1"; fi
case "$MODE" in
    --build|--self-test) ;;
    *)
        echo 'Usage: esde_unified.sh [--self-test|--build]' >&2
        echo "NO_OLD_UPSTREAM_PIN_REUSE=YES"
        exit 2
        ;;
esac

test "$(id -u)" != 0
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
for app in git python3 curl flock sha256sum tee; do
    command -v "$app" >/dev/null || { echo "MISSING_TOOL=$app" >&2; exit 2; }
done
mkdir -p "$ESDE" "$LOGS" "$SCRIPTS"
exec 9>"$ESDE/.esde-general-entry.lock"
flock -n 9 || { echo "GENERAL_ESDE_BUILD_ALREADY_RUNNING" >&2; exit 2; }

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ENTRYLOG="$LOGS/esde-general-entry-$TIMESTAMP.log"
exec > >(tee -a "$ENTRYLOG") 2>&1
echo "ESDE_GENERAL_ENTRY_LOG=$ENTRYLOG"
echo "GENERIC_ENGINE_BUILD=YES"
echo "OBSOLETE_OCT18_UPSTREAM_SHA=RETIRED"
echo "REVIEWED_OFFICIAL_UPSTREAM=a808e7626e3c7de8d0f0fda6a91f620b42f6c762"
echo "ESDE_CORE_INSTALL=NOT_REQUESTED"

TMP=""
trap 'if [[ -n "$TMP" ]]; then rm -f -- "$TMP"; fi' EXIT
if [[ ! -f "$PY" ]] ||
   [[ "$(git hash-object "$PY" 2>/dev/null || true)" != "$MERGER_GIT_BLOB" ]]; then
    echo "STAGE=FETCH_PINNED_GENERAL_ENGINE_MERGER"
    TMP="$(mktemp "$SCRIPTS/.esde-general-source-XXXXXXXX")"
    curl -4fLsS --retry 3 --connect-timeout 15 --max-time 180 \
        "$MERGER_URL" -o "$TMP"
    HASH="$(git hash-object "$TMP")"
    [[ "$HASH" == "$MERGER_GIT_BLOB" ]] || {
        echo "PINNED_MERGER_GIT_BLOB_MISMATCH=$HASH" >&2
        exit 3
    }
    mv -f -- "$TMP" "$PY"
    TMP=""
fi

if [[ ! -f "$RESOLVER" ]] ||
   [[ "$(git hash-object "$RESOLVER" 2>/dev/null || true)" != "$RESOLVER_GIT_BLOB" ]]; then
    echo "STAGE=FETCH_SHA_AUDITED_GENERIC_CPP_RESOLVER"
    TMP="$(mktemp "$SCRIPTS/.esde-generic-resolver-XXXXXXXX")"
    curl -4fLsS --retry 3 --connect-timeout 15 --max-time 180 \
        "$RESOLVER_URL" -o "$TMP"
    HASH="$(git hash-object "$TMP")"
    [[ "$HASH" == "$RESOLVER_GIT_BLOB" ]] || {
        echo "PINNED_SEMANTIC_RESOLVER_GIT_BLOB_MISMATCH=$HASH" >&2
        exit 3
    }
    mv -f -- "$TMP" "$RESOLVER"
    TMP=""
fi

if [[ ! -f "$LATE_RESOLVER" ]] ||
   [[ "$(git hash-object "$LATE_RESOLVER" 2>/dev/null || true)" != "$LATE_GIT_BLOB" ]]; then
    echo "STAGE=FETCH_SHA_VERIFIED_LATER_GENERAL_CPP_RESOLVER"
    TMP="$(mktemp "$SCRIPTS/.esde-late-semantic-XXXXXXXX")"
    curl -4fLsS --retry 3 --connect-timeout 15 --max-time 180 \
        "$LATE_URL" -o "$TMP"
    HASH="$(git hash-object "$TMP")"
    [[ "$HASH" == "$LATE_GIT_BLOB" ]] || {
        echo "PINNED_LATE_CPP_RESOLVER_GIT_BLOB_MISMATCH=$HASH" >&2
        exit 3
    }
    mv -f -- "$TMP" "$LATE_RESOLVER"
    TMP=""
fi

python3 -m py_compile "$PY" "$RESOLVER" "$LATE_RESOLVER"
echo "STAGE=MERGE_SEMANTICS_SELF_TEST"
python3 -B "$PY" --self-test

if [[ "$MODE" == --build ]]; then
    echo "STAGE=GENERAL_CPU_GPU_SHADER_SOURCE_INTEGRATION"
    python3 -B "$PY" --build
else
    echo "SELF_TEST_ONLY=PASS"
fi
echo "GENERAL_ESDE_ENTRY=COMPLETE; INSTALLED_BINARY_UNCHANGED=YES"
echo "SSH_SESSION=REMAINS_OPEN"
