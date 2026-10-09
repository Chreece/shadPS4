#!/usr/bin/env python3
"""One guarded GoW focused SRT IR capture, based on a pinned, tested trial.

The original guarded SRT/GDS/F64/alias/graphics diagnostic is downloaded by
immutable commit and verified before source transformation. This wrapper does
not install shadPS4, replace ES-DE, change saves or terminate the SSH session.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

BASE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
            'da969fe9eaab8eb36d3d326e56683bd21a0da6f0/scripts/'
            'diagnose-gow-graphics-targets-20261010.py')
BASE_BLOB = '46b5a5a214ecaceb396b9f14a1f706798a12c678'
TARGET_SHADER = '0x57b077ac'


def replace_once(source: str, before: str, after: str, name: str) -> str:
    count = source.count(before)
    if count != 1:
        raise RuntimeError(f'SAFE_STOP: {name} source anchor count={count}, expected exactly 1')
    return source.replace(before, after, 1)


def focused_program_patch(program_source: str) -> str:
    """Permit four pre-existing IR dumps only for the high-frequency SRT shader."""
    header = '#include <map>\n#include <string>'
    program_source = replace_once(program_source, header,
        '#include <cstdlib>\n#include <filesystem>\n' + header, 'program includes')
    old = '''    if (!EmulatorSettings.IsDumpShaders()) {
        return;
    }

    const auto dump_dir = GetUserPath(PathType::ShaderDir) / "dumps";'''
    new = '''    const char* gow_ir_dir = std::getenv("SHADPS4_GOW_SRT_IR_DIR");
    const bool gow_capture = gow_ir_dir != nullptr &&
                             info.pgm_hash == 0x57b077acULL &&
                             info.hw_stage == HwStage::Compute;
    if (!EmulatorSettings.IsDumpShaders() && !gow_capture) {
        return;
    }

    const auto dump_dir = gow_capture ? std::filesystem::path(gow_ir_dir) :
                                        GetUserPath(PathType::ShaderDir) / "dumps";'''
    return replace_once(program_source, old, new, 'isolated IR dump gate')


def transform(source: str) -> str:
    source = replace_once(source,
        "f'shadps4-gow-graphics-targets-{STAMP}.tar.gz'",
        "f'shadps4-gow-srt-focus-{STAMP}.tar.gz'", 'report name')
    source = replace_once(source,
        "f'Applications/shadps4-gow-graphics-targets-trial-{STAMP}'",
        "f'Applications/shadps4-gow-srt-focus-trial-{STAMP}'", 'trial path')
    source = replace_once(source, "prefix='.gow-graphics-targets-'",
        "prefix='.gow-srt-focus-'", 'working directory')
    source = replace_once(source,
        "PRESENTER = 'src/video_core/renderer_vulkan/vk_presenter.cpp'",
        "PRESENTER = 'src/video_core/renderer_vulkan/vk_presenter.cpp'\n"
        "PROGRAM = 'src/shader_recompiler/ir/program.cpp'", 'IR program source')
    source = replace_once(source, 'def read_proven_patch():',
        '''def summarize_focused_srt_ir():
    directory = WORK / 'focused-srt-ir'
    result = []
    for path in sorted(directory.glob('*')):
        if not path.is_file():
            continue
        count_phi = count_read = count_loop = 0
        if path.name.endswith('irprogram.txt'):
            with path.open(errors='replace') as reader:
                for line in reader:
                    count_phi += 'Phi ' in line or 'Phi(' in line
                    count_read += 'ReadConst' in line
                    count_loop += 'Loop' in line
        result.append({'name': path.name, 'bytes': path.stat().st_size,
                       'phi_lines': count_phi, 'readconst_lines': count_read,
                       'loop_lines': count_loop})
    (WORK / 'focused-srt-index.json').write_text(json.dumps(result, indent=2))
    note('FOCUSED_SRT_IR_FILES=' + str(len(result)))


def make_focused_program(source: str) -> str:
    # Never affect unrelated guest shader hashes or normal compiler behavior.
    header = '#include <map>\\n#include <string>'
    source = patch_once(source, header,
        '#include <cstdlib>\\n#include <filesystem>\\n' + header,
        'targeted IR dump includes')
    old = ''' + repr('''    if (!EmulatorSettings.IsDumpShaders()) {
        return;
    }

    const auto dump_dir = GetUserPath(PathType::ShaderDir) / "dumps";''') + '''
    new = ''' + repr('''    const char* gow_ir_dir = std::getenv("SHADPS4_GOW_SRT_IR_DIR");
    const bool gow_capture = gow_ir_dir != nullptr &&
                             info.pgm_hash == 0x57b077acULL &&
                             info.hw_stage == HwStage::Compute;
    if (!EmulatorSettings.IsDumpShaders() && !gow_capture) {
        return;
    }

    const auto dump_dir = gow_capture ? std::filesystem::path(gow_ir_dir) :
                                        GetUserPath(PathType::ShaderDir) / "dumps";''') + '''
    return patch_once(source, old, new, 'targeted IR dump gate')


def read_proven_patch():''', 'focused IR helper')
    source = replace_once(source,
        '    for rel in (SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER):',
        '    for rel in (SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, PROGRAM):',
        'extra source preflight')
    source = replace_once(source,
        '    new_presenter=make_present_trace((SRC/PRESENTER).read_text())',
        '    new_presenter=make_present_trace((SRC/PRESENTER).read_text())\n'
        '    new_program=make_focused_program((SRC/PROGRAM).read_text())',
        'prevalidate targeted dump')
    source = replace_once(source,
        '    for rel in (*orig.keys(), SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER):',
        '    for rel in (*orig.keys(), SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, PROGRAM):',
        'IR source backup')
    source = replace_once(source,
        '        for rel in (*orig.keys(),SPIRV,INFO,BUFFER_CACHE,TEXTURE_CACHE,DRIVER,PRESENTER):',
        '        for rel in (*orig.keys(),SPIRV,INFO,BUFFER_CACHE,TEXTURE_CACHE,DRIVER,PRESENTER,PROGRAM):',
        'IR archive backup')
    source = replace_once(source,
        '    (SRC/PRESENTER).write_text(new_presenter)',
        '    (SRC/PRESENTER).write_text(new_presenter)\n'
        '    (SRC/PROGRAM).write_text(new_program)',
        'write traced IR source')
    source = replace_once(source,
        "    env['SHADPS4_GOW_DRAW_TRACE'] = '1'",
        "    env['SHADPS4_GOW_DRAW_TRACE'] = '1'\n"
        "    (WORK/'focused-srt-ir').mkdir(exist_ok=True)\n"
        "    env['SHADPS4_GOW_SRT_IR_DIR'] = str(WORK/'focused-srt-ir')",
        'targeted IR environment')
    source = replace_once(source,
        '    result=run_game(env)',
        '    result=run_game(env)\n'
        '    summarize_focused_srt_ir()',
        'IR report generation')
    source = replace_once(source,
        "    note('SELF_TEST=PASS: prior SRT/GDS/F64 + video + graphics target instrumentation')",
        "    if SRC.is_dir() and (SRC/PROGRAM).is_file():\n"
        "        tested=make_focused_program((SRC/PROGRAM).read_text())\n"
        "        assert '0x57b077acULL' in tested\n"
        "        assert 'SHADPS4_GOW_SRT_IR_DIR' in tested\n"
        "    note('SELF_TEST=PASS: graphics targets + targeted 0x57b077ac IR gate')",
        'IR selftest')
    return source


def git_blob(contents: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='gow-srt-focus-wrapper-') as d:
        base = Path(d) / 'pinned-graphics-targets.py'
        trial = Path(d) / 'focused-srt.py'
        download = subprocess.run(['curl', '-fLsS', '--retry', '3', BASE_URL,
                                   '-o', str(base)], check=False)
        if download.returncode:
            raise RuntimeError('SAFE_STOP: cannot fetch pinned diagnostic')
        raw = base.read_bytes()
        if git_blob(raw) != BASE_BLOB:
            raise RuntimeError('SAFE_STOP: pinned diagnostic Git blob checksum mismatch')
        result = transform(raw.decode('utf-8'))
        compile(result, str(trial), 'exec')
        trial.write_text(result)
        for marker in ('0x57b077acULL', 'SHADPS4_GOW_SRT_IR_DIR',
                       'summarize_focused_srt_ir()', 'PROGRAM).write_text(new_program)'):
            if marker not in result:
                raise RuntimeError('SAFE_STOP: missing source marker ' + marker)
        if '--self-test' in sys.argv:
            print('TRANSFORM_AND_SYNTAX_TEST=PASS', flush=True)
            return subprocess.run([sys.executable, str(trial), '--self-test'], check=False).returncode
        print('TARGET_COMPUTE_SHADER=' + TARGET_SHADER, flush=True)
        print('OTHER_GUEST_SHADERS_UNMODIFIED=YES', flush=True)
        return subprocess.run([sys.executable, str(trial)], check=False).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('SAFE_STOP_WRAPPER=' + str(error), file=sys.stderr)
        sys.exit(1)
