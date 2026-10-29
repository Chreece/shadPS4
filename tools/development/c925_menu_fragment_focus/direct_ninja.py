#!/usr/bin/env python3
"""Recompile one CMake-Ninja object and relink only, without CMake regeneration.

The caller checkpoints and restores its original source, object and executable.
Do not execute arbitrary build-tree clean/update/reconfiguration commands.
"""
from __future__ import annotations
import pathlib
import re
import shlex
import subprocess


def _commands(build: pathlib.Path, target: str) -> list[str]:
    p = subprocess.run(['ninja', '-C', str(build), '-t', 'commands', target],
                       capture_output=True, text=True, timeout=30, check=False)
    if p.returncode:
        raise RuntimeError('NINJA_COMMAND_QUERY_FAILED: ' + p.stderr[-1200:])
    return p.stdout.splitlines()


def _unwrap(command: str, build: pathlib.Path) -> tuple[list[str], pathlib.Path]:
    tokens = shlex.split(command)
    if tokens[:2] == [':', '&&'] and tokens[-2:] == ['&&', ':']:
        tokens = tokens[2:-2]
    cwd = build
    if len(tokens) >= 4 and tokens[0] == 'cd' and tokens[2] == '&&':
        cwd = pathlib.Path(tokens[1]).resolve()
        tokens = tokens[3:]
    if not tokens or any(x in tokens for x in ('&&', '||', ';', '|')):
        raise RuntimeError('UNSUPPORTED_NINJA_SHELL_COMMAND')
    if tokens[0] in (':', 'rm', 'cp', 'mv', 'sudo'):
        raise RuntimeError('UNEXPECTED_COMPILER_OR_LINKER')
    return tokens, cwd


def _matches_output(tokens: list[str], cwd: pathlib.Path, dest: pathlib.Path) -> bool:
    return tokens.count('-o') == 1 and tokens.index('-o') + 1 < len(tokens) and (
        cwd / tokens[tokens.index('-o')+1]).resolve() == dest.resolve()


def parse(build: pathlib.Path, source: pathlib.Path, obj: pathlib.Path,
          binary: pathlib.Path, evidence: pathlib.Path) -> tuple[tuple[list[str], pathlib.Path],
                                                                tuple[list[str], pathlib.Path]]:
    """Fail closed for response files and unknown command shapes.

    All objects except the one target already belong to a SHA-verified ELF.
    This step avoids re-running CMake, whose mtime/glob-check dirtiness caused
    the previous no-patch preflight stop.
    """
    rel_obj = obj.resolve().relative_to(build.resolve()).as_posix()
    compiles=[]
    for line in _commands(build, rel_obj):
        # ``-t commands`` emits every prerequisite command, including generated
        # header steps with complex shell syntax that are not to be executed.
        # Inspect only commands referring to our exact output object.
        if ' -o ' not in line or rel_obj not in line:
            continue
        args, cwd = _unwrap(line, build)
        if not _matches_output(args, cwd, obj) or args.count('-c') != 1:
            continue
        index=args.index('-c')
        if index+1 >= len(args) or (cwd/args[index+1]).resolve() != source.resolve():
            continue
        if any(a.startswith('@') for a in args):
            raise RuntimeError('RESPONSE_FILE_COMPILE_UNSUPPORTED')
        compiles.append((args,cwd))
    if len(compiles)!=1:
        raise RuntimeError('EXACT_OBJECT_COMPILE_COMMAND_NOT_UNIQUE_'+str(len(compiles)))
    links=[]
    for line in _commands(build, binary.resolve().relative_to(build.resolve()).as_posix()):
        # Ignore unrelated CMake codegen/custom commands in the executable DAG.
        # Only the final link may write this output; never execute those others.
        if ' -o ' not in line or 'shadps4' not in line:
            continue
        args, cwd = _unwrap(line, build)
        if not _matches_output(args,cwd,binary):
            continue
        referenced=sum((cwd/a).resolve()==obj.resolve() for a in args)
        if referenced!=1:
            raise RuntimeError('LINK_DOES_NOT_USE_EXACT_OBJECT_ONCE_'+str(referenced))
        if any(a.startswith('@') for a in args):
            raise RuntimeError('RESPONSE_FILE_LINK_UNSUPPORTED')
        links.append((args,cwd))
    if len(links)!=1:
        raise RuntimeError('EXACT_LINK_COMMAND_NOT_UNIQUE_'+str(len(links)))
    compiler,cwd=compiles[0]
    # Ninja normally eats compiler depfiles. Redirect any manual output into
    # evidence instead, so no untracked .d side effect remains in the build.
    if '-MF' in compiler:
        i=compiler.index('-MF')
        if i+1>=len(compiler):raise RuntimeError('BAD_COMPILER_DEPFILE_OPTION')
        compiler[i+1]=str(evidence/'direct-compile.d')
    linker,linkcwd=links[0]
    for i,a in enumerate(linker):
        if a.startswith('-Wl,--dependency-file='):
            linker[i]='-Wl,--dependency-file='+str(evidence/'direct-link.d')
    return (compiler,cwd),(linker,linkcwd)


def command_string(cmd:tuple[list[str],pathlib.Path]) -> str:
    args,cwd=cmd
    return 'cd ' + shlex.quote(str(cwd)) + ' && exec ' + shlex.join(args)


def run(menu,u,build: pathlib.Path, source: pathlib.Path, obj: pathlib.Path,
        binary: pathlib.Path, evidence: pathlib.Path):
    compile_cmd,link_cmd=parse(build,source,obj,binary,evidence)
    (evidence/'direct-command-plan.txt').write_text(
        'COMPILE:\n'+command_string(compile_cmd)+'\nLINK:\n'+command_string(link_cmd)+'\n')
    menu.logged(u,['bash','-c',command_string(compile_cmd)],evidence/'compile.log',
                'COMPILE_ONE_RENDERER_OBJECT',timeout=900)
    if not obj.is_file() or obj.stat().st_size==0:
        raise RuntimeError('NO_COMPILED_RENDERER_OBJECT')
    menu.logged(u,['bash','-c',command_string(link_cmd)],evidence/'link.log',
                'RELINK_ONE_DIAGNOSTIC_EXECUTABLE',timeout=900)
    if not binary.is_file() or binary.stat().st_size==0:
        raise RuntimeError('NO_LINKED_DIAGNOSTIC_EXECUTABLE')
