#!/usr/bin/env python3
"""Ghost v13: apply pinned, updated shadPS4 PR #5342 to the verified v12 trial.

The existing v12 build/playtest/rollback code is reused unchanged except for
the two source edits, an FP64 capability log, and results metadata.
"""
import hashlib
import re
import textwrap
from pathlib import Path

PINNED_PR_HEAD = '20cf4834c307779bb33af8be4329288e2c636170'
PINNED_V12_BLOB = 'ade2cb5237530c7a0d80a566f82203f793dcc3da'
PINNED_PATCH_BLOB = '51e57674724e58e16efccff69906d5a00c4e04da'
FP64_PR5342_NAMES = (
    'PackDouble2x32', 'UnpackDouble2x32',
    'FPAbs64', 'FPAdd64', 'FPFma64', 'FPMax64', 'FPMin64',
    'FPMul64', 'FPDiv64', 'FPNeg64', 'FPRecip64', 'FPRecipSqrt64',
    'FPSaturate64', 'FPClamp64', 'FPRoundEven64', 'FPFloor64',
    'FPCeil64', 'FPTrunc64', 'FPFract64', 'FPFrexpSig64', 'FPFrexpExp64',
    'FPOrdEqual64', 'FPUnordEqual64', 'FPOrdNotEqual64',
    'FPUnordNotEqual64', 'FPOrdLessThan64', 'FPUnordLessThan64',
    'FPOrdGreaterThan64', 'FPUnordGreaterThan64',
    'FPOrdLessThanEqual64', 'FPUnordLessThanEqual64',
    'FPOrdGreaterThanEqual64', 'FPUnordGreaterThanEqual64',
    'FPIsNan64', 'FPIsInf64', 'ConvertS32F64', 'ConvertF32F64',
    'ConvertF64F32', 'ConvertF64S32', 'ConvertF64U32',
)

def git_blob(raw: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()

def inject_once(code: str, before: str, after: str, title: str) -> str:
    count = code.count(before)
    if count != 1:
        raise SystemExit(f'REFUSED: expected exactly one {title} anchor, found {count}')
    return code.replace(before, after, 1)

work = Path(__file__).resolve().parent
old_file = work / 'ghost_bindless_v12_record_guard.py'
patch_file = work / 'ghost_bindless_v5_verified.patch'
for file, expected in ((old_file, PINNED_V12_BLOB), (patch_file, PINNED_PATCH_BLOB)):
    if not file.is_file() or file.is_symlink():
        raise SystemExit(f'REFUSED: pinned dependency unavailable: {file.name}')
    actual = git_blob(file.read_bytes())
    if actual != expected:
        raise SystemExit(f'REFUSED: {file.name} blob mismatch {actual}')

if len(FP64_PR5342_NAMES) != 40 or len(set(FP64_PR5342_NAMES)) != 40:
    raise SystemExit('REFUSED: pinned upstream PR opcode manifest is incomplete')

body = r'''
fp64_rel = 'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp'
serial_rel = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
opcode_path = SRC / 'src/shader_recompiler/ir/opcodes.inc'
declared_fp64 = set()
for line in opcode_path.read_text().splitlines():
    match = re.match(r'^\s*OPCODE\(\s*(\w+)\s*,', line)
    if match and re.search(r'\bF64(?:x[234])?\b', line):
        declared_fp64.add(match.group(1))
if declared_fp64 != set(FP64_PR5342_NAMES):
    raise RuntimeError('PR5342 compiler opcode signatures no longer match pinned 40-opcode manifest')

inputs = {}
for relative in (fp64_rel, serial_rel):
    path = SRC / relative
    if relative in BACKUP or not path.is_file() or path.is_symlink():
        raise RuntimeError('PR5342 source path already modified or missing: ' + relative)
    original = path.read_bytes()
    git_head = subprocess.run(
        ['git', 'show', 'HEAD:' + relative], cwd=SRC, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    if git_head.returncode or git_head.stdout != original:
        raise RuntimeError('PR5342 source differs from original Git HEAD: ' + relative)
    inputs[relative] = original

information_before = inputs[fp64_rel].decode('utf-8')
# The pinned Ghost checkout has changed since upstream. Do not assume any
# particular existing FP64 case-group layout. Add the reviewed 40-opcode
# detection as a separate, diagnostic dispatch *inside the same Visit function*.
visit_anchor = 'void Visit(Info& info, const IR::Inst& inst) {\n'
if information_before.count(visit_anchor) != 1:
    raise RuntimeError('PR5342 expected one shader-info Visit function')
if information_before.count('Visit(info, inst);') != 1:
    raise RuntimeError('PR5342 shader-info visitor invocation changed')
dispatch = (
    '    // shadPS4 PR #5342 (20cf4834): FP64 capability detection.\n'
    '    // Separate switch avoids assuming the older switch case layout.\n'
    '    if (!info.uses_fp64) {\n'
    '        switch (inst.GetOpcode()) {\n'
)
dispatch += '\n'.join(
    '        case IR::Opcode::' + opcode + ':'
    for opcode in FP64_PR5342_NAMES
) + '\n'
dispatch += (
    '            info.uses_fp64 = true;\n'
    '            break;\n'
    '        default:\n'
    '            break;\n'
    '        }\n'
    '    }\n'
)
information_after = information_before.replace(
    visit_anchor, visit_anchor + dispatch, 1
)
if information_after == information_before:
    raise RuntimeError('PR5342 adaptive visitor dispatch was not staged')
# The original opcode switch must remain untouched and all new 40 cases
# must appear in their own switch. Duplicates across separate switches
# are valid C++ and may already be present in older source variants.
if dispatch.count('        case IR::Opcode::') != 40:
    raise RuntimeError('PR5342 adaptive switch has incorrect case count')

serial_before = inputs[serial_rel].decode('utf-8')
versions = re.findall(
    r'(?m)^static constexpr u32 ShaderBinaryVersion = ([0-9]+)u;$', serial_before)
if len(versions) != 1 or not (1 <= int(versions[0]) < 100):
    raise RuntimeError('PR5342 unknown local shader binary version: ' + str(versions))
old_version = int(versions[0])
new_version = old_version + 1
old_line = f'static constexpr u32 ShaderBinaryVersion = {old_version}u;'
new_line = f'static constexpr u32 ShaderBinaryVersion = {new_version}u;'
if serial_before.count(old_line) != 1:
    raise RuntimeError('PR5342 shader cache version change is ambiguous')
serial_after = serial_before.replace(old_line, new_line, 1)

# Diagnostic logging only. The v12 SPIR-V emitter has already been backed up.
emitter_relative = 'src/shader_recompiler/backend/spirv/emit_spirv.cpp'
if emitter_relative not in BACKUP:
    raise RuntimeError('PR5342 original v12 SPIR-V compiler backup missing')
emitter_path = SRC / emitter_relative
emitter_before = emitter_path.read_text()
cap_old = (
    '    if (info.uses_fp64) {\n'
    '        ctx.AddCapability(spv::Capability::Float64);\n'
    '    }'
)
cap_new = (
    '    if (info.uses_fp64) {\n'
    '        LOG_WARNING(Render_Recompiler, '
    '"GHOST_PR5342_FP64_CAPABILITY shader={:#x}", info.pgm_hash);\n'
    '        ctx.AddCapability(spv::Capability::Float64);\n'
    '    }'
)
if emitter_before.count(cap_old) != 1:
    raise RuntimeError('PR5342 original Float64 capability emitter changed')
emitter_after = emitter_before.replace(cap_old, cap_new, 1)

# All requirements have passed; only now stage new source files.
for relative, edited in ((fp64_rel, information_after), (serial_rel, serial_after)):
    path = SRC / relative
    state = path.stat()
    BACKUP[relative] = (
        inputs[relative], state.st_atime_ns, state.st_mtime_ns)
    path.write_text(edited)
    if sha_file(path) != sha_bytes(edited.encode()):
        raise RuntimeError('PR5342 byte verification failed: ' + relative)

emitter_path.write_text(emitter_after)
if sha_file(emitter_path) != sha_bytes(emitter_after.encode()):
    raise RuntimeError('PR5342 diagnostic marker byte verification failed')

for label, before, after in (
    ('info', information_before, information_after),
    ('cache-version', serial_before, serial_after),
    ('capability-log', emitter_before, emitter_after)
):
    (W / ('ghost-v13-pr5342-' + label + '.diff')).write_text(
        ''.join(__import__('difflib').unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile='v12/' + label, tofile='v13/' + label
        ))
    )
S.update({
    'v13_pr5342_upstream_commit': '20cf4834c307779bb33af8be4329288e2c636170',
    'v13_pr5342_opcode_count': len(FP64_PR5342_NAMES),
    'v13_pr5342_all_opcodes_verified': True,
    'v13_shader_binary_version_before': old_version,
    'v13_shader_binary_version_after': new_version,
    'v13_pr5342_patched_source_sha256': sha_bytes(information_after.encode()),
    'v13_pr5342_patch_provenance': 'Pinned 40-opcode FP64 detection in independent Visit switch + local version bump',
    'v13_retry': 'v13b: source-independent opcode layout',
})
print(
    f'GHOST_V13_PR5342_PREFLIGHT=PASS 40 F64 opcodes cache={old_version}->{new_version}',
    flush=True
)
'''

# Existing controller functions use one leading space of indentation.
to_insert = '\n'.join(' ' + line if line else '' for line in body.strip('\n').splitlines()) + '\n'
base = old_file.read_text()
anchor = " print('GHOST_V12_CXX_BUILD=START; waiting for build.log; may take several minutes',flush=True)"
code = inject_once(base, anchor, to_insert + " print('GHOST_V13_CXX_BUILD=START; PR5342+v12',flush=True)", 'v12 build')
code = inject_once(code,
    "OUT=H/f'ghost-bindless-v12-record-guard-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'",
    "OUT=H/f'ghost-bindless-v13b-fp64-pr5342-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'", 'output name')
code = inject_once(code,
    "S={'goal':'Ghost v12 test whether records >=53 cause GPU SQC fault: shader-scoped IR loop sentinel 53, preserve v11 index 0..52 and private RADV; restore all',",
    "S={'goal':'Ghost v13 compare v12 with the pinned 40-opcode FP64 fix from shadPS4 PR 5342; preserve v12 runtime controls and rollback',", 'trial goal')
code = inject_once(code,
    " print('GHOST_V12_RECORD_LIMIT_TEST=START; v11 shader index clamp and private RADV preserved',flush=True)",
    " print('GHOST_V13_FP64_PR5342=START; v12 shader/driver baseline retained',flush=True)", 'start')
code = inject_once(code,
    " S['v12_record_guard_compiled']=text.count('GHOST_V12_RECORD_GUARD')",
    " S['v12_record_guard_compiled']=text.count('GHOST_V12_RECORD_GUARD')\n"
    " S['v13_fp64_runtime_emissions']=text.count('GHOST_PR5342_FP64_CAPABILITY shader=')\n"
    " S['v13_fp64_target_shader_seen']=('GHOST_PR5342_FP64_CAPABILITY shader=0x14906b6a' in text)",
    'runtime evidence')
code = inject_once(code,
    " S['probe_compiled']=True",
    " S['probe_compiled']=True\n S['v13_pr5342_compiled']=True", 'build pass')
code = inject_once(code,
    " print('GHOST_V12_RECORD_GUARD_COMPILED='+str(S.get('v12_record_guard_compiled')),flush=True)",
    " print('GHOST_V12_RECORD_GUARD_COMPILED='+str(S.get('v12_record_guard_compiled')),flush=True)\n"
    " print('GHOST_V13_PR5342_OPCODES='+str(S.get('v13_pr5342_opcode_count')),flush=True)\n"
    " print('GHOST_V13_FP64_RUNTIME_EMISSIONS='+str(S.get('v13_fp64_runtime_emissions')),flush=True)\n"
    " print('GHOST_V13_FP64_TARGET_SHADER_SEEN='+str(S.get('v13_fp64_target_shader_seen')),flush=True)",
    'final evidence')

compile(code, '<Ghost-v13-pinned-controller>', 'exec')
print('GHOST_V13B_CONTROLLER_SYNTAX=PASS', flush=True)
print('GHOST_V13_UPSTREAM_PR_HEAD=' + PINNED_PR_HEAD, flush=True)
namespace = {
    '__name__': '__main__',
    '__file__': str(old_file),
    'FP64_PR5342_NAMES': FP64_PR5342_NAMES,
}
exec(compile(code, '<Ghost-v13-pinned-controller>', 'exec'), namespace)
