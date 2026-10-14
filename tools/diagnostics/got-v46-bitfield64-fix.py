#!/usr/bin/env python3
"""GoT v46: lower 64-bit bitfield insert to legal Vulkan integer operations.

Reuses the exact v45 isolated-build/capture machinery. Preserves v45 RELEASE_MEM,
v40 bindings, all earlier source/objects and the already verified private RADV.
Only emit_spirv_integer.cpp is recompiled; no game-specific opcode bypass, driver
patch, or counter modification. Fresh capture profiles disable pipeline caches.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys
import types

PARENT_SHA = '61c99c81c0d008007da09ec34951da68023195012510e0bf9b84bd42a50653da'
V45_TREE = 'got-v45-release-data-confirm-20261014'
V45_SHA = '039c04d9135177becdb62988c1828d38bd2e46afe339c01093c677290e577d51'
V40_TREE = 'got-v40-cached-image-bindings-20261014'
V40_SHA = '0ed71b7dcc29e110b10073186785d4d463151ad2c6c316beabd2fd5bc0bfe808'
LIVERPOOL = 'src/video_core/amdgpu/liverpool.cpp'
LIVERPOOL_SHA = '80bb1db2414b52feb339196833d385b548056395a746a80ad59c85ba121713fb'
TREE = 'got-v46-bitfield64-lowering-20261014'
LABEL = 'got-v46-bitfield64-visual'
SOURCE = 'src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp'
SOURCE_SHA = '67572039b9e523ade3ceaedaf4ab100f9440d8a274ce3ca364a843a1cf1183bf'
FIXED_SHA = '655e4bfa8c37a849c9fe7ddb11d2cfb7db2f3a20cea42b1343970524b2e6ae05'
KIND = 'VULKAN_BITFIELD_INSERT64_LOWERING'
BEFORE = '''Id EmitBitFieldInsert64(EmitContext& ctx, Id base, Id insert, Id offset, Id count) {
    return ctx.OpBitFieldInsert(ctx.U64, base, insert, offset, count);
}'''
AFTER = '''Id EmitBitFieldInsert64(EmitContext& ctx, Id base, Id insert, Id offset, Id count) {
    // Vulkan without maintenance9 requires a 32-bit Base for OpBitFieldInsert.
    // Preserve the full 64-bit operation using legal integer shifts and masks.
    // Mask both shifts so count=64 and the valid count=0/offset=64 case never
    // evaluate an undefined shift by 64, even in an unselected OpSelect operand.
    const Id shift_mask{ctx.ConstU32(63u)};
    const Id safe_count{ctx.OpBitwiseAnd(ctx.U32[1], count, shift_mask)};
    const Id safe_offset{ctx.OpBitwiseAnd(ctx.U32[1], offset, shift_mask)};
    const Id all_bits{ctx.OpNot(ctx.U64, ctx.u64_zero_value)};
    const Id partial_mask{ctx.OpISub(
        ctx.U64, ctx.OpShiftLeftLogical(ctx.U64, ctx.u64_one_value, safe_count),
        ctx.u64_one_value)};
    const Id full_width{ctx.OpIEqual(ctx.U1[1], count, ctx.ConstU32(64u))};
    const Id low_mask{ctx.OpSelect(ctx.U64, full_width, all_bits, partial_mask)};
    const Id field_mask{ctx.OpShiftLeftLogical(ctx.U64, low_mask, safe_offset)};
    const Id preserved{ctx.OpBitwiseAnd(ctx.U64, base, ctx.OpNot(ctx.U64, field_mask))};
    const Id replacement{ctx.OpBitwiseAnd(
        ctx.U64, ctx.OpShiftLeftLogical(ctx.U64, insert, safe_offset), field_mask)};
    return ctx.OpBitwiseOr(ctx.U64, preserved, replacement);
}'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_source(data: bytes) -> bytes:
    if sha(data) != SOURCE_SHA or data.decode().count(BEFORE) != 1:
        raise RuntimeError('Emitter differs from the uploaded v45 source; nothing overwritten')
    changed = data.decode().replace(BEFORE, AFTER, 1).encode()
    if sha(changed) != FIXED_SHA:
        raise RuntimeError('Patched emitter checksum mismatch')
    return changed


def build_commands(builder, a: Path, b: Path, c: Path, ninja: str, saved_link: list[str]):
    obj = 'CMakeFiles/shadps4.dir/' + SOURCE + '.o'
    lines = [s for s in ninja.splitlines() if SOURCE in s and '-c' in shlex.split(s)]
    if len(lines) != 1:
        raise RuntimeError('Expected exactly one existing integer-emitter compile command')
    cc = builder.shell_argv(lines[0])
    if cc.count(str(a/SOURCE)) != 1 or cc.count('-o') != 1 or cc[cc.index('-o')+1] != obj:
        raise RuntimeError('Emitter compiler input/output differs from preserved Ninja')
    dest = str(c/'build'/obj)
    cc = [str(c/SOURCE) if x == str(a/SOURCE) else dest+'.d' if x == obj+'.d'
          else dest if x == obj else x for x in cc]
    if '-MF' in cc and cc[cc.index('-MF')+1] != dest+'.d':
        raise RuntimeError('Emitter dependency-file path is not isolated')
    if not isinstance(saved_link, list) or not all(isinstance(x, str) for x in saved_link):
        raise RuntimeError('Invalid v45 linker argument manifest')
    ld = list(saved_link)
    if ld.count(obj) != 1 or ld.count('-o') != 1:
        raise RuntimeError('Emitter object or v45 link output is ambiguous')
    oldout = ld[ld.index('-o')+1]
    if oldout not in (str(b/'build/shadps4'), str(b/'build/shadps4.pending')):
        raise RuntimeError('Saved linker output does not point into v45')
    # Never fall back to old relative objects for the two successful corrections.
    required = (str(b/'build/CMakeFiles/shadps4.dir'/ (LIVERPOOL+'.o')),
        str(b.parent/V40_TREE/'build/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp.o'))
    if any(ld.count(x) != 1 for x in required):
        raise RuntimeError('Link would lose the v45 fence or v40 binding correction')
    ld = [dest if x == obj else str(c/'build/shadps4.pending') if x == oldout
          else x.replace(str(b/'build/link.d'), str(c/'build/link.d')) for x in ld]
    if obj in ld or oldout in ld:
        raise RuntimeError('Old output remains in v46 linker')
    return cc, ld


def capture(module, project: Path, script: Path) -> int:
    proof = module.verify_build(project)
    generated = module.make_helpers(project, proof['binary_sha256'])
    for name, text in generated.items():
        if module.read(project/'scripts'/name, 100_000) != text.encode():
            raise RuntimeError('Versioned capture helper changed: '+name)
    v44 = module.module_from(project/'scripts/got-v44-dependency-capture.py',
                             module.CAPTURE_SHA, 'got46_dependency_capture')
    v43 = v44.load_previous(script)
    runtime = v43.load_previous(script)
    original41 = module.module_from(project/'scripts/got-v41-stall-capture.py',
        '7e20c82a2970254890224b73de1ccd852fbded7060cd95f9be27f0e9b47207f4', 'got46_v41_verifier')
    def verify_stack(p):
        builder, _ = original41.verify_stack(p)
        module.verify_build(p)
        for name, text in generated.items():
            if module.read(p/'scripts'/name, 100_000) != text.encode():
                raise RuntimeError('Capture helper changed: '+name)
        return builder, p/'scripts/got46-radv-nuw-capture.py'
    runtime.verify_stack = verify_stack
    runtime.BINARY_SHA = proof['binary_sha256']; runtime.TREE = TREE; runtime.LABEL = LABEL
    previous_install = runtime.install
    def install(retained, p):
        retained = previous_install(retained, p)
        old_stack = retained.load_stack
        def load_stack(where):
            dual, base = old_stack(where)
            old_transform, old_gather = base.transform, base.gather
            def transform(source, helper, session):
                return old_transform(source, helper, session).replace(
                    'got-v44-portable-ready-', 'got-v46-portable-ready-')
            def gather(session, report):
                rc = old_gather(session, report)
                base.store(report/'info/bitfield64-build.json', module.verify_build(where))
                for tree, name in ((V40_TREE, 'binding-layout-build.json'),
                                   (V45_TREE, 'release-mem-build.json')):
                    shutil.copyfile(where/'tests'/tree/'build/proof.json', report/'info'/name)
                base.store(report/'info/bitfield64-preservation.json', {
                    'v40_unchanged': module.file_sha(where/'tests'/V40_TREE/'build/shadps4') == V40_SHA,
                    'v45_unchanged': module.file_sha(where/'tests'/V45_TREE/'build/shadps4') == V45_SHA,
                    'v45_fence_fix_preserved': module.file_sha(where/'tests'/TREE/LIVERPOOL) == LIVERPOOL_SHA,
                    'lowering_source_sha256': module.file_sha(where/'tests'/TREE/SOURCE),
                    'emulator_shader_skipped': False, 'new_driver_patch': False,
                    'gameplay_verified': False})
                dep = report/'info/dependency-capture.json'
                if base.regular(dep):
                    data = json.loads(dep.read_text()); data['emulator_modified'] = True
                    data['candidate_change'] = '64-bit bitfield lowering; v45 notification correction retained'
                    base.store(dep, data)
                return rc
            base.transform, base.gather = transform, gather
            return dual, base
        retained.load_stack = load_stack
        return retained
    runtime.install = install
    return runtime.main()


def load_parent(script: Path):
    path = script.with_name('got-v45-release-mem-fix.py')
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 100_000:
        raise RuntimeError('Preserved v45 helper missing; nothing rebuilt')
    raw = path.read_bytes()
    if sha(raw) != PARENT_SHA:
        raise RuntimeError('v45 helper checksum differs from the successful capture')
    baseline = types.ModuleType('got46_original_v45'); baseline.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), baseline.__dict__)
    # Keep build mechanics identical; only versioned output names/proof kind change.
    code = raw.decode().replace('got45', 'got46').replace('V45_', 'V46_')
    code = code.replace('RELEASE_MEM_DATA_CONFIRM_IRQ_CANDIDATE', KIND)
    code = code.replace('ONE_LIVERPOOL_TU', 'ONE_INTEGER_EMITTER_TU')
    code = code.replace('V40_AND_DRIVER=PRESERVED', 'V45_V40_AND_DRIVER=PRESERVED')
    module = types.ModuleType('got46_reused_builder'); module.__file__ = str(script)
    exec(compile(code, str(path), 'exec'), module.__dict__)
    module.TREE = TREE; module.OLD_TREE = V45_TREE; module.LABEL = LABEL
    module.SOURCE = SOURCE; module.SOURCE_SHA = SOURCE_SHA; module.V40_SHA = V45_SHA
    module.patch_source = patch_source; module.build_commands = build_commands
    old_verify = module.verify_build
    def verify_build(project):
        baseline.verify_build(project)
        proof = old_verify(project)
        if proof.get('kind') != KIND or proof.get('source_sha256') != FIXED_SHA:
            raise RuntimeError('v46 build proof differs from the targeted lowering')
        if module.file_sha(project/'tests'/TREE/LIVERPOOL) != LIVERPOOL_SHA:
            raise RuntimeError('v46 source lost the v45 fence correction')
        return proof
    module.verify_build = verify_build
    old_build = module.build
    def build(project, out):
        baseline.verify_build(project)
        if module.file_sha(project/'tests'/V45_TREE/'build/shadps4') != V45_SHA:
            raise RuntimeError('v45 binary differs from uploaded evidence')
        if module.file_sha(project/'tests'/V45_TREE/LIVERPOOL) != LIVERPOOL_SHA:
            raise RuntimeError('v45 fence source changed')
        proof = old_build(project, out)
        verify_build(project)
        return proof
    module.build = build
    module.capture = lambda project, target: capture(module, project, target)
    return module


def main() -> int:
    script = Path(__file__).resolve()
    if script.parent.name != 'scripts' or script.parent.parent.name != 'GhostOfTsushima':
        raise RuntimeError('Place helper under ~/projects/shadps4/GhostOfTsushima/scripts')
    return load_parent(script).main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BlockingIOError:
        print('V46_BUILD_ALREADY_ACTIVE'); sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc), file=sys.stderr); sys.exit(1)
