"""Host-only regression checks; no real desktop processes or services are stopped."""

import importlib.util
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("guard", Path(__file__).with_name("sunshine_session_guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
HOME = Path("/home/test")


def proc(pid, parent=1, name="shadps4", start=100):
    return {"pid": pid, "ppid": parent, "start": start, "comm": name,
            "exe": "/bin/" + name, "args": ["/bin/" + name]}


def event(text):
    return "[2026-10-01 18:00:00.000]: Info: " + text


class ClientTests(unittest.TestCase):
    def count(self, *events):
        return guard.clients_from_log(map(event, events))

    def test_report_phantom_client_sequence(self):
        events = ["Sunshine version: test"]
        # The 13:25 session exited without a CLIENT DISCONNECTED event.
        events += ["New streaming session started [active sessions: 1]", "CLIENT CONNECTED",
                   "App exited with code [0]", "Process terminated"]
        for _ in range(7):
            events += ["New streaming session started [active sessions: 1]", "CLIENT CONNECTED",
                       "CLIENT DISCONNECTED"]
        self.assertEqual(self.count(*events), 0)
        old_count = sum(e == "CLIENT CONNECTED" for e in events) - sum(
            e == "CLIENT DISCONNECTED" for e in events)
        self.assertEqual(old_count, 1)

    def test_absolute_count_repairs_missing_disconnect_without_process_exit(self):
        self.assertEqual(self.count("Sunshine version: test", "CLIENT CONNECTED",
                                    "New streaming session started [active sessions: 1]",
                                    "CLIENT CONNECTED", "CLIENT DISCONNECTED"), 0)

    def test_two_clients_disconnect_one_still_active(self):
        self.assertEqual(self.count("Sunshine version: test",
                                    "New streaming session started [active sessions: 1]",
                                    "CLIENT CONNECTED",
                                    "New streaming session started [active sessions: 2]",
                                    "CLIENT CONNECTED", "CLIENT DISCONNECTED"), 1)

    def test_interleaved_connect_events_not_double_counted(self):
        self.assertEqual(self.count("Sunshine version: test",
                                    "New streaming session started [active sessions: 1]",
                                    "New streaming session started [active sessions: 2]",
                                    "CLIENT CONNECTED", "CLIENT CONNECTED",
                                    "CLIENT DISCONNECTED", "CLIENT DISCONNECTED"), 0)

    def test_unknown_log_never_authorizes_cleanup(self):
        for events in [[], ["CLIENT DISCONNECTED"], ["CLIENT CONNECTED", "CLIENT DISCONNECTED"],
                       ["Sunshine version: test", "New streaming session started [changed format]"]]:
            with self.subTest(events=events):
                self.assertIsNone(self.count(*events))

    def test_reconnect_protects_new_session(self):
        self.assertEqual(self.count("Sunshine version: test",
                                    "New streaming session started [active sessions: 1]",
                                    "CLIENT CONNECTED", "CLIENT DISCONNECTED",
                                    "New streaming session started [active sessions: 1]"), 1)


class ProcessTests(unittest.TestCase):
    def test_descendants_and_reparented_children_remain_tracked(self):
        processes = {10: proc(10, name="es-de"), 11: proc(11, 10, "bash"),
                     12: proc(12, 11, "unknown-emulator"), 20: proc(20, name="sunshine"),
                     30: proc(30, name="ollama"), 40: proc(40, name="sshd")}
        selected = guard.select_processes(processes, {}, HOME)
        self.assertEqual(set(selected), {10, 11, 12})
        del processes[10]
        processes[11]["ppid"] = 1
        previous = {str(pid): p["start"] for pid, p in selected.items()}
        self.assertEqual(set(guard.select_processes(processes, previous, HOME)), {11, 12})

    def test_reused_pid_and_unrelated_shell_are_excluded(self):
        shell = proc(12, name="bash", start=200)
        shell["args"] = ["bash", "-c", "echo shadps4 /home/test/apps/es/es-de"]
        self.assertEqual(guard.select_processes({12: shell}, {"12": 100}, HOME), {})

    def test_known_orphans_and_exact_helper_path(self):
        helper = proc(10, name="dwarfs")
        helper["args"] += ["/home/test/Applications/rpcs3/rpcs3.AppImage"]
        unrelated = proc(11, name="dwarfs")
        unrelated["args"] += ["/home/test/other.AppImage"]
        processes = {10: helper, 11: unrelated, 12: proc(12, name="shadPS4")}
        self.assertEqual(set(guard.select_processes(processes, {}, HOME)), {10, 12})

    def test_helper_and_ancestors_are_never_targets(self):
        processes = {10: proc(10, name="es-de"), 11: proc(11, 10, "python3")}
        with patch.object(guard.os, "getpid", return_value=11):
            self.assertEqual(guard.select_processes(processes, {}, HOME), {})

    def test_pidfd_does_not_signal_reused_identity(self):
        with patch.object(guard.os, "pidfd_open", return_value=80), \
             patch.object(guard.os, "close") as close, \
             patch.object(guard, "read_process", return_value=proc(12, start=101)), \
             patch.object(guard.signal, "pidfd_send_signal") as send:
            self.assertFalse(guard.signal_process(proc(12), signal.SIGTERM))
            send.assert_not_called()
            close.assert_called_once_with(80)

    def test_reconnect_cancels_force(self):
        with patch.object(guard, "client_count", side_effect=[0, 0, 1]), \
             patch.object(guard, "tracked_processes", return_value={12: proc(12)}), \
             patch.object(guard, "signal_process") as send, \
             patch.object(guard, "log"):
            guard.cleanup(HOME)
            send.assert_called_once_with(proc(12), signal.SIGTERM)

    def test_unknown_state_cannot_kill(self):
        with patch.object(guard, "client_count", return_value=None), \
             patch.object(guard, "tracked_processes") as scan, \
             patch.object(guard, "signal_process") as send:
            guard.cleanup(HOME)
            scan.assert_not_called()
            send.assert_not_called()


class InstallationTests(unittest.TestCase):
    def test_patching_preserves_display_recovery(self):
        functions = "".join(name + "()\n{\n    :\n}\n\n" for name in [
            "moonlight_client_count", "live_es_pids", "cleanup_orphan_es_helpers", "cleanup_es"])
        display = 'repair_x11()\n{\n    xrandr --query\n}\n\n'
        loop = '''while :; do
    repair_x11
    clients="$(moonlight_client_count)"
    if [ "$es" != "$last_es" ]; then
            no_client_count=0
            last_es="$es"
    fi
    sleep 2
done
'''
        result = guard.patched_watchdog((functions + display + loop).encode(),
                                       Path("/home/with space/helper.py")).decode()
        self.assertIn(display, result)
        self.assertNotIn("no_client_count=0", result)
        self.assertIn('|| clients=UNKNOWN', result)
        self.assertEqual(subprocess.run(["bash", "-n"], input=result, text=True).returncode, 0)

    def test_changed_watchdog_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            watchdog = home / ".local/bin/sunshine-display-watchdog"
            watchdog.parent.mkdir(parents=True)
            watchdog.write_text("newer locally edited watchdog\n")
            with self.assertRaisesRegex(RuntimeError, "differs"):
                guard.install(home)
            self.assertEqual(watchdog.read_text(), "newer locally edited watchdog\n")


if __name__ == "__main__":
    unittest.main()
