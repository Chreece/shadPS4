#!/usr/bin/env bash
(
  set -Eeuo pipefail
  ROOT="$HOME/projects/shadps4"
  RUN="shadps4-indirect-hazards-20261027-170922-3777629"
  WORK="$ROOT/Upstream/tests/$RUN"
  SRC="$WORK/source"
  TEST="$WORK/build-tests/tests/shadps4_gcn_test"
  NATIVE="$WORK/build/shadps4"
  EXPECTED_SOURCE="43d7d22360dee7a545ef9e81557fb8ae9109f7a4"
  EXPECTED_BINARY="3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75"
  [[ -x "$TEST" && -x "$NATIVE" && -d "$SRC/.git" ]] || {
    echo "ERROR: Existing build/test binaries not found"; exit 3;
  }
  [[ "$(git -C "$SRC" rev-parse HEAD)" == "$EXPECTED_SOURCE" ]] || {
    echo "ERROR: Source commit mismatch"; exit 3;
  }
  [[ "$(sha256sum "$NATIVE" | cut -d' ' -f1)" == "$EXPECTED_BINARY" ]] || {
    echo "ERROR: Previously validated native binary changed"; exit 3;
  }
  LOGS="$ROOT/logs"
  TAG="indirect-six-native-$(date +%Y%m%d-%H%M%S)-$BASHPID"
  EVIDENCE="$LOGS/$TAG"
  mkdir -p "$EVIDENCE"
  printf 'SOURCE=%s\nNATIVE_SHA256=%s\nGCN_GPU_TESTS=NOT_RUN\n' \
      "$EXPECTED_SOURCE" "$EXPECTED_BINARY" > "$EVIDENCE/provenance.txt"
  "$TEST" --gtest_list_tests --gtest_filter='IndirectCommandSynchronization.*' \
      > "$EVIDENCE/selected-tests.txt" 2>&1
  COUNT="$(grep -Ec '^  [A-Za-z][A-Za-z0-9_]+$' "$EVIDENCE/selected-tests.txt" || true)"
  RC=9
  if [[ "$COUNT" == 6 ]]; then
    set +e
    timeout 30 "$TEST" --gtest_filter='IndirectCommandSynchronization.*' --gtest_color=no \
        > "$EVIDENCE/native-six-tests.log" 2>&1
    RC=$?
    set -e
  else
    printf 'ERROR: Expected six selected tests, found %s\n' "$COUNT" \
        > "$EVIDENCE/native-six-tests.log"
  fi
  if [[ "$RC" == 0 ]] && grep -Fq '[  PASSED  ] 6 tests.' "$EVIDENCE/native-six-tests.log"; then
    RESULT=PASS
  else
    RESULT=FAIL
    [[ "$RC" == 0 ]] && RC=9
  fi
  printf 'INDIRECT_SIX_NATIVE_TESTS=%s\nSELECTED_TESTS=%s\nEXIT=%s\n' \
      "$RESULT" "$COUNT" "$RC" | tee "$EVIDENCE/result.txt"
  cat "$EVIDENCE/native-six-tests.log"
  ARCHIVE="$LOGS/$TAG.tar.gz"
  tar -czf "$ARCHIVE" -C "$LOGS" "$TAG"
  echo "UPLOAD_EVIDENCE=$ARCHIVE"
  echo "SUNSHINE_X11_MOONLIGHT_ESDE_SAVES=UNTOUCHED"
  exit "$RC"
)
