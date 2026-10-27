#!/usr/bin/env python3
"""Compare actual GoT Continue-menu pixels with NO gamepad or keyboard input.

No source checkout, compiler, emulator config changes, live-saves changes, service
restart, extra display or global process signals. Binaries and source are read
only. Two separately limited 118-second launches use the same existing isolated
portable profile and exact prior verified Ollama/VRAM guard. Menu only.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
import time
import traceback

HOME = Path.home()
PROJECT = HOME / 'projects/shadps4'
ROOT = PROJECT / 'GhostOfTsushima'
EXPERIMENT = ROOT / 'experiments/c925-integrated-gpu-20261027-122701'
BUILD = EXPERIMENT / 'build'
SOURCE = EXPERIMENT / 'source'
LOGS = PROJECT / 'logs'
GAME = Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
HELPER = PROJECT / 'scripts/got-c925-ollama-vram-headroom-20261027.py'
HELPER_BLOB = '59e2386ac713c1d388a5b97372061cbf981acc0c'
GIT_HEAD = 'c925523b49ef39434024b16b2b014d120471ee1a'
BASELINE = ROOT / 'candidates/c925-20261027-195934-performance-positive/shadps4'
BASELINE_SHA = '395682a478f04e4f4a5a2da89488e90fe54ccf502dc23cbecf716a7adeac4e8b'
CURRENT = BUILD / 'shadps4'
CURRENT_SHA = '63fe630faed40faf2a7dddde87d1084e5dade151add8f4c0b206f86d9caa69b9'
KNOWN_GOOD = ROOT / 'candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4'
KNOWN_GOOD_SHA = 'be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28'
COPY = ROOT / 'candidates/c925-20261027-202701-cb-shader-mask-preserved/shadps4'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')
OUT = LOGS / ('got-c925-menu-only-ab-' + STAMP)
ARCHIVE = OUT.with_suffix('.tar.gz')


def get_helper():
    if not HELPER.is_file() or HELPER.is_symlink():
        raise RuntimeError('VERIFIED_OLLAMA_HELPER_MISSING_OR_SYMLINK')
    blob = subprocess.check_output(['git', 'hash-object', str(HELPER)],
                                   text=True, timeout=15).strip()
    if blob != HELPER_BLOB:
        raise RuntimeError('VERIFIED_OLLAMA_HELPER_BLOB_MISMATCH')
    spec = importlib.util.spec_from_file_location('c925_verified_menu_helper', HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError('CANNOT_LOAD_PINNED_HELPER')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_digest(helper, path: Path, digest: str, label: str):
    if path.is_symlink() or not path.is_file() or helper.sha256(path) != digest:
        raise RuntimeError('BINARY_SHA_MISMATCH_' + label)


def preflight(helper):
    print('STAGE=VERIFY_EXACT_195934_AND_202701_ELFS_NO_BUILD', flush=True)
    for path in (PROJECT, ROOT, EXPERIMENT, BUILD, SOURCE, BUILD/'user'):
        if path.is_symlink():
            raise RuntimeError('SYMLINK_IN_PROTECTED_WORKSPACE_' + path.name)
    if not GAME.is_file() or not (BUILD/'user').is_dir():
        raise RuntimeError('GAME_OR_ISOLATED_PORTABLE_PROFILE_MISSING')
    if not (BUILD/'build.ninja').is_file():
        raise RuntimeError('EXISTING_NATIVE_BUILD_CONTEXT_MISSING')
    head = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'],
                                   text=True, timeout=15).strip()
    if head != GIT_HEAD:
        raise RuntimeError('SOURCE_BASE_CHANGED')
    cmake = BUILD / 'CMakeCache.txt'
    if not cmake.is_file() or 'CMAKE_HOME_DIRECTORY:INTERNAL=' + str(SOURCE) not in cmake.read_text(errors='replace'):
        raise RuntimeError('FOREIGN_CMAKE_BUILD_DIRECTORY')
    if subprocess.run(['pgrep', '-x', 'shadps4'], capture_output=True).returncode == 0:
        raise RuntimeError('ANOTHER_GAME_IS_RUNNING_REFUSE_INTERFERENCE')
    for path, digest, label in ((BASELINE, BASELINE_SHA, '195934'),
                                (CURRENT, CURRENT_SHA, '202701'),
                                (KNOWN_GOOD, KNOWN_GOOD_SHA, '154043'),
                                (helper.PROVEN, helper.MENU_ELF, 'original_menu')):
        check_digest(helper, path, digest, label)
    # Ensure the comparison cannot accidentally overwrite the current binary.
    if COPY.exists():
        check_digest(helper, COPY, CURRENT_SHA, 'saved_202701')
    else:
        if COPY.parent.is_symlink():
            raise RuntimeError('DESTINATION_PARENT_SYMLINK')
        COPY.parent.mkdir(parents=True, exist_ok=True)
        temp = COPY.with_name('.cb202701-copy-in-progress')
        if temp.exists():
            raise RuntimeError('STALE_COPY_TEMP_REFUSING_OVERWRITE')
        shutil.copy2(CURRENT, temp)
        try:
            check_digest(helper, temp, CURRENT_SHA, 'temp_copy')
            temp.replace(COPY)
            COPY.chmod(0o555)
        finally:
            if temp.exists():
                temp.unlink()
    print('PROTECTED_CB_MASK_BINARY_SHA256=' + CURRENT_SHA, flush=True)
    print('PROTECTED_195934_BASELINE_SHA256=' + BASELINE_SHA, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=False)
    h = None
    result = 0
    summary = {
        'record_type': 'C925_GOT_SAME_MENU_NO_INPUT_TWO_EXACT_ELF_AB',
        'title_id': 'CUSA11456',
        'purpose': 'Compare missing Continue-menu graphics only',
        'gameplay_requested': False,
        'game_input_injected': False,
        'all_screenshots_menu_untouched_expected': True,
        'base_commit': GIT_HEAD,
        'binary_sha256': {'cb202701': CURRENT_SHA, 'baseline195934': BASELINE_SHA},
        'build_performed': False, 'source_modified': False,
        'runs': {}, 'status': 'PREFLIGHT',
    }
    try:
        h = get_helper()
        h.OUT, h.EXP, h.SRC, h.BUILD = OUT, EXPERIMENT, SOURCE, BUILD
        h.MODEL_SNAPSHOT = OUT / 'ollama-models.json'
        h.STATE = summary
        preflight(h)
        # One Ollama offload for both sequential runs. Model restored only after
        # the second emulator process exits (also on any failure).
        print('STAGE=VERIFIED_OLLAMA_OFFLOAD_ONCE_FOR_BOTH_MENU_RUNS', flush=True)
        h.offload_if_needed()
        for label, elf in (('cb202701', CURRENT), ('baseline195934', BASELINE)):
            if subprocess.run(['pgrep', '-x', 'shadps4'], capture_output=True).returncode == 0:
                raise RuntimeError('OTHER_SHADPS4_RUNNING_BEFORE_' + label)
            if h.free_vram() < 8 * (2**30):
                h.OUT = OUT
                h.offload_if_needed()
            if h.free_vram() < 8 * (2**30):
                raise RuntimeError('GPU_HEADROOM_BELOW_8GIB_BEFORE_' + label)
            folder = OUT / label
            folder.mkdir(parents=True, exist_ok=False)
            h.OUT = folder
            print('STAGE=RUN_MENU_ONLY_NO_INPUT_' + label.upper(), flush=True)
            print('DO_NOT_PRESS_CONTINUE_BUILD=' + label, flush=True)
            # The known source helper owns a strictly bounded 118+2sec game,
            # screenshots at 40/55/85/112sec and native Vulkan/SRT diagnostics.
            start = time.monotonic()
            try:
                h.launch(elf)
            finally:
                summary['runs'][label] = {
                    'elapsed_seconds': round(time.monotonic() - start, 2),
                    'exit_code': h.STATE.get('game_exit'),
                    'typed_resources': dict(h.STATE.get('maximum_typed_bindings', {})),
                    'gpu_device_lost': h.STATE.get('gpu_device_lost'),
                    'screenshots': dict(h.STATE.get('screenshots', {})),
                }
                logfile = folder / 'game-console.log'
                if logfile.is_file():
                    content = logfile.read_text(errors='replace')
                    interesting = (
                        'CB_SHADER_MASK_EXPORT_REMAP',
                        'SHARP_RESOURCE_PROVENANCE',
                        'Sharp source was not flatenned',
                        'SRT dependency resolution stalled',
                        'GuestIndexedImageDimensions:',
                        'IndexedBindingDecision:',
                        'ErrorDeviceLost', 'context is lost',
                        'Compiling fs shader', 'Compiling vs shader',
                        'Compiling graphics pipeline',
                    )
                    summary['runs'][label]['shader_counts'] = {
                        token: content.count(token) for token in interesting
                    }
                    interesting_lines = [line[:1300] for line in content.splitlines()
                                         if any(token in line for token in interesting)]
                    (folder / 'menu-gpu-evidence.txt').write_text(
                        '\n'.join(interesting_lines[:750]) + '\n')
                    with logfile.open('rb') as data, gzip.open(
                            folder / 'game-console.log.gz', 'wb', compresslevel=1) as gz:
                        shutil.copyfileobj(data, gz)
                    logfile.unlink()
            # No other game process is stopped or signaled; the tested launch
            # has already exited by the time this point is reached.
            time.sleep(3)
        print('STAGE=BOTH_MENU_ONLY_AB_RUNS_CAPTURED', flush=True)
        summary['status'] = 'MENU_ONLY_AB_COMPLETE_VISUAL_COMPARISON_REQUIRED'
        for label, res in summary['runs'].items():
            if not res['screenshots']:
                summary['status'] = 'ONE_OR_MORE_MENU_SCREENSHOTS_MISSING'
                result = 10
        check_digest(h, CURRENT, CURRENT_SHA, '202701_after_test')
        check_digest(h, BASELINE, BASELINE_SHA, '195934_after_test')
        check_digest(h, COPY, CURRENT_SHA, 'saved202701_after_test')
    except BaseException as exc:
        result = result or 10
        summary['status'] = 'FAILED_' + type(exc).__name__
        summary['exception'] = str(exc)
        (OUT / 'exception.txt').write_text(traceback.format_exc())
        print('TRIAL_STOP=' + str(exc), file=sys.stderr, flush=True)
    finally:
        if h is not None:
            h.OUT = OUT
            try:
                h.restore_ollama()
            except Exception as exc:
                summary['ollama_restore_error'] = str(exc)
        (OUT / 'trial.json').write_text(json.dumps(summary, indent=2) + '\n')
        try:
            diary = PROJECT / 'diary/trials.jsonl'
            diary.parent.mkdir(parents=True, exist_ok=True)
            with diary.open('a') as stream:
                stream.write(json.dumps(summary, separators=(',', ':')) + '\n')
        except OSError as exc:
            print('UNIVERSAL_DIARY_APPEND_ERROR=' + str(exc), file=sys.stderr)
        try:
            with tarfile.open(ARCHIVE, 'w:gz', compresslevel=1) as bundle:
                bundle.add(OUT, arcname='.')
            print('UPLOAD_THIS=' + str(ARCHIVE), flush=True)
        except OSError as exc:
            print('ARCHIVE_ERROR=' + str(exc), file=sys.stderr)
        print('SSH_SESSION=OPEN', flush=True)
        print('RESULT=' + summary['status'], flush=True)
    return result


if __name__ == '__main__':
    sys.exit(main())
