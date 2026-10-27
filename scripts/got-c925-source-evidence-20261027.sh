#!/usr/bin/env bash
# Capture the real locally recoverable c925 GoT source without checkout/build.
# No writes to source, Git index, game data, ES-DE, installed binaries or SSH.
set -Eeuo pipefail
export GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0
umask 077
SHA=c925523b49ef39434024b16b2b014d120471ee1a
ELF_SHA=260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
CLEAN="$ROOT/worktrees/got-proven-20261026"
DIRTY="$ROOT/fresh-source/got-indexed-capacity-20261024-213908"
BINARY="$ROOT/candidates/$SHA-strided-precise-image-readbacks-$ELF_SHA/shadps4"
LOGS="$HOME/projects/shadps4/logs"
OUT="$LOGS/got-c925-source-provenance-$(date +%Y%m%d-%H%M%S)"
STATUS=PREFLIGHT
mkdir -p "$OUT" "$LOGS"
finish() {
  rc=$?
  trap - EXIT
  python3 - "$OUT" "$LOGS" "$SHA" "$ELF_SHA" "$STATUS" "$rc" <<'PY' || true
import json,sys
from pathlib import Path
out,logs,sha,elf,status,rc=sys.argv[1:]
data={'schema_version':2,'record_type':'LOCAL_EXACT_SOURCE_ARCHIVE',
      'source_commit':sha,'verified_elf_sha256':elf,
      'status':status,'runner_exit':int(rc),'source_writes':False,'game_launched':False}
line=json.dumps(data,separators=(',',':'))
Path(out,'trial.jsonl').write_text(line+'\n')
with Path(logs,'emulator-trials-local.jsonl').open('a') as f:f.write(line+'\n')
PY
  echo "$rc" > "$OUT/runner-exit-code.txt"
  tar -czf "$OUT.tar.gz" -C "$OUT" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
die() { echo "STOP: $*" >&2; exit 2; }
for cmd in git python3 sha256sum tar gzip stat; do
  command -v "$cmd" >/dev/null 2>&1 || die "Missing $cmd"
done
[[ -e "$CLEAN/.git" && -e "$DIRTY/.git" && -r "$BINARY" ]] ||
  die "Expected recovered source/verified protected binary missing"
[[ "$(git -C "$CLEAN" cat-file -t "$SHA^{commit}")" == commit ]] ||
  die "Exact recovered commit not available in clean-source object store"
[[ "$(git -C "$DIRTY" rev-parse HEAD)" == "$SHA" ]] ||
  die "Historical source HEAD changed; refusing a mistaken diff"
[[ "$(sha256sum "$BINARY" | awk '{print $1}')" == "$ELF_SHA" ]] ||
  die "Menu-visible binary SHA no longer matches; refusing attribution"
STATUS=VERIFIED
printf 'CLEAN_REPOSITORY=%s\nDIRTY_REPOSITORY=%s\nSOURCE_COMMIT=%s\nVERIFIED_ELF=%s\n' \
  "$CLEAN" "$DIRTY" "$SHA" "$ELF_SHA" > "$OUT/provenance.txt"
git -C "$CLEAN" show -s --format=fuller "$SHA" > "$OUT/commit.txt"
git -C "$CLEAN" log -12 --format='%H %P %s' "$SHA" > "$OUT/ancestry.txt"
git -C "$CLEAN" ls-tree -r --long "$SHA" > "$OUT/tree-manifest.txt"
git -C "$DIRTY" status --porcelain=v1 --untracked-files=normal > "$OUT/dirty-status.txt"
git -C "$DIRTY" diff --stat "$SHA" -- \
 src/video_core src/shader_recompiler src/core src/common src/frontend CMakeLists.txt cmake tests \
 > "$OUT/dirty-code-diff-stat.txt"
git -C "$DIRTY" diff --binary "$SHA" -- \
 src/video_core src/shader_recompiler src/core src/common src/frontend CMakeLists.txt cmake tests \
 > "$OUT/dirty-code-diff.patch"
git -C "$DIRTY" diff --cached --binary "$SHA" -- \
 src/video_core src/shader_recompiler src/core src/common src/frontend CMakeLists.txt cmake tests \
 > "$OUT/index-code-diff.patch"
git -C "$CLEAN" status --porcelain=v1 --untracked-files=no > "$OUT/clean-repo-tracked-status.txt"
{
  for file in \
   src/video_core/renderer_vulkan/vk_rasterizer.cpp \
   src/video_core/buffer_cache/buffer_cache.cpp \
   src/video_core/amdgpu/resource.h \
   src/shader_recompiler/backend/spirv/emit_spirv_image.cpp \
   src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
   src/core/memory.cpp; do
    printf '%s\n' "FILE=$file"
    printf 'PINNED_GIT_BLOB='
    git -C "$CLEAN" rev-parse "$SHA:$file" || true
    if [[ -f "$DIRTY/$file" ]]; then
      printf 'DIRTY_WORKTREE_BLOB='
      git -C "$DIRTY" hash-object "$DIRTY/$file" || true
    fi
  done
} > "$OUT/interesting-blob-identities.txt"
printf 'FILE_BYTES=%s\n' "$(stat -c %s "$BINARY")" > "$OUT/binary-metadata.txt"
if command -v readelf >/dev/null 2>&1; then
  readelf -n "$BINARY" > "$OUT/elf-notes.txt" 2>&1 || true
  readelf -p .comment "$BINARY" > "$OUT/elf-compiler.txt" 2>&1 || true
fi
# The committed full tree is necessary for examining actual C++/SPIR-V and
# constructing a safe local trial, rather than inventing source from GitHub.
# Bound the uncompressed tracked blob size to prevent accidental huge archives.
bytes="$(awk '$1 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ { sum+=$4 } END {print sum+0}' "$OUT/tree-manifest.txt")"
printf 'COMMITTED_TRACKED_BYTES=%s\n' "$bytes" >> "$OUT/provenance.txt"
if (( bytes > 300000000 )); then
  STATUS=SOURCE_TREE_TOO_LARGE
  echo "Tracked source exceeds safe source-archive limit (300MB), capture metadata only" >&2
  exit 5
fi
STATUS=ARCHIVING
git -C "$CLEAN" archive --format=tar "$SHA" |
  gzip -1 > "$OUT/exact-c925-git-source.tar.gz"
test -s "$OUT/exact-c925-git-source.tar.gz" ||
  die "Source archive unexpectedly empty"
gzip -t "$OUT/exact-c925-git-source.tar.gz" ||
  die "Source archive failed gzip validation"
sha256sum "$OUT/exact-c925-git-source.tar.gz" > "$OUT/source-archive-sha256.txt"
STATUS=SOURCE_CAPTURED
echo "EXACT_SOURCE_COMMIT=$SHA"
echo "KNOWN_ELF_SHA_MATCH=PASS"
echo "SOURCE_ARCHIVED=PASS"
echo "DIRTY_WORKTREE_WAS_NOT_MODIFIED=YES"
