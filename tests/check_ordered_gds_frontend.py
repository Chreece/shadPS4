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

def main():
    args=argparse.ArgumentParser()
    args.add_argument('--compiler', default='g++')
    ns=args.parse_args()
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
