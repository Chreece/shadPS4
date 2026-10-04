# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collector regressions. Protocol fixtures do NOT validate GDB/ptrace behavior."""

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import trace_video_progress as trace


def complete_report():
    return {"status": "complete", "detached": True, "seconds": 20.001,
            "stop_reason": "capture_complete",
            "requested_seconds": 20, "deadline_exceeded": False,
            "apis": {api: {"calls": 0} for api in (
                "sceVideodec2CreateDecoder", "sceVideodec2Decode", "sceVideodec2Flush")},
            "records": [], "errors": []}


class CollectorTests(unittest.TestCase):
    def exercise(self, payload, returncode=0):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            # Some execution sandboxes expose a procfs from another PID namespace.
            # Use its self identity for read-only inspection; never signal that PID.
            own_stat = Path('/proc/self/stat').read_text()
            identity = {"pid": int(own_stat.split()[0]), "start_ticks":
                        own_stat.rsplit(')', 1)[1].split()[19],
                        "executable": sys.executable, "sha256": "fixture"}
            actual_read = Path.read_text
            actual_popen = subprocess.Popen

            def read(path, *args, **kwargs):
                if str(path) == '/proc/sys/kernel/yama/ptrace_scope':
                    return '0'
                return actual_read(path, *args, **kwargs)

            def launch(command, **kwargs):
                code = ('print("PES_VIDEO_ARMED=20_seconds", flush=True)\n'
                        'print(' + repr('PES_VIDEO_JSON=' + payload) + ', flush=True)\n'
                        'raise SystemExit(' + str(returncode) + ')\n')
                return actual_popen([sys.executable, '-c', code], **kwargs)

            output = io.StringIO()
            error = None
            with patch.object(Path, 'home', return_value=home), \
                    patch.object(Path, 'read_text', read), \
                    patch.object(trace.os, 'geteuid', return_value=1000), \
                    patch.object(trace.shutil, 'which', return_value='/fixture/gdb'), \
                    patch.object(trace, 'find_process', return_value=identity), \
                    patch.object(trace, 'collect_log_lines', return_value=[]), \
                    patch.object(trace.subprocess, 'run', return_value=subprocess.CompletedProcess(
                        [], 0, 'PES_VIDEO_GDB_CHECK=PASS\n', '')), \
                    patch.object(trace.subprocess, 'Popen', side_effect=launch), \
                    contextlib.redirect_stdout(output):
                try:
                    trace.run()
                except Exception as caught:
                    error = caught
            archives = list(home.glob('*.tar.gz'))
            report = None
            if archives:
                with tarfile.open(archives[0]) as archive:
                    item = next(m for m in archive.getmembers()
                                if m.name.endswith('/video-trace.json'))
                    report = json.load(archive.extractfile(item))
            return error, output.getvalue(), report

    def test_nonzero_debugger_exit_cannot_pass(self):
        error, output, report = self.exercise(json.dumps(complete_report()), 7)
        self.assertIsNotNone(error)
        self.assertNotIn('PES_VIDEO_RESULT=PASS', output)
        self.assertIsNotNone(report)
        self.assertNotEqual(report['status'], 'complete')

    def test_valid_result_passes_with_cleanup_observed(self):
        error, output, report = self.exercise(json.dumps(complete_report()))
        self.assertIsNone(error)
        self.assertIn('PES_VIDEO_RESULT=PASS', output)
        self.assertTrue(report['cleanup_verified'])
        self.assertEqual(report['target_after']['tracer_pid'], 0)

    def test_invalid_result_still_produces_an_archive(self):
        error, output, report = self.exercise('{broken')
        self.assertIsNotNone(error)
        self.assertIsNotNone(report)
        self.assertIn('PES_VIDEO_REPORT=', output)
        self.assertEqual(report['status'], 'failed')

    def test_old_overlong_complete_result_cannot_pass(self):
        old = complete_report()
        old['seconds'] = 229.513
        old.pop('deadline_exceeded')
        error, output, report = self.exercise(json.dumps(old))
        self.assertIsNotNone(error)
        self.assertNotIn('PES_VIDEO_RESULT=PASS', output)
        self.assertEqual(report['status'], 'overrun')


class ReportTests(unittest.TestCase):
    def report(self, payload=None, *, reason='exited', returncode=0, exited=True,
               target=None, cleanup_seconds=20.1):
        outcome = {'reason': reason, 'errors': [], 'returncode': returncode,
                   'debugger_exited': exited, 'armed': True,
                   'capture_and_cleanup_seconds': cleanup_seconds}
        target = target or {'status': 'observed', 'tracer_pid': 0, 'state': 'S'}
        return trace.build_report('PES_VIDEO_JSON=' + json.dumps(
            complete_report() if payload is None else payload), outcome, target)

    def test_detach_duration_counts_towards_deadline(self):
        report = self.report(cleanup_seconds=229.513)
        self.assertEqual(report['status'], 'overrun')
        self.assertFalse(trace.report_passed(report))

    def test_no_pass_without_scheduled_stop_evidence(self):
        for reason in (None, 'interrupted'):
            payload = complete_report()
            payload['stop_reason'] = reason
            self.assertFalse(trace.report_passed(self.report(payload)))

    def test_watchdog_reason_survives_complete_result(self):
        for reason in ('setup_timeout', 'capture_timeout', 'interrupted', 'monitor_failed'):
            with self.subTest(reason=reason):
                report = self.report(reason=reason)
                self.assertFalse(trace.report_passed(report))
                self.assertEqual(report['watchdog']['reason'], reason)

    def test_missing_or_short_window_never_passes(self):
        for seconds in (None, 0, 19.9, float('nan'), float('inf'), '20', True):
            with self.subTest(seconds=seconds):
                payload = complete_report()
                payload['seconds'] = seconds
                self.assertFalse(trace.report_passed(self.report(payload)))

    def test_cleanup_must_be_verified_independently(self):
        for target in ({'status': 'unverified'}, {'status': 'exited'},
                       {'status': 'identity_changed'},
                       {'status': 'observed', 'tracer_pid': 123, 'state': 'S'},
                       {'status': 'observed', 'tracer_pid': 0, 'state': 'T'}):
            with self.subTest(target=target):
                self.assertFalse(trace.report_passed(self.report(target=target)))

    def test_debugger_still_alive_is_explicit(self):
        report = self.report(exited=False, returncode=None)
        self.assertEqual(report['status'], 'detach_pending')
        self.assertFalse(trace.report_passed(report))

    def test_invalid_shape_or_missing_api_never_passes(self):
        for payload in ([], {'status': 'complete'},
                        dict(complete_report(), apis={}),
                        dict(complete_report(), apis={'bad': None})):
            with self.subTest(payload=payload):
                self.assertFalse(trace.report_passed(self.report(payload)))

    def test_pinned_binary_and_probe_syntax_preserved(self):
        self.assertEqual(trace.REVISION, '0539f6dba2a1b075aa017c691b2c8955258e5a1e')
        self.assertEqual(trace.BINARY_SHA256,
                         '370c0c31b36b1afa464c67cf30974cb19fe21dd3b13cb07bb35d9d8c45f79800')
        compile(trace.PROBE, '<video-probe>', 'exec')


class ProbeStopTests(unittest.TestCase):
    """Exercise the actual embedded decision function, without pretending to run GDB."""

    def decision(self, request, elapsed):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stop-request.json'
            if request is not None:
                path.write_text(request)
            function = next(n for n in ast.parse(trace.PROBE).body
                            if isinstance(n, ast.FunctionDef) and n.name == 'interrupted_status')
            namespace = {'Path': Path, 'json': json, 'time': time,
                         'CONFIG': {'stop_request': str(path)}, 'started': 100.0, 'result': {}}
            exec(compile(ast.Module(body=[function], type_ignores=[]), '<probe-stop>', 'exec'), namespace)
            with patch.object(time, 'monotonic', return_value=100 + elapsed):
                status = namespace['interrupted_status']()
            return status, namespace['result']

    def test_scheduled_stop_requires_the_full_window(self):
        request = json.dumps({'reason': 'capture_complete'})
        self.assertEqual(self.decision(request, 19.99)[0], 'interrupted')
        status, result = self.decision(request, 20.01)
        self.assertEqual(status, 'complete')
        self.assertEqual(result['stop_reason'], 'capture_complete')

    def test_manual_missing_or_invalid_requests_do_not_complete(self):
        for request in (None, '{broken', '[]', json.dumps({'reason': 'interrupted'})):
            self.assertEqual(self.decision(request, 21)[0], 'interrupted')

    def test_request_is_written_before_the_signal_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            def helper(command, **kwargs):
                self.assertEqual(json.loads((work / 'stop-request.json').read_text()),
                                 {'reason': 'capture_complete'})
                return subprocess.CompletedProcess(command, 0, 'MATCHED_TRACE_DEBUGGERS=1\n', '')
            with patch.object(trace.subprocess, 'run', side_effect=helper), \
                    contextlib.redirect_stdout(io.StringIO()):
                trace.interrupt_debugger(work, [], reason='capture_complete')

    def test_zero_matches_is_not_a_successful_signal_request(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trace.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, 'MATCHED_TRACE_DEBUGGERS=0\n', '')), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                trace.interrupt_debugger(Path(directory), [])
            evidence = json.loads((Path(directory) / 'interrupt-helper.jsonl').read_text())
            self.assertEqual(evidence['stdout'], 'MATCHED_TRACE_DEBUGGERS=0\n')
            self.assertEqual(evidence['returncode'], 0)

    def test_helper_failure_retains_unmodified_output_including_terminal_codes(self):
        output = '\x1b[?2004lMATCHED_TRACE_DEBUGGERS=1\r\n'
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trace.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, output, 'helper stderr\n')), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                trace.interrupt_debugger(Path(directory), [])
            evidence = json.loads((Path(directory) / 'interrupt-helper.jsonl').read_text())
            self.assertEqual(evidence['stdout'], output)
            self.assertEqual(evidence['stderr'], 'helper stderr\n')

    def test_helper_timeout_retains_partial_output(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trace.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                    ['helper'], 10, output=b'partial\n', stderr=b'error\n')):
            with self.assertRaises(subprocess.TimeoutExpired):
                trace.interrupt_debugger(Path(directory), [])
            evidence = json.loads((Path(directory) / 'interrupt-helper.jsonl').read_text())
            self.assertEqual(evidence['stdout'], 'partial\n')
            self.assertEqual(evidence['stderr'], 'error\n')


class DebuggerIdentityTests(unittest.TestCase):
    def exercise(self, *, reused=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'capture'
            work.mkdir()
            (work / 'identity.json').write_text(json.dumps({'executable': '/fixture/game'}))
            (work / 'debugger.json').write_text(json.dumps({'pid': 17, 'start_ticks': '7'}))
            for pid in (17, 23):
                proc = root / 'proc' / str(pid)
                proc.mkdir(parents=True)
                (proc / 'exe').symlink_to('/usr/bin/gdb')
                fields = ['S'] + ['0'] * 18 + ['8' if reused and pid == 17 else '7']
                (proc / 'stat').write_text(str(pid) + ' (gdb) ' + ' '.join(fields))
                (proc / 'cmdline').write_bytes(b'gdb\0--batch\0-se\0/fixture/game\0-ex\0' +
                                              ('source ' + str(work / 'probe.py')).encode() + b'\0')
            actual_path = Path
            def path(value):
                return root / 'proc' if str(value) == '/proc' else actual_path(value)
            sent = []
            output = io.StringIO()
            with patch('pathlib.Path', side_effect=path), \
                    patch.object(sys, 'argv', ['helper', str(work)]), \
                    patch.object(os, 'pidfd_open', side_effect=lambda pid: pid + 100), \
                    patch.object(os, 'close'), \
                    patch.object(signal, 'pidfd_send_signal', side_effect=lambda fd, sig: sent.append((fd, sig))), \
                    contextlib.redirect_stdout(output):
                if reused:
                    with self.assertRaisesRegex(RuntimeError, 'reused'):
                        exec(compile(trace.INTERRUPT_HELPER, '<helper>', 'exec'), {})
                else:
                    exec(compile(trace.INTERRUPT_HELPER, '<helper>', 'exec'), {})
            return sent, output.getvalue()

    def test_same_command_decoy_is_not_signalled(self):
        sent, output = self.exercise()
        self.assertEqual(sent, [(117, signal.SIGINT)])
        self.assertIn('MATCHED_TRACE_DEBUGGERS=1', output)
        self.assertNotIn('INTERRUPTED_TRACE_GDB=23', output)

    def test_reused_debugger_pid_sends_no_signal(self):
        self.assertEqual(self.exercise(reused=True)[0], [])


class WatchdogTests(unittest.TestCase):
    """Real subprocess waits and SIGINT, with GDB discovery replaced at its boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        (self.work / 'identity.json').write_text(json.dumps({'executable': sys.executable}))
        (self.work / 'probe.py').write_text('# protocol fixture only\n')
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        # Only our disposable protocol children, never a target emulator or shell.
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)

    def launch(self, *, armed=True, ignores=False, source=None):
        code = ('import signal, time, sys\n'
                'signal.signal(signal.SIGINT, ' + (
                    'signal.SIG_IGN' if ignores else 'lambda *_: sys.exit(0)') + ')\n'
                'print("PES_VIDEO_STAGE=fixture_ready", flush=True)\n')
        if armed:
            code += 'print("PES_VIDEO_ARMED=20_seconds", flush=True)\n'
        code += 'time.sleep(120)\n'
        destination = self.work / ('decoy.txt' if source else 'gdb.txt')
        with destination.open('w') as log:
            child = subprocess.Popen([sys.executable, '-c', code, '--batch',
                                      sys.executable, 'source ' + str(source or self.work / 'probe.py')],
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append(child)
        until = time.monotonic() + 3
        while 'fixture_ready' not in destination.read_text():
            if child.poll() is not None or time.monotonic() > until:
                self.fail('Protocol child did not become ready: ' + destination.read_text())
            time.sleep(0.01)
        return child

    def wait(self, child, *, helper_error=None, production=False, **kwargs):
        limits = dict(setup_seconds=0.2, capture_seconds=0.2, grace=0.1,
                      detach_seconds=0.2, poll_seconds=0.01)
        limits.update(kwargs)
        def interrupt(work, prefix, *, reason='interrupted'):
            if helper_error:
                raise RuntimeError(helper_error)
            child.send_signal(signal.SIGINT)
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(trace, 'interrupt_debugger', side_effect=interrupt):
            return trace.wait_for_debugger(child, self.work, [], **({} if production else limits))

    def test_setup_timeout_signals_selected_child_without_changing_session(self):
        child = self.launch(armed=False)
        decoy = self.launch(source=self.work / 'different-probe.py')
        group = os.getpgrp()
        outcome = self.wait(child)
        self.assertEqual(outcome['reason'], 'setup_timeout')
        self.assertTrue(outcome['debugger_exited'])
        self.assertEqual(outcome['returncode'], 0)
        self.assertIsNone(decoy.poll())
        self.assertEqual(os.getpgrp(), group)

    def test_scheduled_stop_and_cooperative_exit(self):
        outcome = self.wait(self.launch())
        self.assertEqual(outcome['reason'], 'capture_complete')
        self.assertTrue(outcome['debugger_exited'])
        self.assertEqual(outcome['errors'], [])

    def test_ignored_interrupt_returns_pending_within_bound(self):
        child = self.launch(ignores=True)
        start = time.monotonic()
        outcome = self.wait(child)
        self.assertFalse(outcome['debugger_exited'])
        self.assertIsNone(child.poll())
        self.assertLess(time.monotonic() - start, 3)

    def test_helper_failure_is_returned_for_archiving(self):
        child = self.launch()
        outcome = self.wait(child, helper_error='fixture failure')
        self.assertFalse(outcome['debugger_exited'])
        self.assertIn('fixture failure', outcome['errors'][0])

    def test_production_capture_deadline_with_real_subprocess(self):
        child = self.launch()
        start = time.monotonic()
        outcome = self.wait(child, production=True)
        elapsed = time.monotonic() - start
        self.assertEqual(outcome['reason'], 'capture_complete')
        self.assertTrue(outcome['debugger_exited'])
        self.assertEqual(outcome['errors'], [])
        self.assertGreaterEqual(elapsed, 20)
        self.assertLess(elapsed, 25)
        print(f'PROTOCOL_WATCHDOG_SECONDS={elapsed:.3f}; live GDB remains unverified')


if __name__ == '__main__':
    unittest.main()
