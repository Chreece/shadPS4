# PES CUSA18676 diagnostic validation, 2026-10-04

## Scope and current verdict

This branch changes the video diagnostic only. It does not establish a PES
root cause or repair the emulator. The installed emulator remains pinned to
`0539f6dba2a1b075aa017c691b2c8955258e5a1e` on
`feat/startup-loading-indicator`, with binary SHA256
`370c0c31b36b1afa464c67cf30974cb19fe21dd3b13cb07bb35d9d8c45f79800`.

No emulator build, install, launcher, save, audio/7.1 setting, or retained fix
was changed. Emulator builds remain subject to the local-Docker-first rule.

The updated disposable Docker self-test passed all four cases in the user's
`selftest-evidence.tar.gz`: idle (20.222 seconds), active (20.199 seconds),
manual interruption and target exit. Active decoding produced the expected
32 entries/returns, 6 errors and 26 valid frames. Surviving targets resumed
with `TracerPid=0`.

The user ran the real-GDB Docker self-test at `67d92a2d`: the idle case did
not stop at 20 seconds. The external watchdog interrupted exactly one GDB at
25.215 seconds and it detached successfully. The collector now schedules that
external interrupt at 20 seconds and removes the GDB Python timer thread.
It distinguishes a scheduled stop from manual interruption and retains the
25-second overrun check.

The subsequent PES archive `shadps4-pes-video-qmcgz5sy.tar.gz` records 20.086
seconds, 36 hooked APIs with zero observed calls, normal GDB exit and verified
detach. The parent marked it failed because the helper's exact-one-match
check failed. Its stdout was not archived, so the reason for that mismatch
is unknown. New captures retain the helper's stdout, stderr and exit status in
`interrupt-helper.jsonl`, including failures/timeouts. Signal selection and
the strict acceptance check are unchanged; this capture is not reclassified.

The earlier `shadps4-graphics-evidence-5s5x8div` contains 3,383,796 bytes of
direct console output but only 123 fresh bytes in `shadps4.log`. Video-only
named-log filtering cannot establish the earlier startup history. The passive
`collect_pes_runtime_context.py` now reads the running process's stdout/stderr
destinations when they are regular files, named logs and three procfs activity
samples. It never attaches, signals, resumes or changes an emulator process;
it skips pipes/devices and caps each log at 16 MiB with exact head/tail offsets.
Its next run requires PES to remain open but does not repeat the video trace.

## Evidence retained

The supplied capture summary reports zero entries across 36 hooked video APIs,
229.513 seconds for an intended 20-second window, and successful detach. This
does not show that video decoding never started, that a decoder API failed, or
that the game is permanently waiting for the GPU.

The archived `shadps4-pes-wait-p_j_varu` capture was re-read during this work:

| Snapshot | Current query key | Selected expected value | Selected observed value |
| --- | --- | --- | --- |
| `wait-0.txt` | `0xe5193` | `0xe5193` | `0x0` |
| `wait-1.txt` | `0x125895` | `0x125895` | `0x0` |
| `wait-2.txt` | `0x166999` | `0x166999` | `0x0` |

The key and selected entry changed. A separate sampled entry was satisfied:
expected/observed `0x80400` in snapshot 0 and `0x100400` in snapshots 1 and 2.
The `CriManaDecodeTh` and `CriThread` stacks show condition-variable waits in
all three snapshots. These observations do not establish why startup stays black.

## Reproduced diagnostic failures

The starting diagnostic revision was
`dd9025bf45d0e58e7812bd82ae21eb17a303359d`. Before correction, executable
collector regression tests demonstrated:

1. A complete JSON result followed by debugger exit code 7 could print PASS.
2. Malformed result JSON raised before the evidence archive was created.
3. Replaying a 229.513-second result labelled complete without the probe's
   overrun flag could print PASS. The parent did not independently validate time.

The parent now retains the watchdog reason, debugger return code, elapsed
capture/cleanup time, and interrupt-helper errors. It rejects incomplete,
overlong, malformed, or failed results and preserves an archive on those tested
paths. It independently reads the original target's start ticks, `TracerPid`,
and process state before accepting cleanup. It does not resume or signal the
target to make a cleanup check pass.

At `91c2da75`, the embedded GDB probe was only reformatted. The subsequent
Docker failure above provided evidence for replacing its timer mechanism.

## Validation performed

Run the narrow regression suite with:

```sh
python3 -m unittest discover -s scripts -p test_trace_video_progress.py -v
```

All 16 tests passed. This includes full collector report/archive paths, valid
and invalid results, overrun replay, nonzero child exit, target cleanup checks,
short/missing timing, and watchdog subprocess behavior.

The production 25-second capture deadline (20 seconds plus 5 seconds grace)
was exercised against a disposable child process. The observed return time was
25.042 seconds, with cooperative SIGINT exit. An uncooperative child was
reported pending after the shortened test cleanup deadline. A failing interrupt
helper was retained as an error rather than escaping before report generation.

**These are protocol/subprocess tests, not live GDB tests.** Tests replace the
debugger-discovery helper boundary with SIGINT sent to their own child. They do
not validate privileged helper discovery, actual attach, breakpoint handling,
`gdb.Thread`, `gdb.interrupt`, inferior resumption, or detach on a real GDB target.

## Blocking environment evidence and next gate

Shell execution succeeds. Docker and GDB are absent in this execution
environment. A disposable child's `PTRACE_TRACEME` returned `-1` with `errno=1`
(operation not permitted). `/proc/self` and `os.getpid()` also expose different
PID namespaces here, preventing an honest end-to-end test of the `/proc`/pidfd
debugger selector. No sandbox or security setting was changed.

Before another user capture, validate the unchanged probe and helper with real
GDB in a suitable local Docker/test environment using disposable targets:

- An idle target with the required symbols and zero API activity must stop its
  observation at the requested time, remove breakpoints, detach, and continue.
- An active target must produce matching entries/returns and expected frame
  fields, including the per-API cap.
- Setup failure, interrupt, target exit, and delayed cleanup must never print
  PASS or leave an unreported debugger attachment.
- The helper must identify only its own debugger and leave other processes and
  the invoking shell/session untouched.

If runtime validation fails, keep changes on the diagnostic branch and preserve
the failing fixture and report. Do not install a speculative emulator fix or
repeat the PES capture to compensate for an unvalidated collector.
