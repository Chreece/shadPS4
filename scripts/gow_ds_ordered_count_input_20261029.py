#!/usr/bin/env python3
"""Read-only recovery and bitfield analysis of the captured guest DS_ORDERED_COUNT shader.

Do NOT start shadPS4, access saves/credentials, change local source or load the GPU.
This script never interprets a plain atomic add as ordered-wave semantics.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile

ROOT = Path.home() / 'projects/shadps4'
GAME = ROOT / 'GodOfWarRagnarok/tests'
LOGS = ROOT / 'logs'
TARGET = 'dbaa6ae4'
SESSIONS = (
    'playtest-gow-vulkan-validation-game-20261029-114941-3306208',
    'playtest-gow-vulkan-validation-game-20261029-103129-3130871',
    'playtest-gow-vulkan-validation-game-20261029-094905-3015906',
)
MAX_FILES = 4096
MAX_SIZE = 16 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shader_entries(data: bytes) -> list[dict]:
    matches = []
    for p in range(0, len(data) - 7, 4):
        ins = int.from_bytes(data[p:p + 8], 'little')
        if (ins >> 26) & 0x3f != 0x36 or (ins >> 18) & 0xff != 63:
            continue
        offset0 = ins & 255
        offset1 = (ins >> 8) & 255
        matches.append({
            'byte_offset_in_input': p,
            'raw_instruction_u64_le': f'0x{ins:016x}',
            'encoding': 'GCN DS', 'opcode': 63, 'name': 'DS_ORDERED_COUNT',
            'gds_bit': (ins >> 17) & 1,
            'offset0': offset0, 'offset1': offset1,
            'ordered_counter_index': (offset0 >> 2) & 0x3f,
            'offset0_alignment_ok': (offset0 & 3) == 0,
            'wave_release': bool(offset1 & 1),
            'wave_done': bool(offset1 & 2),
            'ordered_opcode': (offset1 >> 4) & 3,
            'ordered_opcode_names': ['add', 'exchange', 'reserved', 'wrap'][(offset1 >> 4) & 3],
            'addr_vgpr': (ins >> 32) & 255,
            'data0_vgpr': (ins >> 40) & 255,
            'data1_vgpr': (ins >> 48) & 255,
            'vdst_vgpr': (ins >> 56) & 255,
            'runtime_m0_and_logical_wave_id': 'NOT_OBSERVED_IN_BINARY',
        })
    return matches


def read_shader_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_size <= 0 or s.st_size > MAX_SIZE:
            raise RuntimeError('REFUSE_NON_REGULAR_OR_OVERSIZED_SHADER')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_SIZE + 1)
        if len(data) != s.st_size:
            raise RuntimeError('SHADER_FILE_CHANGED_WHILE_READING')
        return data
    finally:
        os.close(fd)


def candidate_dirs() -> list[Path]:
    dirs = [GAME / name / 'user/shader/dumps' for name in SESSIONS]
    dirs += [ROOT / 'user/shader/dumps', Path.home() / 'user/shader/dumps',
             Path.home() / '.local/share/shadPS4/shader/dumps']
    return dirs


def collect() -> tuple[dict, bytes | None]:
    state: dict = {
        'schema_version': 1, 'game': 'CUSA34384', 'target_shader': f'cs_0x00000000{TARGET}_0.bin',
        'source_run': 'gow-swapchain-fence-20261029T114554Z-3306207',
        'only_existing_files': True, 'game_launch': False,
        'source_edit': False, 'profile_edit': False, 'gpu_use': False,
        'checked_directories': [], 'matches': [],
    }
    data: bytes | None = None
    for folder in candidate_dirs():
        result: dict = {'directory': str(folder), 'status': 'MISSING'}
        state['checked_directories'].append(result)
        if folder.is_symlink():
            result['status'] = 'SYMLINK_SKIPPED'
            continue
        if not folder.is_dir():
            continue
        result['status'] = 'NO_MATCH'
        with os.scandir(folder) as entries:
            scanned = 0
            for e in entries:
                scanned += 1
                if scanned > MAX_FILES:
                    result['status'] = 'SCAN_LIMIT_REACHED'
                    break
                if not re.fullmatch(r'cs_0x[0-9a-fA-F]*dbaa6ae4(?:_[0-9]+)?\.bin', e.name):
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                shader = read_shader_file(Path(e.path))
                item = {'file': str(e.path), 'bytes': len(shader), 'sha256': digest(shader),
                        'decoded_ordered_count_sites': shader_entries(shader)}
                state['matches'].append(item)
                if data is None:
                    data = shader
                result['status'] = 'FOUND'
        result['scanned_entries'] = min(scanned, MAX_FILES)
        if data is not None:
            break
    state['status'] = 'FOUND_INPUT' if data else 'INPUT_NOT_SAVED_IN_CHECKED_DUMP_PATHS'
    state['semantic_warning'] = (
        'DS_ORDERED_COUNT is wave-order dependent. Its GDS offset, ordered index/opcode, '
        'wave flags, M0 logicalWaveID and first active lane cannot be replaced by a plain atomic add. '
        'PR shadps4-emu/shadPS4#5511 is not a proof of equivalent ordering.')
    return state, data


def save_archive(state: dict, shader: bytes | None) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = LOGS / f'gow-ordered-count-input-{stamp}-{os.getpid()}.tar.gz'
    if out.exists():
        raise RuntimeError('OUTPUT_EXISTS_REFUSING_OVERWRITE')
    with tempfile.TemporaryDirectory(prefix='gow-ordered-', dir=LOGS) as temp:
        work = Path(temp)
        evidence = work / 'evidence'
        evidence.mkdir()
        (evidence / 'ordered-count-decoding.json').write_text(json.dumps(state, indent=2) + '\n')
        if shader is not None:
            (evidence / 'cs_0xdbaa6ae4_guest_input.bin').write_bytes(shader)
        with tarfile.open(out, 'x:gz') as archive:
            archive.add(evidence, arcname='evidence', recursive=True)
    return out


def diary(state: dict, out: Path) -> None:
    path = ROOT / 'diary/trials.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({
            'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'experiment': 'GOW_DS_ORDERED_COUNT_GUEST_INPUT_READ_ONLY',
            'status': state['status'],
            'target_shader': state['target_shader'],
            'file_count': len(state['matches']),
            'archive': str(out),
            'no_gpu_work': True, 'source_mutated': False,
        }) + '\n')


def main() -> None:
    os.umask(0o077)
    state, data = collect()
    archive = save_archive(state, data)
    diary(state, archive)
    print('DS_ORDERED_COUNT_CAPTURE=' + state['status'])
    print('SAVED_GUEST_SHADER_BIN=' + ('YES' if data is not None else 'NO'))
    if data:
        print('GUEST_SHADER_SHA256=' + digest(data))
        print('ORDERED_COUNT_INSTRUCTIONS=' + str(len(shader_entries(data))))
        for site in shader_entries(data)[:6]:
            print('DS_ORDERED_COUNT_SITE=' + json.dumps(site, separators=(',', ':')))
    print('GAME_LAUNCHED=NO; SHADER_EMULATION_NOT_CHANGED=YES')
    print('UPLOAD_THIS=' + str(archive))
    print('SSH_SESSION=OPEN')


if __name__ == '__main__':
    main()
