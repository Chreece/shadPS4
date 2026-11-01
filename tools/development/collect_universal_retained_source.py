#!/usr/bin/env python3
"""Read-only capture of unpublished emulator source needed for selective ports.

Only C/C++ files under src and the root CMakeLists are included. Game files,
shader dumps, settings, executables, and dependency trees are excluded.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def main():
    project, out = map(lambda p: Path(p).resolve(), sys.argv[1:3])
    candidates = [
        project / 'GhostOfTsushima/experiments/got-zero-record-20261031T111922Z-3911244/source',
        project / 'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z/source',
        project / 'tests/gds-pr117-native-20261029T164137Z-3996104/source',
    ]
    suffixes = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.inl', '.inc'}
    results = []
    for index, source in enumerate(candidates):
        record = {'path': str(source), 'status': 'MISSING'}
        results.append(record)
        if not source.is_dir() or not source.resolve().is_relative_to(project):
            continue
        source = source.resolve()
        paths = [p for p in (source / 'src').rglob('*')
                 if p.suffix in suffixes and p.is_file() and not p.is_symlink()
                 and p.resolve().is_relative_to(source / 'src')]
        cmake = source / 'CMakeLists.txt'
        if cmake.is_file() and not cmake.is_symlink():
            paths.append(cmake)
        if sum(p.stat().st_size for p in paths) > 100 * 1024 * 1024:
            record['status'] = 'SOURCE_EXCEEDS_100_MIB_LIMIT'
            continue
        snapshot = f'retained-source-{index}.tar.gz'
        manifest = {}
        with tarfile.open(out / snapshot, 'w:gz') as archive:
            for p in sorted(paths):
                rel = str(p.relative_to(source))
                before = p.read_bytes()
                manifest[rel] = hashlib.sha256(before).hexdigest()
                # Archive the same bytes as the manifest, even if another session edits the tree.
                import io
                info = tarfile.TarInfo(rel)
                info.size = len(before)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(before))
        record.update(status='CAPTURED_SOURCE_ONLY', archive=snapshot, sha256=manifest)
        try:
            result = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                    capture_output=True, text=True, timeout=20)
            record['git_commit'] = result.stdout.strip() if result.returncode == 0 else None
        except subprocess.TimeoutExpired:
            record['git_commit'] = None
    (out / 'retained-source-manifest.json').write_text(json.dumps(results, indent=2) + '\n')
    print('RETAINED_SOURCE_CAPTURED=' + str(sum(r['status'] == 'CAPTURED_SOURCE_ONLY' for r in results)))


if __name__ == '__main__':
    main()
