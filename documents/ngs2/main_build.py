#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build combined main locally: NGS2 audio, sparse queues, and texture containment."""

import subprocess
import sys
import zipfile

import local_build

REVISION = '2abd0fb0f807e84713517e6a25e982043897353f'


if __name__ == '__main__':
    local_build.REVISION = REVISION
    local_build.SOURCE_BRANCH = 'main'
    local_build.GRAPHICS_TEST = True
    try:
        local_build.main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('MAIN_LOCAL_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
