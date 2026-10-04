# Kernel unlink: propagate failed deletion

Topic branch: `fix/kernel-unlink-errors`. Base:
`d3630d5df16c60eef579f4f9098065fd145a1b22`.

## Evidence and correction

The user's `shadps4-graphics-evidence-0pxgw5wo.tar.gz` captured this exact base
binary on 2026-10-03. During the options-save sequence, opening
`TEMP0_RDR2OPTIONS.SAV` failed with "No such file or directory", followed by
`posix_unlink: Unlinked /savedata0/TEMP0_RDR2OPTIONS.SAV`. The game continued
creating the temporary file and backing up its options. This demonstrates a
false syscall result and log message, not save corruption or a graphics defect.

`posix_unlink` previously ignored both the temporary open result and the deletion
result. `IOFile::Unlink` returned void, including when it had no open file or the
host deletion failed. This change:

- Returns the native error from `IOFile::Unlink`, preserving it across logging.
- Checks both the temporary open and unlink results, preserving the error across
  temporary-file destruction before translating it to guest errno.
- Reports `-1` with guest errno for the POSIX export, and the matching kernel
  error for `sceKernelUnlink`; logs success only on a successful operation.
- Converts filesystem status failures without throwing from the directory check.
- Rejects a null path with `EFAULT` before `strlen`, preventing a reproduced host
  crash, and preserves the existing 255-character path limit.
- Rejects a selected backend without a writable host file with `EROFS`.
- Adds missing native-to-guest mappings for `ENAMETOOLONG` and `ELOOP`.

The return/error contract follows the public
[POSIX unlink specification](https://pubs.opengroup.org/onlinepubs/9699919799/functions/unlink.html)
and [FreeBSD unlink manual](https://man.freebsd.org/cgi/man.cgi?query=unlink&sektion=2).
This is not a new native PS4 ABI claim. Export identifiers and signatures are
unchanged.

The existing Windows delete-disposition operation now checks its
[NTSTATUS result](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntsetinformationfile)
using the [success/informational range](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/using-ntstatus-values).
Unmapped NT failures conservatively return `EIO` and record the raw NTSTATUS.
Windows runtime validation and detailed NTSTATUS-to-errno translation remain
out of this Linux-focused test's scope.

## Focused validation

The fixture compiles the full production `io_file.cpp`, extracts the exact two
unlink syscall bodies and the production errno functions, and supplies only a
synthetic mount/handle table and logger. It uses real temporary files and real
host filesystem operations. It does not load a game, exercise real mount
resolution, or compile the full kernel translation unit. Extraction markers fail
configuration if the source boundaries change.

On the original code, missing-file and already-removed-file operations returned
false success, the SCE export failed to report `ENOENT`, the null test crashed,
the missing-host-backend test returned false success, and the errno mapping was
wrong. An actual permission-denied deletion also returned false success. Its
fixture must run outside a sandbox that forbids dropping a child process's UID;
the initial sandbox denial was environmental, not evidence about the syscall.

After the fix, all 14 cases pass on Linux x86-64, GCC 13.3.0:

- Release and Debug ASan/UBSan, with leak checking enabled.
- Missing file, SCE error translation, null pointer, long path, directory,
  read-only mount, unknown mount and successful deletion.
- Open-file deletion retains a readable descriptor and its contents.
- Already-removed path, missing host backend, ABI errno conversion,
  permission-denied deletion, and direct IOFile failure results.
- The logger deliberately overwrites host errno to check error preservation.

The existing installer suite also passes all 21 tests. The local Docker installer
runs `kernel_unlink` before the full Linux emulator build. No all-platform CI
build is required for this local-first iteration.

```sh
cmake -S tests/kernel_unlink -B build-kernel-unlink -DCMAKE_BUILD_TYPE=Release
cmake --build build-kernel-unlink
ctest --test-dir build-kernel-unlink --output-on-failure --no-tests=error

cmake -S tests/kernel_unlink -B build-kernel-unlink-asan \
  -DCMAKE_BUILD_TYPE=Debug -DKERNEL_UNLINK_SANITIZERS=ON
cmake --build build-kernel-unlink-asan
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ctest --test-dir build-kernel-unlink-asan --output-on-failure --no-tests=error
```

## Local game evidence still required

The next capture must identify the new binary, include an ordinary save cycle,
and end normally. Missing temporary-file cleanup may now log
`Unlink ... failed, error = 2`; that is the correct `ENOENT` result, not a newly
lost save. Check subsequent temporary-file creation, options/save activity and
the user's ability to resume normally. Do not equate fewer error messages with
correctness.

This is a narrow error-propagation correction. The existing temporary read/write
open requirement, mount resolution, symlink semantics and concurrency design
are not redesigned here. The registry-manager stub, optional resources and
remaining visual glitches are separate issues. No audio, renderer, input,
Sunshine, game data or emulator settings are changed by this patch.

Prepared with OpenAI Codex.
