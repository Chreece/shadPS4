# Sunshine session cleanup repair

This host-only helper repairs the reviewed `sunshine-display-watchdog` implementation.
It does not modify or rebuild the emulator, change Sunshine configuration, or change audio,
saves, gamepad controls, or the display-recovery function. AI-assisted implementation.

The October 1 diagnostic showed a phantom client: the 13:25 session ended without a
`CLIENT DISCONNECTED` record. The old cumulative connect/disconnect counter remained at
one, so the 20-second idle cleanup never ran. The separate `sunshine-esde-idle-guard`
service was inactive. The active watchdog also targeted ES-DE without retaining its
emulator descendants.

The repaired counter uses `New streaming session started [active sessions: N]` as an
absolute count and does not count the following connection event twice. Sunshine's
`Process terminated` stream-control shutdown resets the count. Unknown or unreadable
state cannot authorize termination. This remains a log-based detector: unreported
disconnects cannot be inferred before a subsequent authoritative session event.

Every watchdog poll remembers the current user's ES-DE/emulator processes and their
descendants by PID, process start time, and boot ID. Reparented descendants stay tracked.
Exact executable names also find already-orphaned common emulators. Unknown executables
are covered when observed as descendants; this is not a universal emulator name database.
No arbitrary command substring or entire user process group is killed.

After the existing idle grace, the helper sends TERM, allows four seconds to exit, then
sends KILL to surviving original identities using Linux pidfds. It checks for a reconnect
before every signal and during the grace period. A reconnect cancels remaining cleanup;
it cannot undo TERM already delivered. Zombie processes are ignored. Cleanup messages
are appended to `~/.local/state/sunshine-display-watchdog.log`.

## Installation and local test

Run `python3 sunshine_session_guard.py --install` as the normal desktop user. It checks
the exact reviewed watchdog checksum, verifies the service user and executable, refuses
a competing active old guard, backs up the original, installs the helper, and restarts
only `sunshine-display-watchdog.service`. `sudo` is needed for the service restart.
The original display-recovery function is retained byte for byte. The program prints a
backup location and a guarded RESTORE command; a service restart failure restores the
original script automatically.

Launch ES-DE and a game from Moonlight, disconnect Moonlight, and wait about 30 seconds.
The watchdog log should report zero clients followed by process termination. Reconnect
within 20 seconds in a separate test: the active session should remain running. With two
clients connected, disconnecting only one must not trigger cleanup.

Host tests: `python3 -m unittest discover -s documents/ngs2 -p test_sunshine_session_guard.py -v`.
The actual uploaded event sequence was also replayed: old count 1, repaired count 0.
These checks do not establish successful cleanup on the user's live machine.
