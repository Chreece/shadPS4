#!/usr/bin/env python3
"""GoT: neutralize the remaining unsafe RADV NUW metadata store in a private copy.

Extends the exact f7ef13b retry-safe capture helper. Preserves the prior v10/v14
patches, v37 executable, source, system Mesa, and existing captures. Adds one
four-byte NOP at 0x9ca54; no additional optimizer pass or shader is skipped.
A bounded GDB witness checks the producer type at that now-neutralized store.
This is an evidence-backed candidate, not a declaration of gameplay success.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import types

BASE_SHA = 'a1b08afea8ecb2f30341e82153b139ebc6d4959aa18405bfa8369b9537a8d885'
OLD_PRIVATE_SHA = '5b69473448ecb10d0fd1d4451cd344b9e3d677a7ab14c47045a8f26c0e4426cf'
SITE = 0x9CA54
ORIGINAL = bytes.fromhex('80 48 24 04')
LABEL = 'got-radv-nuw-complete-visual'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('Pinned source anchor changed: ' + repr(old[:80]))
    return text.replace(old, new, 1)


def candidate_bytes(baseline: bytes) -> bytes:
    if sha(baseline) != OLD_PRIVATE_SHA:
        raise RuntimeError('Retained driver hash differs; no unknown driver patched')
    if baseline[SITE:SITE+4] != ORIGINAL:
        raise RuntimeError('Remaining NUW instruction is not the expected four bytes')
    for off, size in ((0x9CA84, 4), (0x12A72C, 5)):
        if baseline[off:off+size] != b'\x90' * size:
            raise RuntimeError('An earlier driver patch is missing: ' + hex(off))
    result = baseline[:SITE] + b'\x90' * 4 + baseline[SITE+4:]
    # Reverse only this change to independently establish preservation of v17.
    if sha(result[:SITE] + ORIGINAL + result[SITE+4:]) != OLD_PRIVATE_SHA:
        raise RuntimeError('Candidate has changes outside the one intended instruction')
    return result


# This runs in GDB, not in a privileged helper. It only reads inferior memory.
# A non-ALU witness can establish the original store's bad target without actually
# performing that write. Limit breakpoint visits to avoid unbounded debugger cost.
WITNESS = r'''
class NuwProducerWitness(gdb.Breakpoint):
    visits = 0
    def stop(self):
        self.visits += 1
        result = {'site': '0x9ca54', 'visits': self.visits,
                  'original_store_disabled': True, 'inferior_memory_written': False,
                  'gameplay_verified': False}
        try:
            inf = gdb.selected_inferior()
            ptr = int(gdb.parse_and_eval('$rax'))
            instr_type = int.from_bytes(bytes(inf.read_memory(ptr+0x18, 1)), 'little')
            result.update(producer=hex(ptr), instruction_type=instr_type)
            if instr_type != 0:
                result['result'] = 'NON_ALU_AT_PREVIOUS_UNCHECKED_ALU_STORE'
                if instr_type in (5, 7):  # nir_load_const_instr or nir_undef_instr
                    parent = int.from_bytes(bytes(inf.read_memory(ptr+0x20, 8)), 'little')
                    result.update(parent_before=hex(parent),
                                  parent_if_old_store_ran=hex(parent | (1 << 34)),
                                  self_parent=parent == ptr,
                                  parent_would_change=not bool(parent & (1 << 34)))
                write('nuw-producer-witness.json', result)
                print('GOT_NUW_NON_ALU_WITNESS=CAPTURED', flush=True)
                self.enabled = False
            elif self.visits >= 256:
                result['result'] = 'NO_NON_ALU_OBSERVED_WITHIN_256_VISITS_INCONCLUSIVE'
                write('nuw-producer-witness.json', result)
                self.enabled = False
        except Exception as exc:
            result.update(result='WITNESS_UNAVAILABLE_INCONCLUSIVE', error=str(exc))
            write('nuw-producer-witness.json', result)
            self.enabled = False
        return False
'''


def load_previous(script: Path):
    path = script.with_name('got-radv-retained-capture.py')
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 100_000:
        raise RuntimeError('The previous retry-safe retained helper is missing')
    data = path.read_bytes()
    if sha(data) != BASE_SHA:
        raise RuntimeError('Expected the previously used f7ef13b retained helper; preserving it')
    source = data.decode('utf-8')
    source = replace_once(source,
        'PRIVATE_RADV=EXACT_RETAINED_V17_HASH; SYSTEM_RADV=UNTOUCHED',
        'PRIVATE_RADV=V17_PLUS_REMAINING_NUW_CORRECTION; SYSTEM_RADV=UNTOUCHED')
    # Correct the previous literal-backslash delimiter while keeping all process
    # ownership/permission/zombie checks; never signal an existing emulator.
    source = replace_once(source, r"raw.split(b'\\0')", r"raw.split(b'\0')")
    module = types.ModuleType('got_nuw_pinned_previous')
    # Its worker dispatch must return to this extension, not the old driver run.
    module.__file__ = str(script)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


def install(module, project: Path):
    legacy = project/'tools/radv-retained-25.0.7/libvulkan_radeon.so'
    for parent in (project, project/'tools', legacy.parent):
        if not parent.is_dir() or parent.is_symlink():
            raise RuntimeError('Missing/unsafe preserved directory: ' + str(parent))
    original = module.read_regular(legacy)
    # Verify the old checksum AND its exact reverse reconstruction to system Mesa.
    module.validate_retained(original)
    changed = candidate_bytes(original)
    new_sha = sha(changed)
    module.LABEL = LABEL
    module.RETAINED_SHA = new_sha
    module.SITES = ((SITE, ORIGINAL),) + module.SITES

    def private_paths(p: Path):
        root = p/'tools/radv-nuw-complete-25.0.7'
        return root/'libvulkan_radeon.so', root/'radeon_icd.json'
    module.paths = private_paths

    def prepare(p: Path) -> dict:
        if os.geteuid() == 0:
            raise RuntimeError('Run as the normal user; only the existing UMR broker uses sudo')
        module.validate_original(module.read_regular(module.SYSTEM_DRIVER))
        if sha(module.read_regular(legacy)) != OLD_PRIVATE_SHA:
            raise RuntimeError('Retained comparison driver changed')
        lib, icd = private_paths(p)
        if lib.parent.is_symlink():
            raise RuntimeError('Candidate directory is symlinked')
        lib.parent.mkdir(mode=0o700, exist_ok=True)
        module.write_or_verify(lib, changed, 0o700)
        module.validate_retained(module.read_regular(lib))
        config = module.load_icd_template()
        if not isinstance(config.get('ICD'), dict) or not isinstance(config['ICD'].get('api_version'), str):
            raise RuntimeError('Unexpected installed ICD template')
        config['ICD']['library_path'] = str(lib)
        module.write_or_verify(icd, (json.dumps(config, indent=2)+'\n').encode(), 0o600)
        module.verify_files(p)
        info = {'result': 'NUW_CANDIDATE_PREPARED_NOT_YET_TESTED',
                'private_library': str(lib), 'private_sha256': new_sha,
                'baseline_sha256': OLD_PRIVATE_SHA,
                'change': {'offset': hex(SITE), 'before': ORIGINAL.hex(), 'after': '90909090'},
                'new_bytes_changed': 4, 'prior_patches_preserved': ['0x9ca84', '0x12a72c'],
                'additional_passes_skipped': False, 'emulator_rebuilt': False,
                'system_driver_modified': False, 'gameplay_fixed': False}
        (lib.parent/'preparation.json').write_text(json.dumps(info, indent=2)+'\n')
        return info
    module.prepare_driver = prepare

    gate = replace_once(module.GDB_GATE,
        '((0x9ca84, 4), (0x12a72c, 5))',
        '((0x9ca54, 4), (0x9ca84, 4), (0x12a72c, 5))')
    gate = replace_once(gate, 'GOT_RETAINED_RADV_LOADED=VERIFIED',
                        'GOT_NUW_COMPLETE_DRIVER_LOADED=VERIFIED')
    gate = replace_once(gate, '            self.enabled = False\n            return False',
        "            write('nuw-producer-witness.json', {'result': 'ARMED_NOT_YET_OBSERVED'})\n"
        "            self.witness = NuwProducerWitness('*' + hex(bias+0x9ca54), internal=True)\n"
        '            self.enabled = False\n            return False')
    module.GDB_GATE = WITNESS + '\n' + gate

    previous_stack = module.load_stack
    def load_stack(p):
        dual, base = previous_stack(p)
        old_transform, old_gather = base.transform, base.gather
        def transform(source, helper, session):
            text = old_transform(source, helper, session)
            return text.replace('got-radv-retained-portable-ready-',
                                'got-radv-nuw-complete-portable-ready-')
        def gather(session, report):
            rc = old_gather(session, report)
            proof = session/'native/nuw-producer-witness.json'
            witness = json.loads(proof.read_text()) if base.regular(proof) else {'result': 'NOT_OBSERVED'}
            base.store(report/'info/nuw-complete.json', {
                'candidate_sha256': new_sha, 'baseline_sha256': OLD_PRIVATE_SHA,
                'baseline_unchanged': sha(module.read_regular(legacy)) == OLD_PRIVATE_SHA,
                'changed_instruction': hex(SITE), 'witness': witness,
                'extra_optimizer_pass_disabled': False, 'gameplay_verified': False})
            return rc
        base.transform, base.gather = transform, gather
        return dual, base
    module.load_stack = load_stack
    return module


def main() -> int:
    os.umask(0o077)
    script = Path(__file__).resolve()
    if script.parent.name != 'scripts' or script.parent.parent.name != 'GhostOfTsushima':
        raise RuntimeError('Place this helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    module = install(load_previous(script), script.parent.parent)
    return module.main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
