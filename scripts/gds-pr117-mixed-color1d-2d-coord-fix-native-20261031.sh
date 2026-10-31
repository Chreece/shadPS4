#!/usr/bin/env bash
# Build-only validation of the universal shared Vk2D guest Color1D/Color2D fix.
# NO emulator launch, GPU test, DynamoRIO, Xorg/Sunshine changes, SSH termination.
set -Eeuo pipefail
umask 077
P="$HOME/projects/shadps4"
ROOT="$P/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BIN="$ROOT/build/shadps4"
LOGS="$P/logs"
PIN="be50ced5fdbbc3a3b069fc921743ded6c2fb566a"
EXPECTED_PREVIOUS_ELF="24e28de5fdf91fe1865720a0f002883cf5863ef72b562a55aab9ec07a2e79590"
EXPECTED_PROTECTED_ELF="0971e21ee0b7593b1cef86f19e796fa8c616c297e7d0f23d688dfdcdebc3f687"
BRANCH="feature/pr117-gpu-color1d-2d-shared-vkview-20261031"
TAG="gds-pr117-color1d2d-coordinates-native-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
OLD_HEAD=NOT_FOUND
OLD_ELF=NOT_FOUND
NEW_ELF=NOT_BUILT
mkdir -p "$OUT"
finish() {
  code=$?
  trap - EXIT
  set +e
  printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE_PIN=%s\nORIGINAL_HEAD=%s\nORIGINAL_ELF=%s\nNEW_ELF=%s\nNO_GAME_OR_GPU_RUN=YES\nNO_SESSION_RESTART=YES\n' \
    "$STATE" "$code" "$PIN" "$OLD_HEAD" "$OLD_ELF" "$NEW_ELF" >"$OUT/result.txt"
  python3 - "$P" "$TAG" "$STATE" "$code" "$PIN" "$OLD_ELF" "$NEW_ELF" "$ARCHIVE" <<'PYDIARY' || true
import datetime,fcntl,json,os,pathlib,sys
project,tag,state,rc,source,oldelf,newelf,archive=sys.argv[1:]
row=dict(schema_version=2,record_id=tag,
 recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 scope='universal_shadPS4',game_id='CUSA34384',
 record_type='UNIVERSAL_GPU_TYPED_1D_2D_NATIVE_BUILD_ONLY',
 source_commit=source,original_elf_sha256=oldelf,new_elf_sha256=newelf,
 outcome=state,exit_code=int(rc),gameplay='NOT_TESTED',gpu='NOT_RUN',
 archive=pathlib.Path(archive).name,decision='NEED_NATIVE_RESULT_BEFORE_ANY_GPU_ACCEPTANCE')
for name in ('diary/trials.jsonl','docs/emulator/TRIALS.jsonl'):
 p=pathlib.Path(project)/name
 if p.is_symlink():continue
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('a',encoding='utf8') as stream:
  fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
  stream.write(json.dumps(row,separators=(',',':'))+'\n')
  stream.flush();os.fsync(stream.fileno())
  fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
PYDIARY
  tar -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/tar-errors.txt"
  printf 'STATUS=%s\nUPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$STATE" "$ARCHIVE"
}
trap finish EXIT
for c in git sha256sum python3 tar flock cmake g++ clang++-17 nice df awk ldd; do
  command -v "$c" >/dev/null || { echo "MISSING_TOOL=$c"; exit 2; }
done
[[ -d "$SRC/.git" && -f "$BIN" && -x "$BIN" && ! -L "$BIN" ]] || {
  echo 'ISOLATED_SOURCE_OR_BINARY_MISSING';exit 3;
}
exec 9>"$LOGS/gds-pr117-real-guest-gpu-exclusive.lock"
exec 8>"$LOGS/gow-upstream-got-build.lock"
exec 7>"$LOGS/shadps4-gow-srt-phi.lock"
exec 6>"$LOGS/shadps4-official-indirect-game.lock"
exec 5>"$ROOT/.indirect-native-build.lock"
for fd in 9 8 7 6 5; do
  flock -n "$fd" || { echo 'OTHER_BUILD_OR_GPU_OWNER_ACTIVE'; exit 4; }
done
OLD_HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$OLD_HEAD" == "08d173a2214a9cb77f10075e66068ada2197e993" ||
   "$OLD_HEAD" == "$PIN" ]] || {
  echo "SOURCE_NOT_PREVIOUS_ACCEPTED_BUILD_NO_CHANGES"; exit 4;
}
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || {
  echo 'DIRTY_ISOLATED_SOURCE_NO_CHANGES';exit 4;
}
[[ -f "$ROOT/build/build.ninja" && -f "$ROOT/build/CMakeCache.txt" ]] || {
  echo 'ISOLATED_CMAKE_BUILD_MISSING';exit 4;
}
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$ROOT/build/CMakeCache.txt" || {
  echo 'CMAKE_SOURCE_DIRECTORY_MISMATCH';exit 4;
}
SPACE="$(df -Pk "$ROOT" | awk 'END{print $4}')"
[[ "$SPACE" =~ ^[0-9]+$ ]] && (( SPACE >= 3145728 )) || {
  echo 'LESS_THAN_3GIB_FREE_NO_BUILD';exit 4;
}
OLD_ELF="$(sha256sum "$BIN" | awk '{print $1}')"
[[ "$OLD_ELF" == "$EXPECTED_PREVIOUS_ELF" ]] || {
  echo "PRIOR_COMPILED_ELF_NOT_VERIFIED_NO_CHANGES"; exit 4;
}
OLDER_BACKUP="$ROOT/preserved/gds-before-mixed-color1d-2d-$EXPECTED_PROTECTED_ELF"
echo "$EXPECTED_PROTECTED_ELF  $OLDER_BACKUP" | sha256sum -c --status || {
  echo "ORIGINAL_PR117_ELF_BACKUP_INVALID_NO_CHANGES"; exit 4;
}
PRESERVED="$ROOT/preserved/gds-before-mixed-color1d-2d-$OLD_ELF"
mkdir -p "$(dirname "$PRESERVED")"
if [[ ! -e "$PRESERVED" ]]; then
  cp --reflink=auto -- "$BIN" "$PRESERVED"
fi
echo "$OLD_ELF  $PRESERVED" | sha256sum -c --status || {
  echo 'PREVIOUS_ELF_PRESERVATION_FAILED';exit 4;
}
STATE=FAST_FORWARD_ISOLATED_SOURCE_ONLY
if [[ "$OLD_HEAD" != "$PIN" ]]; then
  GIT_TERMINAL_PROMPT=0 git -C "$SRC" fetch --no-tags \
    https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH" \
    >"$OUT/fetch.log" 2>&1 || { tail -60 "$OUT/fetch.log";exit 5; }
  git -C "$SRC" cat-file -e "$PIN^{commit}" || exit 5
  git -C "$SRC" merge-base --is-ancestor HEAD "$PIN" || {
    echo "DIVERGED_ISOLATED_SOURCE_NO_CHECKOUT";exit 5;
  }
  git -C "$SRC" switch --quiet --detach "$PIN"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || exit 5
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || exit 5
STATE=STRICT_SOURCE_CONTRACTS
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/check_pr117_guest_color1d_2d.py" \
  >"$OUT/mixed-source-contract.log" 2>&1 || {
    tail -90 "$OUT/mixed-source-contract.log";exit 6;
  }
grep -Fq 'GUEST_2_TO_4_COMPONENT_IMPLICIT_IMAGE_SAMPLE=PASS' \
  "$OUT/mixed-source-contract.log" || { echo "COORDINATE_ARITY_REGRESSION_FAILED"; exit 6; }
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/check_pr117_gpu_descriptor_tables.py" \
  >"$OUT/descriptor-source-contract.log" 2>&1 || {
    tail -90 "$OUT/descriptor-source-contract.log";exit 6;
  }
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/check_srt_host_walker.py" \
  >"$OUT/srt-source-contract.log" 2>&1 || {
    tail -90 "$OUT/srt-source-contract.log";exit 6;
  }
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/check_pr117_texture_overlap.py" \
  >"$OUT/texture-source-contract.log" 2>&1 || {
    tail -90 "$OUT/texture-source-contract.log";exit 6;
  }
STATE=GCC_CLANG_RELEASE_MIXED_1D_2D_FIXTURE
for compiler in g++ clang++-17; do
  name=gcc
  [[ "$compiler" != clang++-17 ]] || name=clang17
  "$compiler" -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic \
    -I"$SRC/src" "$SRC/tests/compatible_color1d_2d_image.cpp" \
    -o "$OUT/color1d-2d-$name" >"$OUT/compile-$name.log" 2>&1 || {
      tail -80 "$OUT/compile-$name.log";exit 7;
    }
  "$OUT/color1d-2d-$name" >"$OUT/fixture-$name.log" 2>&1 || {
    tail -80 "$OUT/fixture-$name.log";exit 7;
  }
  grep -Fq 'GUEST_DYNAMIC_TSHARP_TYPE_AND_Y_COORDINATE=PASS' "$OUT/fixture-$name.log" || exit 7
  rm -f -- "$OUT/color1d-2d-$name"
done
echo 'GCC_CLANG_RELEASE_FIXTURES=PASS'
STATE=BUILD_NATIVE_NO_GPU_LAUNCH
nice -n 10 cmake --build "$ROOT/build" --target shadps4 --parallel 2 \
  >"$OUT/build-native.log" 2>&1 || {
    tail -120 "$OUT/build-native.log";exit 8;
  }
NEW_ELF="$(sha256sum "$BIN" | awk '{print $1}')"
ldd "$BIN" >"$OUT/native-ldd.txt" 2>&1 || exit 8
! grep -q 'not found' "$OUT/native-ldd.txt" || {
  echo 'NATIVE_ELF_DEPENDENCY_MISSING';exit 8;
}
echo "$OLD_ELF  $PRESERVED" | sha256sum -c --status || {
  echo 'PROTECTED_ELF_CHANGED';exit 8;
}
echo "$EXPECTED_PROTECTED_ELF  $OLDER_BACKUP" | sha256sum -c --status || {
  echo 'ORIGINAL_PR117_ELF_CHANGED';exit 8;
}
if [[ "$NEW_ELF" == "$OLD_ELF" ]]; then
  echo "NEW_SOURCE_DID_NOT_CHANGE_NATIVE_BINARY";exit 8
fi
printf 'NATIVE_COMPILE_AND_LINK=PASS\nOLD_ELF=%s\nNEW_ELF=%s\nNO_GPU_GAME_TEST_EXECUTED=YES\n' \
  "$OLD_ELF" "$NEW_ELF"
STATE=PASS_NATIVE_BUILD_ONLY_NO_GAME_EXECUTION
