#!/usr/bin/env bash
# Read-only source-parity comparison; no emulator, GPU, build, checkout or system changes.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOGS="$HOME/projects/shadps4/logs"
OLD="$ROOT/tests/got-v51-stencil-reference-20261014"
NEW="$ROOT/tests/got-generic-srt-680029f3/source"
mkdir -p "$LOGS"
OUT="$(mktemp -d "$LOGS/got-source-parity-20261015-XXXXXXXX")"
mkdir -p "$OUT/info" "$OUT/old" "$OUT/new"
{
  printf 'GOAL=compare proven v55-era source with 3802579 upstream candidate\n'
  printf 'COLLECTED=%s\n' "$(date -Is)"
  printf 'READ_ONLY_SOURCE=YES; GAME_RUN=NO; NO_GITHUB_ACTIONS=YES\n'
  for label in old new; do
    if [[ "$label" == old ]]; then src="$OLD"; else src="$NEW"; fi
    printf '\nSOURCE_%s=%s\n' "$label" "$src"
    if [[ -d "$src" && ! -L "$src" ]]; then
      printf 'HEAD_%s=' "$label"
      git -C "$src" rev-parse HEAD 2>&1 || true
      printf 'TRACKED_DIRTY_%s=' "$label"
      git -C "$src" status --porcelain --untracked-files=no 2>/dev/null | wc -l || true
    else
      printf 'MISSING_%s_SOURCE\n' "$label"
    fi
  done
  for b in "$ROOT/play/manual-v55-direct/shadps4" "$ROOT/tests/got-generic-srt-680029f3/build/shadps4"; do
    if [[ -f "$b" && ! -L "$b" ]]; then sha256sum "$b"; else printf 'MISSING_BINARY=%s\n' "$b"; fi
  done
  for p in "$OLD/build/cpu-id-runtime/libshadps4_cpu_id.so" "$ROOT/tests/got-generic-srt-680029f3/build/cpu-id-runtime/libshadps4_cpu_id.so"; do
    if [[ -f "$p" ]]; then printf 'CPU_ID_RUNTIME_PRESENT=%s\n' "$p"; else printf 'CPU_ID_RUNTIME_MISSING=%s\n' "$p"; fi
  done
} > "$OUT/info/provenance.txt"
while IFS= read -r rel; do
  [[ -n "$rel" ]] || continue
  for label in old new; do
    if [[ "$label" == old ]]; then src="$OLD"; else src="$NEW"; fi
    f="$src/$rel"
    if [[ -f "$f" && ! -L "$f" ]] && (( $(stat -c%s "$f") < 2000000 )); then
      mkdir -p "$OUT/$label/$(dirname "$rel")"
      cp -- "$f" "$OUT/$label/$rel"
      sha256sum "$f" >> "$OUT/info/$label-hashes.txt"
    else
      printf 'MISSING_OR_TOO_LARGE %s %s\n' "$label" "$rel" >> "$OUT/info/skips.txt"
    fi
  done
  if [[ -f "$OUT/old/$rel" && -f "$OUT/new/$rel" ]]; then
    diff -u --label "old-v55/$rel" --label "upstream/$rel" "$OUT/old/$rel" "$OUT/new/$rel" >> "$OUT/info/changes.patch" || true
  fi
done <<'SOURCES'
src/main.cpp
src/core/cpu_patches.cpp
src/core/cpu_patches.h
src/core/cpu_id.cpp
src/core/cpu_id.h
src/core/emulator.cpp
src/core/emulator_settings.cpp
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/backend/spirv/emit_spirv_image.cpp
src/shader_recompiler/backend/spirv/emit_spirv.cpp
src/shader_recompiler/info.h
src/shader_recompiler/frontend/translate/scalar_memory.cpp
src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_instance.cpp
src/video_core/amdgpu/liverpool.cpp
CMakeLists.txt
src/CMakeLists.txt
SOURCES
for needle in SHADPS4_GHOST_BINDLESS_EXPERIMENT cpu-id-mode CPU_ID_TRANSLATION GHOST_BINDLESS_TABLE GhostRecordSelector ImageSampleImplicitLodGhostIndexed; do
  printf '\n=== %s ===\n' "$needle" >> "$OUT/info/feature-inventory.txt"
  grep -Rn -F "$needle" "$OUT/old" "$OUT/new" >> "$OUT/info/feature-inventory.txt" 2>/dev/null || true
done
ARCHIVE="$OUT.tar.gz"
tar -czf "$ARCHIVE.tmp" -C "$OUT" info old new
mv -f -- "$ARCHIVE.tmp" "$ARCHIVE"
echo "SOURCE_PARITY=CAPTURED; NO_GPU_RUN; NO_REBUILD; NO_PROFILE_CHANGE"
echo "ARCHIVE=$ARCHIVE"
echo "SSH=UNCHANGED; X11=UNCHANGED; SUNSHINE=UNCHANGED; MESA=UNCHANGED"
