# PES CUSA18676 diagnostic validation, 2026-10-04

## Scope and current verdict

This branch changes the video diagnostic only. It does not establish a PES
root cause or repair the emulator. The installed emulator remains pinned to
`0539f6dba2a1b075aa017c691b2c8955258e5a1e` on
`feat/startup-loading-indicator`, with binary SHA256
`370c0c31b36b1afa464c67cf30974cb19fe21dd3b13cb07bb35d9d8c45f79800`.

No emulator build, install, launcher, save, audio/7.1 setting, or retained fix
was changed. Emulator builds remain subject to the local-Docker-first rule.

**Do not request another PES capture yet. Live GDB validation remains blocked.**

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

The embedded GDB probe was reformatted from an escaped one-line literal into a
readable literal; its executable content is unchanged from `dd9025bf` apart from
leading whitespace. The timer/interrupt mechanism has not been claimed fixed.

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
