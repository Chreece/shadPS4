#!/usr/bin/env bash
# Read-only exact local GoT shader source and baseline snapshot for upstream port.
# No game, GPU, build, checkout, restarts, saved-game or display changes.
set -Eeuo pipefail
umask 077
ROOT="${SHADPS4_ROOT:-$HOME/projects/shadps4}"
GOT="$ROOT/GhostOfTsushima"
SRC="${GOT_SOURCE_OVERRIDE:-$GOT/experiments/c925-integrated-gpu-20261027-122701/source}"
LOGS="$ROOT/logs"
TAG="got-shader-parity-$(date +%Y%m%d-%H%M%S)-$BASHPID"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
mkdir -p "$OUT" "$ROOT/diary"
STATUS="STARTED"
finish() {
    local rc=$?
    trap - EXIT
    printf 'STATUS=%s\nEXIT_CODE=%s\nSOURCE=%s\nGPU_OR_GAME_LAUNCHED=NO\nSSH_SESSION=OPEN\n' \
      "$STATUS" "$rc" "$SRC" > "$OUT/result.txt"
    tar -czf "$ARCHIVE" -C "$LOGS" "$TAG" || true
    echo "RESULT=$STATUS"
    echo "UPLOAD_THIS=$ARCHIVE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT
for c in git python3 tar; do command -v "$c" >/dev/null || { echo "MISSING=$c"; exit 3; }; done
[[ -d "$SRC" && ! -L "$SRC" ]] &&
    [[ "$(git -C "$SRC" rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]] || {
    echo "LOCAL_C925_SOURCE_NOT_PRESENT=$SRC"; STATUS="SOURCE_NOT_PRESENT"; exit 4;
}
STATUS="COLLECT_SOURCE_ONLY_NO_BINARY_UPLOAD"
python3 - "$SRC" "$OUT" "$GOT" "$ROOT" <<'PY'
import hashlib, json, pathlib, shutil, subprocess, sys
source, dest, got, root = (pathlib.Path(p).expanduser().resolve() for p in sys.argv[1:])
FILES='''
src/shader_recompiler/info.h
src/shader_recompiler/profile.h
src/shader_recompiler/resource.h
src/shader_recompiler/runtime_info.h
src/shader_recompiler/specialization.h
src/shader_recompiler/compact_image_indices.h
src/shader_recompiler/backend/spirv/emit_spirv.cpp
src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp
src/shader_recompiler/backend/spirv/emit_spirv_image.cpp
src/shader_recompiler/backend/spirv/emit_spirv_instructions.h
src/shader_recompiler/backend/spirv/spirv_emit_context.cpp
src/shader_recompiler/backend/spirv/spirv_emit_context.h
src/shader_recompiler/frontend/translate/export.cpp
src/shader_recompiler/ir/opcodes.inc
src/shader_recompiler/ir/ir_emitter.cpp
src/shader_recompiler/ir/ir_emitter.h
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/resource_pass.h
src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
src/shader_recompiler/ir/srt_gvn_table.h
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_rasterizer.h
src/video_core/renderer_vulkan/vk_instance.cpp
src/video_core/renderer_vulkan/vk_instance.h
src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
src/video_core/renderer_vulkan/vk_pipeline_cache.h
src/video_core/renderer_vulkan/vk_pipeline_common.cpp
src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
src/video_core/renderer_vulkan/vk_compute_pipeline.cpp
src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp
src/video_core/renderer_vulkan/vk_runtime.cpp
src/video_core/renderer_vulkan/vk_dispatch_helper.cpp
src/video_core/texture_cache/sampler.cpp
src/video_core/texture_cache/texture_cache.cpp
src/video_core/texture_cache/texture_cache.h
src/video_core/texture_cache/types.h
src/video_core/buffer_cache/buffer_cache.cpp
src/video_core/buffer_cache/buffer_cache.h
src/video_core/amdgpu/regs_color.h
src/video_core/amdgpu/regs_shader.h
src/video_core/amdgpu/liverpool.cpp
src/video_core/amdgpu/liverpool.h
src/core/memory.cpp
'''.split()

def run(*args, check=False, timeout=25):
    return subprocess.run(args, cwd=source, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=check)

commit=run('git','rev-parse','HEAD',check=True).stdout.decode().strip()
metadata={"base_head":commit,"candidate_files":len(FILES),"current":[],"baseline":[],"missing":[],"skipped":[],"binary_hashes":{},"total_current_bytes":0}
for name,command in [('git-status.txt',('git','status','--porcelain=v1','--untracked-files=no')),
                     ('git-history.txt',('git','log','--all','--format=%h %ad %s','--date=iso','-35')),
                     ('git-reflog.txt',('git','reflog','-35','--date=iso')),
                     ('git-current-diff.patch',('git','diff','--no-ext-diff','--binary','HEAD','--',*FILES)),
                     ('git-index-diff.patch',('git','diff','--no-ext-diff','--binary','--cached','HEAD','--',*FILES))]:
    p=run(*command,timeout=55)
    (dest/name).write_bytes(p.stdout + (b'\nGIT_READ_ERROR: '+p.stderr if p.returncode else b''))

for rel in FILES:
    p=source/rel
    if not p.is_file() or p.is_symlink():
        metadata['missing'].append(rel)
    elif p.stat().st_size > 1_500_000 or metadata['total_current_bytes'] + p.stat().st_size > 18_000_000:
        metadata['skipped'].append(rel)
    else:
        target=dest/'current'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target,follow_symlinks=False)
        metadata['total_current_bytes']+=p.stat().st_size
        metadata['current'].append({'path':rel,'bytes':p.stat().st_size,
                                    'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    baseline=run('git','show','HEAD:'+rel)
    if baseline.returncode==0 and len(baseline.stdout) <= 1_500_000:
        target=dest/'baseline'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(baseline.stdout)
        metadata['baseline'].append({'path':rel,
                                     'sha256':hashlib.sha256(baseline.stdout).hexdigest()})

# Only check hashes, never archive/copy any executable, game data or user profile.
for name,rel in {
 'got-1540-typed-positive':'candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4',
 'got-1959-performance-positive':'candidates/c925-20261027-195934-performance-positive/shadps4',
 'got-2027-cbmask-candidate':'candidates/c925-20261027-202701-cb-shader-mask-preserved/shadps4',
}.items():
    p=got/rel
    if not p.is_file() or p.is_symlink():
        metadata['binary_hashes'][name]='NOT_FOUND'
        continue
    sha=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):
            sha.update(block)
    metadata['binary_hashes'][name]=sha.hexdigest()
metadata['head_after']=run('git','rev-parse','HEAD',check=True).stdout.decode().strip()
metadata['source_head_stable']=metadata['base_head']==metadata['head_after']
(dest/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
print('EXACT_GOT_LOCAL_SOURCE_COMMIT='+commit,flush=True)
print('SOURCE_HEAD_STABLE='+str(metadata['source_head_stable']),flush=True)
print('CAPTURED_SOURCE_FILES='+str(len(metadata['current'])),flush=True)
print('CAPTURED_BASELINE_FILES='+str(len(metadata['baseline'])),flush=True)
print('MISSING_SOURCE_FILES='+str(len(metadata['missing'])),flush=True)
for k,v in metadata['binary_hashes'].items():print('PROTECTED_'+k+'='+v,flush=True)
PY
STATUS="SOURCE_PARITY_CAPTURED_NO_GAME_RUN"
python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$ARCHIVE" <<'PYDIARY' || true
import fcntl,json,os,sys
path,tag,archive=sys.argv[1:]
row={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-28",
     "record_type":"GOT_NATIVE_SHADER_SOURCE_PARITY_READ_ONLY",
     "game_id":"CUSA11456","tested_games":[],"game_effect":"NOT_RUN",
     "archive":os.path.basename(archive),"result":"SOURCE_CAPTURE_ONLY",
     "decision":"PRESERVE_POSITIVE_GOT_TYPED_SOURCE_BEFORE_UNIFIED_UPSTREAM_SHADER_PORT",
     "next_test":"Port exact current GoT type-correct 2D/3D/Cube and dynamic image/sampler SHARP code onto native-verified PR94, resolving upstream #5503/#5441 overlaps"}
with open(path,'a',encoding='utf8') as f:
    fcntl.flock(f.fileno(),fcntl.LOCK_EX)
    f.write(json.dumps(row,separators=(',',':'))+'\n')
    f.flush();os.fsync(f.fileno())
PYDIARY
