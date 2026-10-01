"""Focused host repair checks; never access or print real X11 authorization records."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("repair", Path(__file__).with_name("repair_sunshine_xauthority.py"))
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class AuthorityTests(unittest.TestCase):
    def test_only_current_users_headless_server_is_selected(self):
        argv = ["/usr/lib/xorg/Xorg", "-nolisten", "tcp", ":0", "-auth", "/tmp/serverauth.ABC123"]
        group = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/headless-x.service\n"
        self.assertIsNotNone(repair.server_record(22, argv, group, 1000))
        self.assertIsNone(repair.server_record(22, argv, group, 1001))
        self.assertIsNone(repair.server_record(22, argv, group.replace("headless-x", "unrelated"), 1000))
        self.assertIsNone(repair.server_record(22, [a.replace(":0", ":1") for a in argv], group, 1000))
        self.assertIsNone(repair.server_record(22, argv[:-1] + ["/etc/shadow"], group, 1000))

    def test_working_authority_is_not_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = home / ".Xauthority"
            auth.write_bytes(b"existing-test-data")
            with patch.object(repair.shutil, "which", return_value="/bin/mock"), \
                 patch.object(repair, "query", return_value=subprocess.CompletedProcess([], 0, b"display info", b"")), \
                 patch.object(repair, "find_server") as find:
                repair.repair(home)
            self.assertEqual(auth.read_bytes(), b"existing-test-data")
            find.assert_not_called()

    def test_unverified_candidate_does_not_change_client_file(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = home / ".Xauthority"
            auth.write_bytes(b"original")
            server = {"authority": "/tmp/serverauth.TEST", "pid": 22}
            with patch.object(repair.shutil, "which", return_value="/bin/mock"), \
                 patch.object(repair, "query", return_value=subprocess.CompletedProcess([], 1, b"", b"denied")), \
                 patch.object(repair, "find_server", return_value=server), \
                 patch.object(repair, "read_authority", return_value=b"synthetic"), \
                 patch.object(repair.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"synthetic", b"")):
                with self.assertRaisesRegex(RuntimeError, "candidate authorization"):
                    repair.repair(home)
            self.assertEqual(auth.read_bytes(), b"original")

    def test_final_access_failure_restores_original(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = home / ".Xauthority"
            auth.write_bytes(b"original")
            server = {"authority": "/tmp/serverauth.TEST", "pid": 22}
            failed = subprocess.CompletedProcess([], 1, b"", b"denied")
            success = subprocess.CompletedProcess([], 0, b"display info", b"")
            def xauth(args, **kwargs):
                if "merge" in args:
                    Path(args[2]).write_bytes(b"replacement")
                return subprocess.CompletedProcess([], 0, b"synthetic", b"")
            with patch.object(repair.shutil, "which", return_value="/bin/mock"), \
                 patch.object(repair, "query", side_effect=[failed, success, failed]), \
                 patch.object(repair, "find_server", return_value=server), \
                 patch.object(repair, "read_authority", return_value=b"synthetic"), \
                 patch.object(repair.subprocess, "run", side_effect=xauth):
                with self.assertRaisesRegex(RuntimeError, "Final X11"):
                    repair.repair(home)
            self.assertEqual(auth.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
