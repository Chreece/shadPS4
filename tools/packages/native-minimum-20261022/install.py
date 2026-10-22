#!/usr/bin/env python3
"""Install an immutable complete package; does not edit emulator files."""
import ast
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

EXPECTED='c742e9361ae9ea409758facb3f1bb267004848f320ff45fd75beb82882cd3424'
SIZE=30580
FILES={'MANIFEST.json','README.md','masked_wave_minimum.h','masked_wave_minimum_emit.inc',
       'run_native.py','run_wave.py','runtime_adapter.py','test_native_workflow.py',
       'test_package.py','verify_emitter.cpp','verify_native_sirit.cpp','verify_wave.py','wave_fix.py'}

def install(payload: bytes, destination: Path):
    if len(payload)!=SIZE or hashlib.sha256(payload).hexdigest()!=EXPECTED:
        raise SystemExit('PACKAGE_BYTES_MISMATCH; nothing executed')
    decoder=lzma.LZMADecompressor()
    raw=decoder.decompress(payload,max_length=1024*1024)
    if not decoder.eof or decoder.unused_data:
        raise SystemExit('INVALID_COMPRESSED_PACKAGE')
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!=FILES or any(not isinstance(v,str) for v in data.values()):
        raise SystemExit('PACKAGE_FILE_SET_MISMATCH')
    manifest=json.loads(data['MANIFEST.json'])
    if not isinstance(manifest,dict) or set(manifest)!=FILES-{'MANIFEST.json'}:
        raise SystemExit('MANIFEST_FILE_SET_MISMATCH')
    for name,digest in manifest.items():
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise SystemExit('INVALID_MANIFEST_HASH='+name)
        if hashlib.sha256(data[name].encode()).hexdigest()!=digest:
            raise SystemExit('MANIFEST_CHECKSUM_MISMATCH='+name)
        if name.endswith('.py'):ast.parse(data[name],filename=name)
    # All input, CRC, hash, path and syntax checks finish before the first write.
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise SystemExit('PACKAGE_DESTINATION_OCCUPIED')
        for name,text in data.items():
            path=destination/name
            if path.is_symlink() or not path.is_file() or path.read_bytes()!=text.encode():
                raise SystemExit('EXISTING_PACKAGE_CHANGED='+name)
    else:
        temporary=Path(tempfile.mkdtemp(prefix='.native-minimum-',dir=destination.parent))
        try:
            for name,text in data.items():(temporary/name).write_bytes(text.encode())
            os.rename(temporary,destination)
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    print('COMPLETE_PACKAGE_VERIFIED=PASS files='+str(len(FILES)),flush=True)
    print('PACKAGE='+str(destination),flush=True)
    return destination

if __name__=='__main__':
    source,revision,destination=sys.argv[1:4]
    if not re.fullmatch('[0-9a-f]{40}',revision):raise SystemExit('INVALID_PACKAGE_REVISION')
    encoded=b''.join(subprocess.check_output(['git','-C',source,'show',
        revision+f':tools/packages/native-minimum-20261022/payload.{i:02d}.b64'])
        for i in range(4))
    payload=base64.b64decode(encoded,validate=True)
    install(payload,Path(destination))
