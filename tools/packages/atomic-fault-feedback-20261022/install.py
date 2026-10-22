#!/usr/bin/env python3
"""Install the pinned fault-feedback continuation; no emulator files are edited."""
import ast
import hashlib
import lzma
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PAYLOAD_SHA = '480869d8cd34f94099cc17c8ee9a618610582276e2c82559b866b993d289771d'
RUNNER_SHA = '25026a77a6216b70af249772efd309addf17438233c665d9dcc6fc30235c0ffa'
PAYLOAD_SIZE = 11376
RUNNER_SIZE = 34389
PAYLOAD_PATH = 'tools/packages/atomic-fault-feedback-20261022/fault-feedback.py.xz'


def install(source, revision, destination):
    if not re.fullmatch(r'[0-9a-f]{40}',revision):
        raise RuntimeError('INVALID_PACKAGE_REVISION')
    packed=subprocess.check_output(['git','-C',str(source),'show',revision+':'+PAYLOAD_PATH])
    if len(packed)!=PAYLOAD_SIZE or hashlib.sha256(packed).hexdigest()!=PAYLOAD_SHA:
        raise RuntimeError('FAULT_PACKAGE_CHECKSUM_MISMATCH')
    raw=lzma.decompress(packed,memlimit=64*1024*1024)
    if len(raw)!=RUNNER_SIZE or hashlib.sha256(raw).hexdigest()!=RUNNER_SHA:
        raise RuntimeError('FAULT_RUNNER_CHECKSUM_MISMATCH')
    ast.parse(raw,filename='fault_feedback.py')
    if not destination.parent.is_dir():
        raise RuntimeError('SCRIPTS_DIRECTORY_MISSING')
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes()!=raw:
            raise RuntimeError('EXISTING_FAULT_RUNNER_CHANGED')
    else:
        fd,name=tempfile.mkstemp(prefix='.fault-feedback-',dir=destination.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic no-replace publication: never overwrite a concurrent edit.
            os.link(temporary,destination)
        finally:
            temporary.unlink(missing_ok=True)
    print('FAULT_PACKAGE_VERIFIED=PASS')
    print('RUNNER_SHA256='+RUNNER_SHA)
    print('RUNNER='+str(destination))


if __name__=='__main__':
    try:
        if len(sys.argv)!=4:
            raise RuntimeError('EXPECTED_SOURCE_REVISION_DESTINATION')
        install(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))
    except (RuntimeError,OSError,ValueError,lzma.LZMAError,subprocess.CalledProcessError) as error:
        print('FAULT_INSTALL_ERROR='+str(error),file=sys.stderr)
        raise SystemExit(2)
