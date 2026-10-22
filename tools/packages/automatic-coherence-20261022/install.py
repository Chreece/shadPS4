#!/usr/bin/env python3
"""Install the complete, pinned automatic-coherence build package; never launch a game."""
from __future__ import annotations
import ast
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

PAYLOAD='tools/packages/automatic-coherence-20261022/payload.tar.xz'
PAYLOAD_SHA='b38446e621dcb7fadf9a78ec02a793fec9531500917aa4040e50fa6c281ac0a6'
PAYLOAD_SIZE=9296
TAR_SIZE=40960
REQUIRED={'run_coherence.py','patch_coherence.py','verify_coherence.py','source_fragments.json'}
GIT_ENV={k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR')}


def decode(payload):
    if len(payload)!=PAYLOAD_SIZE or hashlib.sha256(payload).hexdigest()!=PAYLOAD_SHA:
        raise RuntimeError('PACKAGE_PAYLOAD_CHECKSUM_MISMATCH')
    decoder=lzma.LZMADecompressor(memlimit=128*1024*1024)
    raw=decoder.decompress(payload,max_length=TAR_SIZE+1)
    if len(raw)!=TAR_SIZE or not decoder.eof or decoder.unused_data:
        raise RuntimeError('PACKAGE_DECOMPRESSION_MISMATCH')
    contents={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source:
        for entry in source:
            name=entry.name
            if (not entry.isfile() or name not in REQUIRED|{'MANIFEST.json'}
                    or name in contents or entry.size<0 or entry.size>TAR_SIZE):
                raise RuntimeError('UNSAFE_OR_UNEXPECTED_PACKAGE_MEMBER='+name)
            stream=source.extractfile(entry)
            if stream is None:raise RuntimeError('PACKAGE_MEMBER_NOT_READABLE='+name)
            contents[name]=stream.read(entry.size+1)
            if len(contents[name])!=entry.size:raise RuntimeError('PACKAGE_MEMBER_SIZE_CHANGED='+name)
    if set(contents)!=REQUIRED|{'MANIFEST.json'}:raise RuntimeError('INCOMPLETE_PACKAGE')
    manifest=json.loads(contents['MANIFEST.json'])
    if not isinstance(manifest,dict) or set(manifest)!=REQUIRED:raise RuntimeError('INCOMPLETE_MANIFEST')
    for name,expected in manifest.items():
        if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:
            raise RuntimeError('INVALID_MANIFEST_HASH='+name)
        if hashlib.sha256(contents[name]).hexdigest()!=expected:
            raise RuntimeError('PACKAGE_MEMBER_CHECKSUM_MISMATCH='+name)
        if name.endswith('.py'):ast.parse(contents[name],filename=name)
    json.loads(contents['source_fragments.json'])
    return contents


def install(source,revision,destination):
    if re.fullmatch('[0-9a-f]{40}',revision) is None:raise RuntimeError('INVALID_REVISION')
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-C',str(source),*args],env=GIT_ENV,timeout=30)
    if git('rev-parse',revision+'^{commit}').decode().strip()!=revision:
        raise RuntimeError('EXPECTED_COMMIT_MISSING')
    contents=decode(git('show',revision+':'+PAYLOAD))
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():raise RuntimeError('PACKAGE_DESTINATION_OCCUPIED')
        for name,data in contents.items():
            path=destination/name
            if path.is_symlink() or not path.is_file() or path.read_bytes()!=data:
                raise RuntimeError('EXISTING_PACKAGE_CHANGED='+name)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='.automatic-coherence-',dir=destination.parent))
        try:
            for name,data in contents.items():
                with (temporary/name).open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            if destination.exists() or destination.is_symlink():raise RuntimeError('PACKAGE_APPEARED_DURING_INSTALL')
            os.rename(temporary,destination)
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    print('AUTOMATIC_COHERENCE_PACKAGE=VERIFIED files='+str(len(REQUIRED)),flush=True)
    print('PRODUCTION_SOURCE_UNCHANGED=YES; GAME_LAUNCHED=NO',flush=True)
    print('PACKAGE='+str(destination),flush=True)


if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit('Usage: install.py SOURCE COMMIT DIRECTORY')
    try:install(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))
    except (OSError,ValueError,RuntimeError,lzma.LZMAError,tarfile.TarError,subprocess.CalledProcessError) as error:
        print('AUTOMATIC_COHERENCE_INSTALL_FAILED='+str(error),file=sys.stderr,flush=True)
        raise SystemExit(2)
