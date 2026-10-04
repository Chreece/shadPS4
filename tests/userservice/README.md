<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# User-colour startup regression

The local ac36a0ed crash report records a host read from address 0x28 after pad
initialization, before NGS2 loads. The retained executable contains a candidate
instruction at the same page offset in sceUserServiceGetUserColor: it reads
user_color at offset 0x28 directly from GetUserByID's result. That lookup can
return null. Without the crashed process's load map or backtrace, this is a
matching concrete defect, not a fully symbolicated crash frame.

The test compiles the production HLE entry point and substitutes only the user
store and logger. It covers absent and removed users, null output, the invalid
user sentinel, unchanged output on failure and the existing colour mapping for
valid users. It neither loads nor writes a real user profile.

```sh
cmake -S tests/userservice -B build-userservice -DCMAKE_BUILD_TYPE=Release
cmake --build build-userservice
ctest --test-dir build-userservice --output-on-failure
```

GCC 13.3 Release passes. Linking the same fixture against the previous production
entry point reproduces SIGSEGV on the missing-user query. The complete Docker
emulator build and subsequent game startup still need verification on the target
host. This change does not establish a fix for missing cutscene audio or crackles.

## Combined-main regression

The working diagnostic revision f1c1c790 included this guard. The clean audio
integration omitted it, leaving main 2abd0fb0 with the unchecked lookup again.
The user reported immediate game-launch failure with that combined build.
Restore the same guard on `fix/userservice-missing-user` and merge it separately
into main; the standalone audio branch remains independent of this startup fix.
The main local build must run this regression even during graphics iterations.

The missing-user fixture reproduces SIGSEGV against the function extracted from
2abd0fb0, and passes against the restored production translation unit. This
establishes the code regression; the new host crash trace has not yet been
provided, so it does not establish that every startup failure has this cause.
