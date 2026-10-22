#!/usr/bin/env python3
"""Install a verified all-games float atomic translator continuation; never edit emulator files."""
import ast
import base64
import hashlib
import lzma
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PREFIX = 'tools/packages/typed-float-atomics-20261022'
PARTS = (0, 1, 6, 7, 8, 9, 10, 11, 4)
SHA = {
    0: ('ab68d8a654f94c16f40e072d3920215c269870a1cd5b82d906d362dc07a95564',3000),
    1: ('690a3fca81d642d3c7898aa13937f5f31ffc722d69d79a47acd36fd178e0fc28',3000),
    6: ('1b672a7ad1ac84eb5c91872708cf178de4e938c5ea530db16aea05164bcc07ee',1000),
    7: ('e1b0107b625edff87249e4134ee2b3685c30e94eec76323ef0c48d40da201bac',1000),
    8: ('f18b08f187bc90c4513ca47a8c12a9ad7a009699b36b5626207453f257e9b820',1000),
    9: ('6e12269553ec7b91d90c710b9c8fafed1036cdba4540345f2563998dfbd56e28',1000),
    10: ('112f320568fbadaa40699b67e5e0ea4e7e81482cbfd7743a1a2cdf0baa61d156',1000),
    11: ('5471986d6ec53748edbc2f250a68c89e4fa6c8925c1e707358702dbc3688b005',1000),
    4: ('a9a934e102acd2573a5630c3eb88d9166fa7cb704a3f9cee6ff0eeca57ff4146',2392),
}
EXPECTED_BASE64_SHA = 'fcfdb815a945d051180ff5ac66d3c4fe38bf2ea3d871f2e94d444a72564618a4'
EXPECTED_PAYLOAD_SHA = '97ac1a235da092d5aef5c882b8cfd6c166902a03001f33d802c33e02ae21f590'
EXPECTED_RUNNER_SHA = '1aedac41041fd34210325729506b15336df0a5df9b1f5dbd1b358493f773a197'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def install(source, revision, destination):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('INVALID_COMMIT_HASH')
    cmd = lambda *args: subprocess.check_output(['git', '-C', str(source), *args])
    if cmd('rev-parse', revision+'^{commit}').decode().strip() != revision:
        raise RuntimeError('EXPECTED_COMMIT_UNAVAILABLE')
    pieces = []
    for part in PARTS:
        payload = cmd('show', f'{revision}:{PREFIX}/part{part:02d}.b64')
        expected_sha, expected_len = SHA[part]
        if len(payload) != expected_len or sha(payload) != expected_sha:
            raise RuntimeError('CORRUPT_PART_'+str(part))
        pieces.append(payload)
    encoded = b''.join(pieces)
    if len(encoded) != 14392 or sha(encoded) != EXPECTED_BASE64_SHA:
        raise RuntimeError('ENCODED_PACKAGE_MISMATCH')
    compressed = base64.b64decode(encoded, validate=True)
    if len(compressed) != 10792 or sha(compressed) != EXPECTED_PAYLOAD_SHA:
        raise RuntimeError('PAYLOAD_MISMATCH')
    decoder = lzma.LZMADecompressor(memlimit=64*1024*1024)
    script = decoder.decompress(compressed, max_length=33163)
    if (not decoder.eof or decoder.unused_data or len(script)!=33162
            or sha(script)!=EXPECTED_RUNNER_SHA):
        raise RuntimeError('SCRIPT_MISMATCH')
    ast.parse(script, filename='atomic_minmax.py')
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes()!=script:
            raise RuntimeError('LOCAL_SCRIPT_CHANGED_OR_OCCUPIED')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix='.typed-float-atomic-',dir=destination.parent)
        temp = Path(filename)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(script)
                file.flush()
                os.fsync(file.fileno())
            os.link(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    print('ATOMIC_PACKAGE_SHA256_VERIFIED=PASS')
    print('PRODUCTION_SOURCE_UNTOUCHED=YES')
    print('RUNNER='+str(destination))


if __name__ == '__main__':
    if len(sys.argv)!=4:
        raise SystemExit('Usage: install.py SOURCE COMMIT OUTPUT')
    try:
        install(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))
    except (OSError, ValueError, RuntimeError, lzma.LZMAError, subprocess.CalledProcessError) as exc:
        print('ATOMIC_INSTALL_ERROR='+str(exc),file=sys.stderr)
        raise SystemExit(2)
