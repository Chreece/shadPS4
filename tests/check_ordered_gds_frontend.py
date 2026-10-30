#!/usr/bin/env python3
"""Extract the REAL ordered GDS methods and type-check their IR forwarding."""
from pathlib import Path
import argparse, subprocess, tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name('gcn_ordered_gds_frontend_fixture.cpp.in')

def method(text: str, marker: str) -> str:
    if text.count(marker) != 1:
        raise RuntimeError(f'Expected exactly one method {marker}')
    at=text.index(marker)
    start=text.index('{',at)
    depth=0
    for pos in range(start,len(text)):
        depth+=(text[pos]=='{')-(text[pos]=='}')
        if not depth:
            return text[at:pos+1]
    raise RuntimeError('Unterminated method')

def source_contracts() -> None:
    def read(path):
        return (ROOT / path).read_text()

    checks = {
        'src/shader_recompiler/ir/opcodes.inc':
            ('OPCODE(OrderedGdsCount,', 1),
        'src/shader_recompiler/ir/ir_emitter.h':
            ('[[nodiscard]] U32 OrderedGdsCount(', 1),
        'src/shader_recompiler/ir/ir_emitter.cpp':
            ('U32 IREmitter::OrderedGdsCount(', 1),
        'src/shader_recompiler/frontend/translate/translate.h':
            ('void DS_ORDERED_COUNT(', 1),
        'src/shader_recompiler/frontend/translate/data_share.cpp':
            ('void Translator::DS_ORDERED_COUNT(', 1),
        'src/shader_recompiler/info.h':
            ('bool uses_ordered_gds{};', 1),
        'src/shader_recompiler/ir/microinstruction.cpp':
            ('case Opcode::OrderedGdsCount:', 1),
        'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h':
            ('Id EmitOrderedGdsCount(', 1),
        'src/shader_recompiler/backend/spirv/emit_spirv.cpp':
            ('Id EmitOrderedGdsCount(', 1),
        'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp':
            ('Id EmitOrderedGdsCount(', 0),
    }
    for path, (token, required) in checks.items():
        count = read(path).count(token)
        if count != required:
            raise RuntimeError(f'DUPLICATE_OR_MISSING_GDS_SYMBOL: {path}: {token}: {count}')
    translator = read('src/shader_recompiler/frontend/translate/data_share.cpp')
    if translator.count('case Opcode::DS_ORDERED_COUNT:') != 1:
        raise RuntimeError('DUPLICATE_OR_MISSING_DS_ORDERED_CASE')
    inst = read('src/shader_recompiler/ir/microinstruction.cpp')
    if 'case Opcode::OrderedGdsCount:' not in inst.split('bool Inst::MayHaveSideEffects()',1)[1].split('bool Inst::AreAllArgsImmediates()',1)[0]:
        raise RuntimeError('GDS_SIDE_EFFECT_CAN_BE_ELIMINATED')
    emit = read('src/shader_recompiler/backend/spirv/emit_spirv.cpp')
    if 'ASSERT_MSG(!program.info.uses_ordered_gds' not in emit:
        raise RuntimeError('UNSAFE_GPU_FALLBACK_POSSIBLE')
    body = method(translator, 'void Translator::DS_ORDERED_COUNT(')
    if any(atom in body for atom in ('SharedAtomicIAdd', 'SharedAtomicCmpSwap', 'SharedAtomicInc', 'DataAppend')):
        raise RuntimeError('UNORDERED_ATOMIC_SUBSTITUTION')
    if 'RecordOrderedGdsCheckpoint(site_pc, exec_mask)' not in body:
        raise RuntimeError('LIVE_REGISTER_CHECKPOINT_NOT_CALLED_AT_GUEST_DS')
    if 'ir.GetM0()' not in body or 'GetSrc(inst.src[0])' not in body:
        raise RuntimeError('RUNTIME_GDS_INPUT_LOST')
    print('ORDERED_GDS_SOURCE_CONTRACTS=PASS no_duplicates=1 side_effect=1 backend_gate=1')

def main():
    args=argparse.ArgumentParser()
    args.add_argument('--compiler', default='g++')
    ns=args.parse_args()
    source_contracts()
    em=(ROOT/'src/shader_recompiler/ir/ir_emitter.cpp').read_text()
    trans=(ROOT/'src/shader_recompiler/frontend/translate/data_share.cpp').read_text()
    code=FIXTURE.read_text()
    code=code.replace('/* EXACT_EMITTER */',method(em,'U32 IREmitter::OrderedGdsCount('))
    code=code.replace('/* EXACT_TRANSLATOR */',method(trans,'void Translator::DS_ORDERED_COUNT('))
    with tempfile.TemporaryDirectory(prefix='gds-ir-') as d:
        s=Path(d)/'test.cpp';out=Path(d)/'test';s.write_text(code)
        subprocess.run([ns.compiler,'-std=c++23','-Wall','-Wextra','-Werror','-pedantic',
                        '-O2',str(s),'-o',str(out)],check=True)
        subprocess.run([str(out)],check=True)

if __name__=='__main__':main()
