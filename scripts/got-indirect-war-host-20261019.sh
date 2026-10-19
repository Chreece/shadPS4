#!/usr/bin/env bash
# Host-only permanent Vulkan indirect read/WAR synchronization fix, original executable restored. No game, Vulkan init, service changes,
# Git checkout, submodule update, profile writes, or SSH exit.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
SOURCE="$WORK/source"
BUILD="$WORK/build"
BIN="$BUILD/shadps4"
LOGS="$ROOT/logs"
BASE="0b648a826f86fd4ec981872ee482c3fc2ae57699"
CANDIDATE="08b1769773fc84931057d5b529f24ca700499a19"
BRANCH="fix/indirect-buffer-read-hazards-20261019"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="got-indirect-war-fix-host-$STAMP"
RUN="$GOT/tests/runs/$NAME"
ARCHIVE="$LOGS/$NAME.tar.gz"
STATUS=STARTED
CANDIDATE_BUILT=0
ORIGINAL_SHA=""
mkdir -p "$LOGS" "$GOT/tests/runs" "$RUN/info" "$RUN/tmp"
touch "$RUN/build.log" "$RUN/host-tests.log" "$RUN/info/restore.txt"
# Archive the real failure reason, not just the final status code.
exec > >(tee -a "$RUN/info/console.log") 2>&1
exec 9>"$WORK/.build.lock"
if ! flock -n 9; then echo "BUILD_LOCK_HELD_NO_CHANGES"; exit 3; fi

finish() {
    local rc=$?
    trap - EXIT INT TERM HUP
    set +e
    # Restore exactly the prior source bytes: no git checkout or reset.
    if [ -s "$RUN/info/restore-operations.txt" ]; then
        while IFS='|' read -r operation rel; do
            case "$operation" in
                RESTORE)
                    mkdir -p "$(dirname "$SOURCE/$rel")"
                    cp -p -- "$RUN/tmp/original/$rel" "$SOURCE/$rel"
                    test $? -eq 0 || rc=2
                    touch "$SOURCE/$rel"
                    ;;
                REMOVE)
                    rm -f -- "$SOURCE/$rel"
                    test $? -eq 0 || rc=2
                    ;;
            esac
        done < "$RUN/info/restore-operations.txt"
    fi
    # Preserve the new build as a candidate, not the active emulator.
    if [ "$CANDIDATE_BUILT" = 1 ] && [ -x "$BIN" ]; then
        mkdir -p "$WORK/bin-backups"
        cp -p -- "$BIN" "$WORK/bin-backups/shadps4-got-indirect-war-fix-$CANDIDATE"
        sha256sum "$WORK/bin-backups/shadps4-got-indirect-war-fix-$CANDIDATE" \
          > "$RUN/info/candidate-preserved.sha256"
    fi
    if [ -f "$RUN/tmp/original-shadps4" ]; then
        if ! cp -p -- "$RUN/tmp/original-shadps4" "$BIN"; then
            rc=2
            echo "ORIGINAL_BINARY_RESTORE_FAILED" >> "$RUN/info/restore.txt"
        elif [ "$(sha256sum "$BIN" | awk '{print $1}')" != "$ORIGINAL_SHA" ]; then
            rc=2
            echo "ORIGINAL_BINARY_HASH_MISMATCH" >> "$RUN/info/restore.txt"
        else
            echo "ORIGINAL_BINARY_RESTORED=PASS hash=$ORIGINAL_SHA" >> "$RUN/info/restore.txt"
        fi
    fi
    {
        echo "STATUS=$STATUS"
        echo "EXIT=$rc"
        echo "CANDIDATE_BUILT=$CANDIDATE_BUILT"
        echo "BASELINE_EXECUTABLE_RESTORED=$(grep -c '^ORIGINAL_BINARY_RESTORED=PASS' "$RUN/info/restore.txt" 2>/dev/null || true)"
        echo "GAME_LAUNCHED=NO"
        echo "OLLAMA_CHANGED=NO"
        echo "SSH_SESSION=OPEN"
    } | tee "$RUN/info/final.txt"
    if tar -czf "$ARCHIVE" -C "$RUN" info \
        --exclude=original-shadps4 --exclude='*.o' \
        -C "$RUN" build.log host-tests.log 2>/dev/null; then
        echo "REPORT=$ARCHIVE"
        rm -rf -- "$RUN"
    else
        echo "ARCHIVE_FAILED_WORKDIR=$RUN"
        rc=2
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

test -d "$SOURCE/.git" && test -x "$BIN" && test -d "$BUILD" || {
    STATUS=WORKSPACE_MISSING; exit 2;
}
for cmd in git python3 c++ cmake ninja ccache; do
    command -v "$cmd" >/dev/null || { STATUS="MISSING_$cmd"; exit 2; }
done
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$BASE" || {
    STATUS=BASE_SOURCE_NOT_PINNED; exit 2;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    STATUS=ACTIVE_SHADPS4_REFUSE_BUILD; exit 2;
fi
free_kib=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
if [ "$free_kib" -lt 3145728 ]; then
    STATUS=DISK_BELOW_3GIB; exit 2;
fi
available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
if [ "$available_kib" -lt 6291456 ]; then
    STATUS=HOST_RAM_BELOW_6GIB; exit 2;
fi
ORIGINAL_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
cp -p -- "$BIN" "$RUN/tmp/original-shadps4"
echo "ORIGINAL_BINARY_HASH=$ORIGINAL_SHA" > "$RUN/info/start.txt"
echo "CURRENT_SOURCE=$BASE" >> "$RUN/info/start.txt"
echo "PINNED_CANDIDATE=$CANDIDATE" >> "$RUN/info/start.txt"

STATUS=FETCH_PINNED_OBJECTS
if ! git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}" 2>/dev/null; then
    git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
      -c fetch.recurseSubmodules=false \
      fetch --depth=256 --no-tags --no-recurse-submodules \
      https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH" \
      > "$RUN/info/git-fetch.log" 2>&1 || {
        tail -60 "$RUN/info/git-fetch.log"
        STATUS=FETCH_FAILED
        exit 2
      }
fi
git -C "$SOURCE" cat-file -e "$CANDIDATE^{commit}"
# Fixed list of all 58 unique pinned source overlays from the original
# material-source runner, plus the new read-bounds header. No reliance on
# another diagnostic branch or a previous report.
cat > "$RUN/info/overlay-files.txt" <<'GOT_PINNED_OVERLAY_FILES'
src/common/slot_vector.h
src/core/devtools/widget/reg_popup.cpp
src/input/controller.cpp
src/input/controller.h
src/input/gamepad_hotplug_snapshot.h
src/input/input_handler.cpp
src/sdl_window.cpp
src/sdl_window.h
src/shader_recompiler/backend/spirv/emit_spirv_image.cpp
src/shader_recompiler/backend/spirv/emit_spirv_instructions.h
src/shader_recompiler/backend/spirv/spirv_emit_context.cpp
src/shader_recompiler/backend/spirv/spirv_emit_context.h
src/shader_recompiler/frontend/control_flow_graph.cpp
src/shader_recompiler/frontend/structured_control_flow.cpp
src/shader_recompiler/guest_tsharp_array.h
src/shader_recompiler/ir/ir_emitter.cpp
src/shader_recompiler/ir/ir_emitter.h
src/shader_recompiler/ir/opcodes.inc
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
src/shader_recompiler/ir/passes/image_phi_preflight.h
src/shader_recompiler/ir/passes/indexed_image_selector.h
src/shader_recompiler/ir/passes/phi_simplification_pass.cpp
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/ir/passes/resource_pass.h
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/sharp_dword_mask.h
src/shader_recompiler/ir/passes/srt_host_barrier.h
src/shader_recompiler/ir/passes/srt_inline_read_codegen.h
src/shader_recompiler/ir/passes/srt_offset_preflight.h
src/shader_recompiler/ir/passes/srt_proven_identity.h
src/shader_recompiler/ir/passes/srt_uniformity.h
src/shader_recompiler/ir/program.cpp
src/shader_recompiler/nonuniform_image_features.h
src/shader_recompiler/profile.h
src/shader_recompiler/resource.h
src/shader_recompiler/sharp_fetch_bounds.h
src/shader_recompiler/info.h
src/shader_recompiler/specialization.h
src/shader_recompiler/strided_material_specialization.h
src/video_core/renderer_vulkan/vk_pipeline_cache.h
src/shader_recompiler/targeted_shader_dump.h
src/video_core/amdgpu/liverpool.cpp
src/video_core/amdgpu/pm4_register_write.h
src/video_core/amdgpu/regs_depth.h
src/video_core/buffer_cache/buffer_cache.cpp
src/video_core/page_manager.cpp
src/video_core/protection_range_coalescer.h
src/video_core/renderer_vulkan/resource_audit_hash_filter.h
src/video_core/renderer_vulkan/vk_compute_pipeline.cpp
src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp
src/video_core/renderer_vulkan/vk_instance.cpp
src/video_core/renderer_vulkan/vk_instance.h
src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
src/video_core/renderer_vulkan/vk_pipeline_common.cpp
src/video_core/renderer_vulkan/vk_pipeline_common.h
src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
src/video_core/renderer_vulkan/vk_got_menu_pass_trace.h
src/video_core/renderer_vulkan/vk_got_menu_stage_probe.h
src/video_core/renderer_vulkan/vk_got_scene_depth_ab.h
src/video_core/renderer_vulkan/vk_got_scene_gpu_queries.h
src/video_core/renderer_vulkan/vk_got_indirect_command_proof.h
src/video_core/renderer_vulkan/vk_got_indirect_gpu_readback.h
src/video_core/renderer_vulkan/vk_got_active_menu_draws.h
src/video_core/renderer_vulkan/vk_buffer_hazard_rules.h
src/video_core/renderer_vulkan/vk_runtime.cpp
src/video_core/renderer_vulkan/vk_presenter.cpp
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_rasterizer.h
src/video_core/renderer_vulkan/vk_resource_pool.cpp
src/video_core/renderer_vulkan/vk_resource_pool.h
src/video_core/texture_cache/image_info.cpp
src/video_core/texture_cache/texture_cache.cpp
src/video_core/texture_cache/texture_cache.h
GOT_PINNED_OVERLAY_FILES
test "$(wc -l < "$RUN/info/overlay-files.txt")" -eq 73 || {
    STATUS=UNEXPECTED_CANDIDATE_OVERLAY_SIZE; exit 2;
}

STATUS=PREFLIGHT_ALL_SOURCE_BLOBS
while IFS= read -r rel; do
    target_blob="$(git -C "$SOURCE" rev-parse "$CANDIDATE:$rel")" || exit 2
    mkdir -p "$RUN/tmp/staged/$(dirname "$rel")"
    git -C "$SOURCE" show "$CANDIDATE:$rel" > "$RUN/tmp/staged/$rel" || exit 2
    test "$(git -C "$SOURCE" hash-object "$RUN/tmp/staged/$rel")" = "$target_blob" || {
        STATUS=STAGED_BLOB_MISMATCH; exit 2;
    }
    base_blob="$(git -C "$SOURCE" rev-parse --verify "$BASE:$rel" 2>/dev/null || true)"
    if [ -e "$SOURCE/$rel" ] || [ -L "$SOURCE/$rel" ]; then
        test -f "$SOURCE/$rel" && test ! -L "$SOURCE/$rel" || {
            STATUS=UNEXPECTED_SOURCE_SYMLINK; exit 2;
        }
        actual_blob="$(git -C "$SOURCE" hash-object "$SOURCE/$rel")"
        if [ -z "$base_blob" ]; then
            echo "LOCAL_FILE_NOT_IN_BASE_REF=$rel actual=$actual_blob"
            echo "SOURCE_OVERRIDE=$rel actual=$actual_blob base=NOT_IN_BASE" \
                >> "$RUN/info/preexisting-source-overrides.txt"
        elif [ "$actual_blob" != "$base_blob" ]; then
            # The last GoT runs left source overlays behind. Preserve their
            # exact bytes with cp -p at staging and restore regardless of
            # whether they match a previously known trial commit. No reset.
            echo "PRESERVE_EXISTING_LOCAL_SOURCE=$rel actual=$actual_blob base=$base_blob"
            echo "SOURCE_OVERRIDE=$rel actual=$actual_blob base=$base_blob" \
                >> "$RUN/info/preexisting-source-overrides.txt"
        fi
    else
        test -z "$base_blob" || {
            echo "MISSING_BASELINE_SOURCE=$rel"
            STATUS=SOURCE_PRECHECK_FAILED
            exit 2
        }
    fi
done < "$RUN/info/overlay-files.txt"
echo "SOURCE_PREFLIGHT=PASS files=73"
if [ -f "$RUN/info/preexisting-source-overrides.txt" ]; then
    echo "PREEXISTING_SOURCE_OVERRIDES=$(wc -l < "$RUN/info/preexisting-source-overrides.txt")"
    # Names and hashes only; no private local source content in the report.
    cat "$RUN/info/preexisting-source-overrides.txt"
else
    echo "PREEXISTING_SOURCE_OVERRIDES=0"
fi

STATUS=HOST_TESTS
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_bounds.cpp" \
    > "$RUN/tmp/test_sharp_fetch_bounds.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror \
    -I"$RUN/tmp/staged/src" "$RUN/tmp/test_sharp_fetch_bounds.cpp" \
    -o "$RUN/tmp/test-sharp-fetch-bounds" > "$RUN/host-tests.log" 2>&1 || {
        tail -100 "$RUN/host-tests.log"
        STATUS=HOST_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-sharp-fetch-bounds" | tee -a "$RUN/host-tests.log" || {
    STATUS=HOST_TEST_FAILED; exit 2;
}
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_guest_tsharp_array.cpp" \
    > "$RUN/tmp/test_guest_tsharp_array.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" "$RUN/tmp/test_guest_tsharp_array.cpp" \
    -o "$RUN/tmp/test-guest-tsharp-array" >>"$RUN/host-tests.log" 2>&1 || {
        tail -100 "$RUN/host-tests.log"
        STATUS=GUEST_ARRAY_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-guest-tsharp-array" | tee -a "$RUN/host-tests.log" || {
    STATUS=GUEST_ARRAY_TEST_FAILED; exit 2;
}

# Prove the real recorded 5+11 guest sources transition from zero-record
# preliminary material tables to complete 97-record tables. Host-only.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_strided_material_specialization.cpp" \
    > "$RUN/tmp/test_strided_material_specialization.cpp"
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_strided_material_specialization.cpp" \
    -o "$RUN/tmp/test-strided-material" >>"$RUN/host-tests.log" 2>&1 || {
        tail -100 "$RUN/host-tests.log"
        STATUS=STRIDED_MATERIAL_TEST_COMPILE_FAILED
        exit 2
    }
"$RUN/tmp/test-strided-material" | tee -a "$RUN/host-tests.log" || {
    STATUS=STRIDED_MATERIAL_TEST_FAILED
    exit 2
}


# Existing affine/indexed resource matcher + GPU attribute selector:
# real source headers, synthetic Shader::IR producers, no emulator.
MAGIC_ENUM_INC=""
for dir in "$SOURCE/externals/magic_enum/include" "$SOURCE/externals/magic_enum"; do
    if [ -f "$dir/magic_enum/magic_enum.hpp" ]; then
        MAGIC_ENUM_INC="$dir"
        break
    fi
done
FMT_INC=""
for dir in "$SOURCE/externals/fmt/include" "$SOURCE/externals/fmt"; do
    if [ -f "$dir/fmt/format.h" ]; then
        FMT_INC="$dir"
        break
    fi
done
if [ ! -f "$SOURCE/src/shader_recompiler/ir/opcodes.h" ]; then
    echo "MENU_SELECTOR_OPCODE_HEADER_MISSING=$SOURCE/src/shader_recompiler/ir/opcodes.h"
    STATUS=MENU_SELECTOR_OPCODE_HEADER_MISSING
    exit 2
fi
if [ -z "$MAGIC_ENUM_INC" ] || [ -z "$FMT_INC" ]; then
    STATUS=MENU_SELECTOR_VENDOR_INCLUDE_MISSING
    echo "MAGIC_ENUM_INC=$MAGIC_ENUM_INC FMT_INC=$FMT_INC"
    exit 2
fi
for pair in \
    test_indexed_image_selector:indexed-image-selector \
    test_menu_attribute_tsharp:menu-attribute-tsharp; do
    name="$(printf '%s' "$pair" | cut -d: -f1)"
    out="$(printf '%s' "$pair" | cut -d: -f2)"
    git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/$name.cpp" \
        > "$RUN/tmp/$name.cpp" || { STATUS=MENU_TEST_SOURCE_MISSING; exit 2; }
    # Stage overrides take precedence, but unmodified headers remain in
    # the actual source worktree. Missing -I SOURCE caused the prior failure.
    c++ -std=c++23 -O2 -Wall -Wextra -Werror -I"$RUN/tmp/staged/src" \
        -I"$SOURCE/src" -I"$MAGIC_ENUM_INC" -I"$FMT_INC" \
        "$RUN/tmp/$name.cpp" -o "$RUN/tmp/$out" \
        >>"$RUN/host-tests.log" 2>&1 || {
            tail -100 "$RUN/host-tests.log"
            STATUS=MENU_SELECTOR_TEST_BUILD_FAILED
            exit 2
        }
    "$RUN/tmp/$out" | tee -a "$RUN/host-tests.log" || {
        STATUS=MENU_SELECTOR_TEST_FAILED
        exit 2
    }
done


# If the scene geometry gate is broadened accidentally, stop BEFORE any
# shadPS4 build or guest GPU execution. Pure, standalone host regression.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_got_scene_depth_ab.cpp" \
    > "$RUN/tmp/test_got_scene_depth_ab.cpp" || {
        STATUS=GOT_AB_TEST_SOURCE_MISSING; exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_got_scene_depth_ab.cpp" \
    -o "$RUN/tmp/test-got-scene-depth-ab" \
    >>"$RUN/host-tests.log" 2>&1 || {
        tail -90 "$RUN/host-tests.log"
        STATUS=GOT_AB_TEST_COMPILE_FAILED
        exit 2
    }
"$RUN/tmp/test-got-scene-depth-ab" | tee -a "$RUN/host-tests.log" || {
    STATUS=GOT_AB_TEST_FAILED
    exit 2
}
echo "GOT_SCENE_DEPTH_AB_TEST=PASS" | tee -a "$RUN/host-tests.log"

# New independently-gated face-culling A/B: 100000 deterministic cases.
# It must NEVER touch fullscreen composition, unrelated games, shader
# compilation, or depth-compare state unless explicitly opt-in.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_got_scene_raster_ab.cpp" \
    >"$RUN/tmp/test_got_scene_raster_ab.cpp" || {
      STATUS=SCENE_RASTER_TEST_SOURCE_MISSING; exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_got_scene_raster_ab.cpp" \
    -o "$RUN/tmp/test-got-scene-raster-ab" >>"$RUN/host-tests.log" 2>&1 || {
      tail -100 "$RUN/host-tests.log"
      STATUS=SCENE_RASTER_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-got-scene-raster-ab" | tee -a "$RUN/host-tests.log" || {
    STATUS=SCENE_RASTER_TEST_FAILED; exit 2;
}
echo "SCENE_RASTER_AB_REGRESSION=PASS" | tee -a "$RUN/host-tests.log"

# Host-side proof of the actual Vulkan argument layout and count-buffer
# interpretation (100,000 pseudorandom cases). This runs BEFORE any shader
# build, Vulkan GPU use, source staging or shutdown of Ollama.
git -C "$SOURCE" show \
    "$CANDIDATE:tests/shader_recompiler/test_got_indirect_gpu_args.cpp" \
    >"$RUN/tmp/test_got_indirect_gpu_args.cpp" || {
      STATUS=INDIRECT_GPU_ARG_TEST_SOURCE_MISSING;exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_got_indirect_gpu_args.cpp" \
    -o "$RUN/tmp/test-got-indirect-gpu-args" >>"$RUN/host-tests.log" 2>&1 || {
      tail -110 "$RUN/host-tests.log"
      STATUS=INDIRECT_GPU_ARG_TEST_COMPILE_FAILED;exit 2;
    }
"$RUN/tmp/test-got-indirect-gpu-args" | tee -a "$RUN/host-tests.log" || {
    STATUS=INDIRECT_GPU_ARG_TEST_FAILED;exit 2;
}
echo "GOT_INDIRECT_GPU_ARGS_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"

# Checkpoint-based GPU selector gate: test 100k scope exclusions, both
# high-volume scene shader pairs and all three direct controls. Crucially,
# the indirect register num_indices=3 is informational and never a gate.
git -C "$SOURCE" show \
    "$CANDIDATE:tests/shader_recompiler/test_got_active_menu_draws.cpp" \
    >"$RUN/tmp/test_got_active_menu_draws.cpp" || {
      STATUS=ACTIVE_MENU_SELECTOR_TEST_SOURCE_MISSING; exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_got_active_menu_draws.cpp" \
    -o "$RUN/tmp/test-got-active-menu-draws" >>"$RUN/host-tests.log" 2>&1 || {
      tail -100 "$RUN/host-tests.log"
      STATUS=ACTIVE_MENU_SELECTOR_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-got-active-menu-draws" | tee -a "$RUN/host-tests.log" || {
    STATUS=ACTIVE_MENU_SELECTOR_TEST_FAILED; exit 2;
}
echo "GOT_ACTIVE_MENU_SELECTOR_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"

# Test the real vk::AccessFlags2 and vk::PipelineStageFlags2 constants
# from this project's pinned Vulkan-Headers, not a fake numeric substitute.
# This catches the former missing indirect-command read and wrong stage.
VKH="$SOURCE/externals/vulkan-headers/include"
if [ ! -f "$VKH/vulkan/vulkan.hpp" ]; then
    STATUS=VULKAN_HEADERS_MISSING_FOR_WAR_TEST
    echo "MISSING_VULKAN_HPP=$VKH/vulkan/vulkan.hpp"
    exit 2
fi
git -C "$SOURCE" show \
    "$CANDIDATE:tests/shader_recompiler/test_indirect_buffer_war_hazard.cpp" \
    > "$RUN/tmp/test_indirect_buffer_war_hazard.cpp" || {
        STATUS=INDIRECT_WAR_TEST_SOURCE_MISSING;exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" -I"$VKH" \
    "$RUN/tmp/test_indirect_buffer_war_hazard.cpp" \
    -o "$RUN/tmp/test-indirect-war" >>"$RUN/host-tests.log" 2>&1 || {
        tail -120 "$RUN/host-tests.log"
        STATUS=INDIRECT_WAR_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-indirect-war" | tee -a "$RUN/host-tests.log" || {
    STATUS=INDIRECT_WAR_TEST_FAILED;exit 2;
}
# Structural preflight: both production callers MUST use the tested mask
# and draw-indirect stage; never accept a header test disconnected from
# the code that actually makes Vulkan barriers.
python3 - "$RUN/tmp/staged/src" <<'PYWARPROOF'
from pathlib import Path
import sys
root=Path(sys.argv[1])/"video_core"/"renderer_vulkan"
runtime=(root/"vk_runtime.cpp").read_text()
rasterizer=(root/"vk_rasterizer.cpp").read_text()
rules=(root/"vk_buffer_hazard_rules.h").read_text()
assert "constexpr vk::AccessFlags2 READ_MASK = BufferHazards::ReadMask;" in runtime
assert "resource->read_ranges.Add(range);" in runtime
assert "resource->read_ranges.Overlaps(offset,offset+size)" in runtime
assert "BufferHazards::StageForBoundBuffer(src_access,is_compute)" in rasterizer
assert "vk::AccessFlagBits2::eIndirectCommandRead" in rules
assert "vk::PipelineStageFlagBits2::eDrawIndirect" in rules
# The previous host link failed at rasterizer.cpp:492: an undefined
# VideoCore::BufferTypeName diagnostic helper. Reject that regression
# before compilation while preserving readable buffer-backend labels.
assert "VideoCore::BufferTypeName(" not in rasterizer
assert "backing_type_name(buffer->mem_type)" in rasterizer
for name in ("DeviceLocal", "HostUncached", "HostCached", "Stream", "Sparse"):
    assert "case VideoCore::MemoryType::"+name+":" in rasterizer
print("GOT_INDIRECT_BACKING_LOGGER_SOURCE=PASS five_enum_names=YES undefined_helper=NO")
print("GOT_INDIRECT_WAR_PRODUCTION_WIRING=PASS "
      "runtime_read_tracking=PASS "
      "rasterizer_draw_indirect_stage=PASS "
      "actual_vulkan_bits_tested=PASS")
PYWARPROOF
echo "GOT_INDIRECT_WAR_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"

echo "HOST_ONLY_TESTS=PASS" | tee -a "$RUN/host-tests.log"

# Stage only verified content. All 73 file checks completed before the first write.
STATUS=STAGING_CANDIDATE
while IFS= read -r rel; do
    if [ -e "$SOURCE/$rel" ]; then
        mkdir -p "$RUN/tmp/original/$(dirname "$rel")"
        cp -p -- "$SOURCE/$rel" "$RUN/tmp/original/$rel"
        echo "RESTORE|$rel" >> "$RUN/info/restore-operations.txt"
    else
        echo "REMOVE|$rel" >> "$RUN/info/restore-operations.txt"
    fi
    mkdir -p "$(dirname "$SOURCE/$rel")"
    cp -p -- "$RUN/tmp/staged/$rel" "$SOURCE/$rel"
    touch "$SOURCE/$rel"
done < "$RUN/info/overlay-files.txt"

STATUS=CONFIGURING_OFFLINE
export CCACHE_DIR="$GOT/cache/ccache"
export CCACHE_MAXSIZE=8G CCACHE_COMPRESS=true CCACHE_BASEDIR="$GOT"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=OFF -DCMAKE_C_COMPILER_LAUNCHER=ccache \
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    > "$RUN/info/configure.log" 2>&1 || {
        tail -120 "$RUN/info/configure.log"
        STATUS=CONFIGURE_FAILED; exit 2;
    }
# Fail fast on the exact source file that failed at 141/143 in the
# previous run, without waiting for the other shader/compiler objects.
# Ninja's object path is taken from that run's actual build log.
STATUS=BUILDING_VK_RASTERIZER_OBJECT_FIRST
nice -n 10 cmake --build "$BUILD" \
    --target CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o \
    --parallel 2 > "$RUN/info/vk-rasterizer-object-build.log" 2>&1 || {
        tail -120 "$RUN/info/vk-rasterizer-object-build.log"
        STATUS=VK_RASTERIZER_OBJECT_BUILD_FAILED
        exit 2
    }
echo "VK_RASTERIZER_OBJECT_BUILD=PASS" | tee -a "$RUN/host-tests.log"

STATUS=BUILDING_JOBS_2
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$RUN/build.log" 2>&1 || {
        tail -120 "$RUN/build.log"
        STATUS=SHADPS4_BUILD_FAILED; exit 2;
    }
test -x "$BIN" || { STATUS=BINARY_MISSING_AFTER_BUILD; exit 2; }
sha256sum "$BIN" | tee "$RUN/info/candidate-binary.sha256"
CANDIDATE_BUILT=1
STATUS=HOST_ONLY_BUILD_PASS
echo "GOT_INDIRECT_WAR_HAZARD_FIX_COMPILED=YES GAME_LAUNCHED=NO VULKAN_DRIVER_NOT_EXERCISED=YES"
echo "SOURCE_AND_ORIGINAL_BINARY_RESTORE=AUTOMATIC"
