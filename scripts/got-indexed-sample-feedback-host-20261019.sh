#!/usr/bin/env bash
# Build recovered shader-resource repairs and bounded material feedback. No game, Vulkan init, service changes,
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
CANDIDATE="9a141e842c48f9e8872139e7d4dfc809441749f3"
BRANCH="fix/gcn-legacy-reciprocal-code-20261020"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="got-indexed-sample-feedback-host-$STAMP"
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
        cp -p -- "$BIN" "$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$CANDIDATE"
        sha256sum "$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$CANDIDATE" \
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
# Complete pinned overlay; preserve and restore every original source file.
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
src/shader_recompiler/frontend/translate/data_share.cpp
src/shader_recompiler/frontend/translate/vector_memory.cpp
src/shader_recompiler/frontend/translate/vector_alu.cpp
src/shader_recompiler/guest_tsharp_array.h
src/shader_recompiler/buffer_table_span.h
src/shader_recompiler/indexed_image_feedback.h
src/shader_recompiler/ir/ir_emitter.cpp
src/shader_recompiler/ir/ir_emitter.h
src/shader_recompiler/ir/opcodes.inc
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
src/shader_recompiler/ir/passes/image_phi_preflight.h
src/shader_recompiler/ir/passes/ir_passes.h
src/shader_recompiler/ir/passes/indexed_image_selector.h
src/shader_recompiler/ir/passes/phi_simplification_pass.cpp
src/shader_recompiler/ir/passes/lds_singleton_divergence.h
src/shader_recompiler/ir/passes/shared_memory_barrier_pass.cpp
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/ir/passes/resource_pass.h
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/sharp_dword_mask.h
src/shader_recompiler/ir/passes/srt.h
src/shader_recompiler/ir/passes/srt_host_barrier.h
src/shader_recompiler/ir/passes/srt_inline_read_codegen.h
src/shader_recompiler/ir/passes/srt_offset_preflight.h
src/shader_recompiler/ir/passes/srt_proven_identity.h
src/shader_recompiler/ir/passes/srt_uniformity.h
src/shader_recompiler/ir/program.cpp
src/shader_recompiler/nonuniform_image_features.h
src/shader_recompiler/profile.h
src/shader_recompiler/resource.h
src/shader_recompiler/recompiler.cpp
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
src/video_core/buffer_cache/buffer_cache.h
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
src/video_core/renderer_vulkan/vk_got_indirect_producer_trace.h
src/video_core/renderer_vulkan/vk_got_dma_origin_probe.h
src/video_core/renderer_vulkan/vk_got_gds_compute_probe.h
src/video_core/renderer_vulkan/vk_runtime.cpp
src/video_core/renderer_vulkan/vk_runtime.h
src/video_core/renderer_vulkan/vk_scheduler.h
src/video_core/renderer_vulkan/vk_scheduler.cpp
src/video_core/renderer_vulkan/vk_frame_probe.h
src/video_core/renderer_vulkan/vk_frame_probe_images.h
src/video_core/renderer_vulkan/vk_presenter.cpp
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_rasterizer.h
src/video_core/renderer_vulkan/vk_resource_pool.cpp
src/video_core/renderer_vulkan/vk_resource_pool.h
src/video_core/texture_cache/image_info.cpp
src/video_core/texture_cache/texture_cache.cpp
src/video_core/texture_cache/texture_cache.h
src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp
src/shader_recompiler/ir/operand_helper.h
src/shader_recompiler/ir/reg.h
src/shader_recompiler/ir/passes/lower_buffer_format_to_raw.cpp
src/shader_recompiler/frontend/translate/vector_interpolation.cpp
src/shader_recompiler/frontend/translate/scalar_alu.cpp
src/video_core/buffer_cache/fault_manager.cpp
src/video_core/host_shaders/fault_buffer_process.comp
src/shader_recompiler/backend/spirv/emit_spirv_special.cpp
src/shader_recompiler/vertex_output_feedback.h
GOT_PINNED_OVERLAY_FILES
test "$(wc -l < "$RUN/info/overlay-files.txt")" -eq 102 || {
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
echo "SOURCE_PREFLIGHT=PASS files=$(wc -l < "$RUN/info/overlay-files.txt")"
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
STATUS=PRODUCTION_SOURCE_PREFLIGHT
if python3 - "$RUN/tmp/staged/src" > "$RUN/info/source-preflight.log" 2>&1 <<'PYWARPROOF'
from pathlib import Path
import re
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
producer=(root/"vk_got_indirect_producer_trace.h").read_text()
cache=(Path(sys.argv[1])/"video_core"/"buffer_cache"/"buffer_cache.cpp").read_text()
assert "ObserveZeroIndirectAddress" in producer
assert "ObservedIndirectProducerHits" in rasterizer
assert "observed_indirect_addresses" in producer
assert "SHADPS4_GOT_INDIRECT_PRODUCERS" in producer
assert "GotIndirectProducer: path=COMPUTE_DISPATCH_SUBMITTED" in rasterizer
assert "GotIndirectProducer: path=WRITABLE_SHADER_BIND" in rasterizer
assert "GotIndirectProducer: path=CPU_TO_GPU_UPLOAD_RANGE" in cache
assert "GotIndirectProducer: path=VULKAN_COPY_SUBMITTED" in runtime
assert "vk_got_dma_origin_probe.h" in rasterizer
assert "Diagnostics::CaptureGotDmaGpuWords(" in rasterizer
assert "GotDmaWordProbeEnabled()" in (
    root/"vk_got_dma_origin_probe.h").read_text()
assert "SelectGotDmaWordCandidate(" in producer
print("GOT_DMA_GPU_WORD_PRODUCTION_WIRING=PASS "
      "source_and_destination_readbacks=YES mutation=NO")
gds_src=(root/"vk_got_gds_compute_probe.h").read_text()
cache_header=(Path(sys.argv[1])/"video_core"/"buffer_cache"/"buffer_cache.h").read_text()
assert "Diagnostics::CaptureGotGdsComputeProducer(" in rasterizer
assert "gds_buf->Invalidate(gds_offset, sizeof(u32));" in rasterizer
assert rasterizer.index("gds_buf->Invalidate(gds_offset, sizeof(u32));") < rasterizer.index(
    "std::memcpy(&value, gds_buf->mapped_data.data() + gds_offset, sizeof(value));")
assert "Buffer* GetGdsBuffer() noexcept" in cache_header
assert "const Buffer* GetGdsBuffer() const noexcept" in cache_header
assert "SHADPS4_GOT_GDS_PRODUCER_PROBE" in gds_src
assert "runtime.CopyBuffer(gds,&sample->staging" in gds_src
for name in ("DispatchDirect", "DispatchIndirect"):
    start=rasterizer.index("void Rasterizer::"+name+"(")
    body=rasterizer[start:rasterizer.index("\n}", start)]
    reset=list(re.finditer(r"^\s+ResetBindings\s*\(\s*true\s*\)\s*;", body, re.M))
    capture=list(re.finditer(r"^\s+Diagnostics::CaptureGotGdsComputeProducer\s*\(", body, re.M))
    assert len(reset) == 1, name+": expected one ResetBindings(true) call"
    assert len(capture) == 1, name+": expected one GDS capture call"
    assert reset[0].start() < capture[0].start(), name+": GDS capture must follow ResetBindings(true)"
print("GOT_GDS_HOST_COHERENCE_AND_LIVE_PRODUCER_WIRING=PASS "
      "bounds_check=YES cache_invalidate_before_read=YES "
      "dispatch_readback_after_shader_access_recorded=YES")
lds=(Path(sys.argv[1])/"shader_recompiler"/"ir"/"passes"/"shared_memory_barrier_pass.cpp").read_text()
phi=(Path(sys.argv[1])/"shader_recompiler"/"ir"/"passes"/"phi_simplification_pass.cpp").read_text()
serial=(root/"vk_pipeline_serialization.cpp").read_text()
assert "IsVaryingLocalInvocationComponent(" in lds
assert "FindDivergentLoops(program.syntax_list, runtime_info)" in lds
assert "EmitBarrierInMergeBlock(node.data, divergence_end, divergence_depth, runtime_info)" in lds
assert "op == IR::Opcode::InverseBallot || op == IR::Opcode::GetUserData" in phi
assert "ShaderBinaryVersion = 62u;" in serial
scalar = (Path(sys.argv[1]) / "shader_recompiler/frontend/translate/scalar_alu.cpp").read_text()
assert "ir.SetScc(ir.InverseBallot(result));" not in scalar
for name in ("S_AND_B64", "S_OR_B64", "S_NOT_B64", "S_SAVEEXEC_B64"):
    body = scalar.split("void Translator::" + name + "(", 1)[1].split("\n}", 1)[0]
    assert "ir.SetScc(ir.INotEqual(result, ir.Imm64(u64(0))));" in body, name
print("GOT_SCALAR_CONDITION_WIRING=PASS whole_result_nonzero=YES")
fault = (Path(sys.argv[1]) / "video_core/buffer_cache/fault_manager.cpp").read_text()
fault_parser = (Path(sys.argv[1]) / "video_core/host_shaders/fault_buffer_process.comp").read_text()
buffer_cache = (Path(sys.argv[1]) / "video_core/buffer_cache/buffer_cache.cpp").read_text()
assert "ObtainBufferFlags::IgnoreStreamBuffer" in fault
assert "download_buffer.Flush(offset, PageFaultAreaSize)" in fault
assert "download_buffer.Invalidate(offset, PageFaultAreaSize)" in fault
assert "std::min<u64>(fault_buf[0], MaxPageFaults - 1)" in fault
assert "atomicOr(fault_buffer[id], word)" in fault_parser
assert "runtime.FillBuffer(fault_buffer, 0u, fault_buffer->SizeBytes(), 0u)" in buffer_cache
assert "if (stage.uses_dma)" in rasterizer
assert "fault_init_barrier" in buffer_cache
print("GOT_DYNAMIC_RESOURCE_RESIDENCY=PASS persistent_fault_pages=YES bounded_readback=YES")
vertex_output = (Path(sys.argv[1]) / "shader_recompiler/backend/spirv/emit_spirv_special.cpp").read_text()
assert "RecordVertexOutput(ctx)" in vertex_output
assert "CaptureVertexOutput();" in rasterizer
print("VERTEX_OUTPUT_CAPTURE_WIRING=PASS bounded_samples=64 guest_mutation=NO")
interpolation = (Path(sys.argv[1]) / "shader_recompiler/frontend/translate/vector_interpolation.cpp").read_text()
spirv_context = (Path(sys.argv[1]) / "shader_recompiler/backend/spirv/spirv_emit_context.cpp").read_text()
assert "OpAtomicOr(U32[1], fault_ptr" in spirv_context
assert interpolation.count("fs_interpolation[attr_index][inst.control.vintrp.chan]") == 2
assert "spv::Decoration::Component" in spirv_context
assert "fragment_input_components[i][component]" in spirv_context
assert "const Id pointer = param.id;" in spirv_context
assert "OpInterpolateAtVertexAMD(F32[param.num_components], pointer, ConstU32(vertex))" in spirv_context
print("GOT_COMPONENT_INTERPOLATION_WIRING=PASS per_component=YES mixed_inputs=YES")
print("GOT_SHADER_UPSTREAM_CORRECTNESS_WIRING=PASS "
      "lds_singleton_axis=YES userdata_phi_guard=YES cache_version=62")
print("GOT_INDIRECT_PRODUCER_SOURCE_WIRING=PASS observed_zero_commands=32 "
      "buffer_shader=YES compute_dispatch=YES cpu_upload=YES vk_copy=YES")
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
then
    cat "$RUN/info/source-preflight.log" | tee -a "$RUN/host-tests.log"
else
    cat "$RUN/info/source-preflight.log" | tee -a "$RUN/host-tests.log"
    STATUS=PRODUCTION_SOURCE_PREFLIGHT_FAILED
    exit 2
fi
STATUS=HOST_TESTS
echo "GOT_INDIRECT_WAR_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"

# Exact eight real 20-byte GPU argument records, 100,000 deterministic
# interval tests, overflow and gap exclusions. Host-only and no GPU access.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_got_indirect_producer_trace.cpp" \
    > "$RUN/tmp/test_got_indirect_producer_trace.cpp" || {
        STATUS=GOT_PRODUCER_TEST_SOURCE_MISSING; exit 2;
    }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$RUN/tmp/staged/src" \
    "$RUN/tmp/test_got_indirect_producer_trace.cpp" \
    -o "$RUN/tmp/test-got-indirect-producer" >>"$RUN/host-tests.log" 2>&1 || {
        tail -100 "$RUN/host-tests.log"
        STATUS=GOT_PRODUCER_TEST_COMPILE_FAILED; exit 2;
    }
"$RUN/tmp/test-got-indirect-producer" | tee -a "$RUN/host-tests.log" || {
    STATUS=GOT_PRODUCER_TEST_FAILED; exit 2;
}
echo "GOT_INDIRECT_PRODUCER_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"
echo "GOT_DMA_ADDRESS_SELECTOR_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"
echo "GOT_GDS_PRODUCER_PROBE_SELECTION_HOST_TEST=PASS" | tee -a "$RUN/host-tests.log"

# Upstream PR #5429: a LocalInvocationId axis with workgroup dimension 1
# is uniform. Prevent invalid divergence suppression of LDS barriers.
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_lds_singleton_divergence.cpp" \
  > "$RUN/tmp/test_lds_singleton_divergence.cpp" || {
    STATUS=LDS_SINGLETON_TEST_MISSING; exit 2;
  }
c++ -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
  -I"$RUN/tmp/staged/src" \
  "$RUN/tmp/test_lds_singleton_divergence.cpp" \
  -o "$RUN/tmp/test-lds-singleton" >>"$RUN/host-tests.log" 2>&1 || {
    tail -90 "$RUN/host-tests.log"
    STATUS=LDS_SINGLETON_TEST_BUILD_FAILED; exit 2;
  }
"$RUN/tmp/test-lds-singleton" | tee -a "$RUN/host-tests.log" || {
  STATUS=LDS_SINGLETON_TEST_FAILED; exit 2;
}
echo "GOT_UPSTREAM_SHADER_SEMANTICS_TEST=PASS" | tee -a "$RUN/host-tests.log"

echo "HOST_ONLY_TESTS=PASS" | tee -a "$RUN/host-tests.log"

# Stage only verified content after every file check completes.
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

STATUS=COMPILE_DISPATCH_AND_COLLECTION_OBJECTS
nice -n 10 ninja -C "$BUILD" -j 2 \
  CMakeFiles/shadps4.dir/src/video_core/amdgpu/liverpool.cpp.o \
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp.o \
  > "$RUN/info/dispatch-collection-objects.log" 2>&1 || {
    tail -100 "$RUN/info/dispatch-collection-objects.log"
    STATUS=DISPATCH_COLLECTION_OBJECTS_FAILED; exit 2;
  }
echo "DISPATCH_COLLECTION_OBJECTS=PASS" | tee -a "$RUN/host-tests.log"

STATUS=COMPILE_INDEXED_FEEDBACK_OBJECTS
nice -n 10 ninja -C "$BUILD" -j 2 \
  CMakeFiles/shadps4.dir/src/shader_recompiler/frontend/translate/vector_memory.cpp.o \
  CMakeFiles/shadps4.dir/src/shader_recompiler/backend/spirv/emit_spirv_image.cpp.o \
  CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/resource_patching_pass.cpp.o \
  CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp.o \
  > "$RUN/info/indexed-feedback-objects.log" 2>&1 || {
    tail -100 "$RUN/info/indexed-feedback-objects.log"
    STATUS=INDEXED_FEEDBACK_OBJECTS_FAILED; exit 2;
  }
echo "INDEXED_FEEDBACK_OBJECTS=PASS" | tee -a "$RUN/host-tests.log"

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
echo "INDEXED_SAMPLE_FEEDBACK_COMPILED=YES GAME_LAUNCHED=NO VULKAN_DRIVER_NOT_EXERCISED=YES"
echo "SOURCE_AND_ORIGINAL_BINARY_RESTORE=AUTOMATIC"
