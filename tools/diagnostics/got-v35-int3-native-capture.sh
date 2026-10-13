#!/usr/bin/env bash
# GoT v35 native crash localization: GDB trap capture of the existing v34
# failure. No source change, rebuild, sudo, SSH, Sunshine, ES-DE or save mutation.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
PROJECT="$ROOT/GhostOfTsushima"
SCRIPTS="$PROJECT/scripts"
V33="$PROJECT/tests/got-v33-gow-srt-correctness-20261013"
BIN="$V33/build/shadps4"
BASE="$SCRIPTS/got-v34-nodcc-visual.sh"
NEW="$SCRIPTS/got-v35-int3-visual.sh"
WRAPPER="$SCRIPTS/got-v35-int3-wrapper.sh"
SHA="9e6aff9d6d83310cdb4937c2488d64be836e7903a55b67fca08264cdd6a8ab8a"
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }

# If v35 already exists, reuse its output rather than schedule another GPU run.
if [[ -f "$NEW" && ! -L "$NEW" && -f "$WRAPPER" && ! -L "$WRAPPER" ]]; then
    printf 'EXISTING_V35=YES; REBUILD=NO; NEW_GPU_RUN=NO\n'
    bash "$NEW" status
    exit 0
fi
[[ ! -e "$NEW" && ! -L "$NEW" && ! -e "$WRAPPER" && ! -L "$WRAPPER" ]] ||
    fail 'partial/unsafe v35 files exist; refusing to overwrite'
for x in "$ROOT" "$PROJECT" "$SCRIPTS" "$V33" "$V33/build"; do
    [[ -d "$x" && ! -L "$x" ]] || fail "missing or unsafe input directory: $x"
done
for x in "$BASE" "$BIN"; do
    [[ -f "$x" && ! -L "$x" ]] || fail "missing or unsafe input: $x"
done
[[ -x "$BIN" ]] || fail 'v33 binary not executable'
[[ "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$SHA" ]] ||
    fail 'v33 binary hash mismatch; refusing to debug an unknown build'
for x in gdb python3 sha256sum xwininfo systemd-run; do
    command -v "$x" >/dev/null || fail "missing $x; no run scheduled"
done
bash -n "$BASE" || fail 'v34 launcher syntax invalid'
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
    fail 'another shadps4 is running; no new launch'
fi
systemctl --user show-environment >/dev/null 2>&1 ||
    fail 'systemd user manager not available; no SSH-attached launch'

# Build an independent screenshot runner from v34 with precisely one change:
# the executable invocation becomes a GDB wrapper around the SAME v33 binary.
python3 - "$BASE" "$NEW" "$WRAPPER" "$SHA" <<'PY'
from pathlib import Path
import re,sys
old,new,wrapper,sha=map(str,sys.argv[1:])
source=Path(old).read_text()
for marker in ('got-v34-nodcc-visual', 'got-v33-gow-srt-correctness-20261013',sha,
               'systemd-run', 'screenshots', 'RADV_DEBUG=', 'worker()'):
    if marker not in source:raise SystemExit('STOP: unexpected v34 script: missing '+marker)
radvs=re.findall(r'(?m)^[ \t]*(?:export\s+)?RADV_DEBUG=[\'\"]?([a-z0-9_,]+)', source)
if len(radvs)!=1 or not {'hang','nodcc'} <= set(radvs[0].split(',')):
    raise SystemExit('STOP: v34 is not the pinned hang+nodcc setup')
program=r'"\$ready/shadps4"(?=\s+--cpu-id-mode=native\s+--game\s+"\$GAME"\s+--fullscreen\s+true)'
found=list(re.finditer(program,source))
if len(found)!=1:raise SystemExit(f'STOP: expected one game command, got {len(found)}')
# The base runner already stages a separate portable user profile and captures
# screenshots. Keep all those lines and its RADV settings byte-for-byte.
source=source[:found[0].start()]+ '"'+str(Path(wrapper))+'"'+source[found[0].end():]
source=source.replace('got-v34-nodcc-visual','got-v35-int3-visual')
if 'got-v34-nodcc-visual' in source:
    raise SystemExit('STOP: v34 labels remain in modified runner')
if source.count(str(Path(wrapper)))!=1:
    raise SystemExit('STOP: GDB wrapper executable reference is not unique')
if source.count('RADV_DEBUG=')!=1:
    raise SystemExit('STOP: driver setting count changed unexpectedly')
if Path(new).exists() or Path(new).is_symlink():
    raise SystemExit('STOP: v35 runner appeared during validation')
Path(new).write_text(source)
Path(new).chmod(0o700)
print('RUNNER_PARITY=PASS; GAME_BINARY_WRAPPED=ONCE; RADV_FLAGS_UNCHANGED=YES')
PY

# GDB must allow shadPS4's normal SIGSEGV/SIGBUS page-tracking handlers.
# Fatal UNREACHABLE_MSG calls assert_fail_impl(), which executes INT3. If the
# executable retains that symbol, stop BEFORE shutdown; otherwise catch INT3.
cat > "$WRAPPER" <<'GDBWRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BIN="$PWD/shadps4"
SHA="9e6aff9d6d83310cdb4937c2488d64be836e7903a55b67fca08264cdd6a8ab8a"
[[ -x "$BIN" && -f "$BIN" && ! -L "$BIN" ]] || { echo 'GOT35_STOP=NO_PORTABLE_BINARY'; exit 2; }
[[ "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$SHA" ]] || {
    echo 'GOT35_STOP=WRONG_BINARY_SHA'; exit 2;
}
command -v gdb >/dev/null || { echo 'GOT35_STOP=GDB_MISSING'; exit 2; }
printf 'GOT35_NATIVE_CAPTURE=START; BASE=v34_nodcc; BINARY_SHA256=%s\n' "$SHA"
args=(
  -q -nx --batch
  -ex 'set pagination off'
  -ex 'set confirm off'
  -ex 'set print elements 0'
  -ex 'set print frame-arguments scalars'
  -ex 'set disable-randomization off'
  -ex 'handle SIGSEGV nostop noprint pass'
  -ex 'handle SIGBUS nostop noprint pass'
  -ex 'handle SIGILL nostop noprint pass'
  -ex 'handle SIGUSR1 nostop noprint pass'
)
# Prefer the native fatal assertion symbol when available, avoiding the normal
# INT3 trap path that follows teardown. Source symbol can be absent when stripped.
if command -v nm >/dev/null && nm -C "$BIN" 2>/dev/null | grep -E ' [TtWw] assert_fail_impl\(\)' >/dev/null; then
  args+=(-ex 'break assert_fail_impl')
  echo 'GOT35_BREAKPOINT=assert_fail_impl_before_shutdown'
else
  echo 'GOT35_BREAKPOINT=fatal_int3_fallback'
fi
args+=(
  -ex 'run'
  -ex 'printf "\nGOT35_NATIVE_STOP_BEGIN\n"'
  -ex 'info program'
  -ex 'info threads'
  -ex 'thread apply all bt 18'
  -ex 'info registers'
  -ex 'info proc mappings'
  -ex 'x/12i $pc-16'
  -ex 'printf "\nGOT35_NATIVE_STOP_END\n"'
)
exec gdb "${args[@]}" --args "$BIN" "$@"
GDBWRAPPER
chmod 700 "$WRAPPER"
bash -n "$NEW" || fail 'generated runner syntax invalid'
bash -n "$WRAPPER" || fail 'generated wrapper syntax invalid'

printf 'PRECHECK=PASS; V33_UNCHANGED=YES; V34_UNCHANGED=YES\n'
printf 'GDB=ENABLED; DETACHED=SYSTEMD_USER; SCREENSHOTS=YES; GPU_SOURCE_CHANGES=NO\n'
bash "$NEW" start
printf 'CHECK_ARCHIVE_COMMAND=bash %s status\n' "$NEW"
