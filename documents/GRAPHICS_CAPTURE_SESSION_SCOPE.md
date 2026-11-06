# Graphics capture session boundaries

The evidence collector must not classify an entire accumulated startup log as
fresh just because the current launch changed its modification time. This caused
older game-not-found messages to appear alongside the verified current launch in
the 2026-10-02 report.

`emulator.log` captures the selected child's stdout and stderr directly. The
worker also snapshots the two optional shared logs immediately before launch,
recording file identity, byte count and a hash of the existing prefix. After the
child exits, it copies only verified appended bytes, or a file that was absent
before launch. It freezes these copies before publishing `exit.json`, so later
launches cannot contaminate the archive while the collector prepares it.

`renderer-logs-before.json` records the boundaries. `renderer-logs.json` and
`summary.json.renderer_log_capture` explain each captured or omitted file.
Unchanged files, including files merely touched, are not copied. Rotated,
replaced, truncated or rewritten files have no provable byte boundary and are
explicitly omitted. The direct child capture remains available. The collector
does not truncate, delete or rotate the user's original logs. An interrupted
session without an exit record has no finalized supplemental logs and remains
incomplete; the launcher is restored without stopping the game.

## Verification on 2026-10-03

`python3 -m unittest -v test_collect_graphics_evidence test_install_local_default`
from `scripts/` passes 31 checks. Coverage includes historical-prefix exclusion,
touch-only changes, new files, truncation and same-size/larger rewrites, rotation,
missing boundaries, size limits, interrupted captures and launcher restoration.
Real child-process/archive tests also exclude entries written after arming but
before launch and after the captured child finishes. Running that regression
against the old collector fails because its archive includes historical and
following-session entries.

Emulator source, build definitions, existing C++ tests and the default installer
match the pre-integration baseline `ac9122931a1d19467e1288e29350a8fbd9bf7a14`.
This maintenance change does not rebuild or deploy an emulator, change settings,
or establish that remaining visual glitches are fixed. Runtime acceptance still
requires the user's observations as well as relevant logs.
