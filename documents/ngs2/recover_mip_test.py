#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture the failed tiled-mip test and restore the retained 6e00d2cc launcher."""
import os
from pathlib import Path
import subprocess
import sys

import recover_crash


def main():
    if os.geteuid() == 0 or sys.platform != 'linux':
        raise RuntimeError('Run on your Linux desktop account, without sudo.')
    recover_crash.WORKING = '6e00d2ccadfa4ec1f5a7bbd46ad8a233857bbbfa'
    recover_crash.DIAGNOSTIC = {'286d0cca483ce80f9d4a4fe98d4620b6b003e0ca'}
    recover_crash.recover(Path.home())


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('NGS2_RECOVERY=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
