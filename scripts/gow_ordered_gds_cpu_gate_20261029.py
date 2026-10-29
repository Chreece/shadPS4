#!/usr/bin/env python3
"""Run immutable PR117 C++23 CPU tests with checksummed source, no GPU or repo edit.

Runs the *real* checked-out test suite in an isolated scratch source tree.
Keeps remote user's shadPS4 checkout, SSH, Moonlight, GPU, user profile, game and saves untouched.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time

COMMIT = "574fd7fe0f69bcd8d416fae5bc51a4c7cfdea476"
PINS = [
  [
    "src/shader_recompiler/frontend/ordered_gds.h",
    "1e4898dec6548f840cf80a142e5945c8599106d6"
  ],
  [
    "src/shader_recompiler/frontend/ordered_gds_continuation.h",
    "685d94aa238ab486dff2a7db0b1fb7d91773b1e7"
  ],
  [
    "src/shader_recompiler/frontend/translate/translate.h",
    "4a55046913ae26c0f3aa1a7e5c298a4c78b4283c"
  ],
  [
    "src/shader_recompiler/frontend/translate/data_share.cpp",
    "d48347cd6a0b23583ed186adf44ca2f4225d1052"
  ],
  [
    "src/shader_recompiler/info.h",
    "d94bd3b30ae9bcee249ef8b2a25c94ffabf03d9d"
  ],
  [
    "src/shader_recompiler/ir/opcodes.inc",
    "ac545a02115c75429802158e0ca2272db71994fb"
  ],
  [
    "src/shader_recompiler/ir/ir_emitter.h",
    "b1432421993d550de9481309c9264b6c6fc650fa"
  ],
  [
    "src/shader_recompiler/ir/ir_emitter.cpp",
    "d18957eb983419b7c2cec997f146ddaed728362d"
  ],
  [
    "src/shader_recompiler/ir/microinstruction.cpp",
    "bdcc1897cf840eaa78e5b773c796a8f7b5fb64f1"
  ],
  [
    "src/shader_recompiler/backend/spirv/emit_spirv_instructions.h",
    "5d82de6ccf99b03bb8dd3a8efa4c0a7b0a68b1d0"
  ],
  [
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
    "94bdca9e68a17938a4236cac6d93faa6acc249af"
  ],
  [
    "src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp",
    "2fe9e201656ddd9dbde28c538a7e7470df8f1b2e"
  ],
  [
    "tests/gcn_ordered_gds_complete_batch.cpp",
    "de07d3bd4593cc6f48acda1173ea73c1c338db8d"
  ],
  [
    "tests/gcn_ordered_gds_continuation.cpp",
    "49ecc3b388e1fa6880009c255d7dfb36f2ba0ae9"
  ],
  [
    "tests/gcn_ordered_gds_frontend_fixture.cpp.in",
    "b0d02331a94dec3c9c75186433f26d16a4db2b41"
  ],
  [
    "tests/check_ordered_gds_frontend.py",
    "811d43b436f354cbff8bd32e4510a1d0986804d9"
  ],
  [
    "tests/check_ordered_gds_ir.py",
    "11576a39aeeb757bd251e26dc076682a98700e13"
  ],
  [
    "tests/run_ordered_gds_cpu.sh",
    "f04374126cd620f837d52939c304abc527d5811c"
  ]
]
ROOT = Path.home() / 'projects/shadps4'
LOGS = ROOT / 'logs'
TESTS = ROOT / 'tests'
DIARY = ROOT / 'diary/trials.jsonl'
MAX_SOURCE_BYTES = 2_000_000

def sha1git(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def main() -> int:
    os.umask(0o077)
    LOGS.mkdir(parents=True, exist_ok=True)
    TESTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tag = f'gds-pr117-cpu-{stamp}-{os.getpid()}'
    outdir = LOGS / tag
    outdir.mkdir(mode=0o700)
    archive = LOGS / (tag + '.tar.gz')
    result = dict(schema_version=2, experiment='PR117_TYPED_GDS_TWO_SITE_CPU_ONLY',
                  source_commit=COMMIT, files=len(PINS), gpu_used=False,
                  game_launched=False, user_checkout_modified=False,
                  tests=[], status='STARTED', started_at=stamp)
    status = 1
    try:
        work = Path(tempfile.mkdtemp(prefix=tag+'-', dir=TESTS))
        outroot = work / 'checkout'
        outroot.mkdir()
        downloaded = []
        for filename, expected_blob in PINS:
            rel = Path(filename)
            if rel.is_absolute() or '..' in rel.parts:
                raise RuntimeError('UNSAFE_SOURCE_PATH')
            dest = outroot / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = ('https://raw.githubusercontent.com/Chreece/shadPS4/' + COMMIT +
                   '/' + filename)
            command = ['curl', '-4fLsS', '--retry', '3', '--connect-timeout', '15',
                       '--max-time', '90', '--output', str(dest), url]
            with (outdir / 'download.log').open('ab') as log:
                p = subprocess.run(command, stdout=log, stderr=log, timeout=120)
            if p.returncode:
                raise RuntimeError(f'FETCH_FAILED rc={p.returncode} {filename}')
            data = dest.read_bytes()
            if len(data) > MAX_SOURCE_BYTES or sha1git(data) != expected_blob:
                raise RuntimeError('SOURCE_GIT_BLOB_MISMATCH: ' + filename)
            downloaded.append(dict(path=filename, blob=expected_blob,
                                   sha256=hashlib.sha256(data).hexdigest(),
                                   bytes=len(data)))
        (outdir / 'source-pins.json').write_text(json.dumps(downloaded, indent=2)+'\n')
        result['download_sha_verification'] = 'PASS'
        for compiler in ('g++','clang++'):
            if not shutil.which(compiler):
                raise RuntimeError('MISSING_COMPILER='+compiler)
            env = os.environ.copy()
            env['CXX'] = compiler
            env['RUN_SANITIZERS'] = '1'
            env['TMPDIR'] = str(work)
            env['UBSAN_OPTIONS'] = 'halt_on_error=1'
            testlog = outdir / (compiler.replace('+','p')+'-cpu.log')
            with testlog.open('wb') as log:
                trial = subprocess.run(
                    ['bash', 'tests/run_ordered_gds_cpu.sh'],
                    cwd=outroot, env=env, stdout=log,
                    stderr=subprocess.STDOUT, timeout=300)
            test = dict(compiler=compiler, return_code=trial.returncode,
                        output=testlog.name)
            result['tests'].append(test)
            if trial.returncode:
                raise RuntimeError('CPU_REGRESSION_FAILED_'+compiler)
            if b'ORDERED_GDS_OFFLINE_REGRESSION=PASS' not in testlog.read_bytes():
                raise RuntimeError('CPU_REGRESSION_COMPLETION_MARKER_MISSING_'+compiler)
        result['status'] = 'PASS_CPU_ONLY'
        status = 0
    except Exception as exc:
        result['status'] = 'FAIL'
        result['error'] = repr(exc)
    finally:
        result['completed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (outdir / 'result.json').write_text(json.dumps(result,indent=2)+'\n')
        DIARY.parent.mkdir(parents=True, exist_ok=True)
        with DIARY.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(result, diary_source='universal',
                                         archive=str(archive)))+'\n')
        with tarfile.open(archive, 'x:gz') as tar:
            tar.add(outdir, arcname=tag)
        print('PR117_TEST_RESULT='+result['status'], flush=True)
        print('PRODUCTION_GPU_BACKEND=NOT_IMPLEMENTED',flush=True)
        print('GAME_LAUNCHED=NO SOURCE_REPOSITORIES=UNCHANGED SSH_SESSION=OPEN',flush=True)
        print('UPLOAD_THIS='+str(archive), flush=True)
    return status

if __name__=='__main__':
    raise SystemExit(main())
