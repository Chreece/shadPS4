#!/usr/bin/env bash
# GoT controlled RADV DCC isolation: reuse the SHA-pinned, verified v33 binary
# and screenshot harness. Exactly one changed runtime setting: append nodcc.
# No rebuild, git reset, SSH changes, Sunshine changes, or checkpoint deletion.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
P="$ROOT/GhostOfTsushima"
V33="$P/tests/got-v33-gow-srt-correctness-20261013"
BIN="$V33/build/shadps4"
OLD="$P/scripts/got-v33-gow-srt-visual.sh"
NEW="$P/scripts/got-v34-nodcc-visual.sh"
EXPECTED="9e6aff9d6d83310cdb4937c2488d64be836e7903a55b67fca08264cdd6a8ab8a"
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
if [[ -f "$NEW" && ! -L "$NEW" ]]; then
    printf 'PREVIOUS_V34_RUN=FOUND; REBUILD=NO; REPEAT_GAME_LAUNCH=NO\n'
    bash "$NEW" status
    exit 0
fi
[[ ! -e "$NEW" && ! -L "$NEW" ]] || fail "unsafe existing v34 launcher"
for x in "$P" "$P/scripts" "$V33" "$V33/build"; do
    [[ -d "$x" && ! -L "$x" ]] || fail "missing/unsafe required directory: $x"
done
for x in "$BIN" "$OLD"; do
    [[ -f "$x" && ! -L "$x" ]] || fail "missing or unsafe verified v33 input: $x"
done
[[ -x "$BIN" ]] || fail "v33 executable not runnable"
[[ "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$EXPECTED" ]] ||
    fail "v33 binary differs from uploaded successful compile provenance"
for x in python3 bash sha256sum xwininfo; do
    command -v "$x" >/dev/null || fail "missing $x"
done
printf 'V33_BINARY=VERIFIED\nPREVIOUS_FIXES=PRESERVED\n'
python3 - "$OLD" "$NEW" "$EXPECTED" <<'PY'
from pathlib import Path
import re,sys
old,new,sha=sys.argv[1:]
s=Path(old).read_text()
oldlabel='got-v33-gow-srt-visual'
newlabel='got-v34-nodcc-visual'
source_name='got-v33-gow-srt-correctness-20261013'
if oldlabel not in s or source_name not in s or sha not in s:
    raise SystemExit('STOP: v33 screenshot harness does not match tested version')
# Exact, safe one-variable change: retain ALL existing RADV flags but disable DCC.
pattern=re.compile(r"(?m)^(?P<pre>[ \t]*(?:export[ \t]+)?RADV_DEBUG=)(?P<quote>['\"]?)(?P<flags>[a-z0-9_,]+)(?P=quote)(?P<comment>[ \t]*(?:#.*)?)$")
matches=list(pattern.finditer(s))
if len(matches)!=1:
    raise SystemExit(f'STOP: expected exactly 1 literal RADV_DEBUG assignment, found {len(matches)}')
m=matches[0]
flags=m.group('flags').split(',')
if 'hang' not in flags:
    raise SystemExit('STOP: RADV_DEBUG=hang missing; comparison must keep the same evidence mode')
if 'nodcc' in flags:
    raise SystemExit('STOP: v33 already had nodcc, no meaningful comparison')
newflags=','.join(flags+['nodcc'])
replacement=m.group('pre')+m.group('quote')+newflags+m.group('quote')+m.group('comment')
s=s[:m.start()]+replacement+s[m.end():]
s=s.replace(oldlabel,newlabel)
if oldlabel in s or 'got-v34-nodcc-visual' not in s or source_name not in s:
    raise SystemExit('STOP: v34 labels or binary provenance invalid')
p=Path(new)
if p.exists() or p.is_symlink():
    raise SystemExit('STOP: would overwrite an existing v34 launcher')
p.write_text(s)
p.chmod(0o700)
print('DCC_AB=ONLY_RADV_DEBUG_PLUS_NODCC; V33_BINARY=UNCHANGED')
print('RADV_DEBUG_BEFORE='+','.join(flags))
print('RADV_DEBUG_AFTER='+newflags)
PY
bash -n "$NEW" || fail "new screenshot launcher failed syntax check"
printf 'STAGE=ONE_DETACHED_V34_VISUAL_RUN\n'
bash "$NEW" start
printf 'ARCHIVE_STATUS_COMMAND=bash %s status\n' "$NEW"
