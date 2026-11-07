#!/usr/bin/env python3
"""Run the actual Linux executable against isolated XDG data directories."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if sys.platform != 'linux':
        raise SystemExit('This startup regression requires Linux XDG directory selection')
    if len(sys.argv) != 2:
        raise SystemExit('Usage: test_cli_user_directories.py /absolute/path/to/shadps4')
    import resource

    binary = Path(sys.argv[1]).resolve(strict=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryDirectory(prefix='shadps4-userdirs-') as tmp:
        root = Path(tmp)
        data = root / 'missing' / 'parents' / 'data'
        env = dict(os.environ, XDG_DATA_HOME=str(data))

        def run():
            return subprocess.run([str(binary), '--help'], cwd=root, env=env,
                                  capture_output=True, text=True, timeout=20)

        first = run()
        if first.returncode != 0 or 'shadPS4 Emulator CLI' not in first.stdout:
            raise AssertionError((first.returncode, first.stdout, first.stderr))
        user = data / 'shadPS4'
        assert user.is_dir(), 'missing parent directories were not created'
        marker = user / 'preserve-existing-data.txt'
        marker.write_text('existing data must survive startup\n')
        second = run()
        assert second.returncode == 0, second.stderr
        assert marker.read_text() == 'existing data must survive startup\n'

        conflict = root / 'conflict'
        conflict.mkdir()
        occupied = conflict / 'shadPS4'
        occupied.write_text('keep this regular file\n')
        env['XDG_DATA_HOME'] = str(conflict)
        refused = run()
        assert refused.returncode != 0, 'a file cannot be used as a directory'
        assert occupied.read_text() == 'keep this regular file\n'
    print('NATIVE_CLI_USER_DIRECTORIES=PASS scenarios=3 GPU_NOT_RUN')


if __name__ == '__main__':
    main()
