#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the isolated texture-containment candidate using the existing Docker cache."""

import subprocess
import sys
import zipfile

import local_build

REVISION = 'f32415fc426b8aa5577095cdc7efe2750852e65e'


if __name__ == '__main__':
    local_build.REVISION = REVISION
    local_build.GRAPHICS_TEST = True
    try:
        local_build.main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('GRAPHICS_LOCAL_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
