# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""ABI decoding and validation-gate checks; live GDB still requires the Docker test."""

import ast
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import pes_frame_profile as frames
import run_pes_frame_test as runner
import run_pes_startup_test as startup
import trace_video_progress as trace


class FrameFieldsTests(unittest.TestCase):
    def namespace(self, regions=None, registers=None):
        regions = regions or {}
        class Breakpoint:
            def __init__(self, *args, **kwargs):
                self.valid = True
            def is_valid(self):
                return self.valid
            def delete(self):
                self.valid = False
        def memory(address, size):
            for start, data in regions.items():
                offset = address - start
                if 0 <= offset and offset + size <= len(data):
                    return data[offset:offset + size]
            raise AssertionError(f'Unexpected memory read: {address:#x}, {size}')
        scope = {'memory': Mock(side_effect=memory), 'register': (registers or {}).__getitem__,
                 'number': lambda data, offset, size=8: int.from_bytes(data[offset:offset + size], 'little'),
                 'gdb': Mock(Breakpoint=Breakpoint), 'pending': [],
                 'result': {'apis': {api: {'out_of_scope': 0} for api in frames.APIS}}}
        exec(compile(frames.SUPPORT, '<frame-profile>', 'exec'), scope)
        return scope

    def test_native_return_slots_preserve_tail_calls_and_reject_wrong_stack_or_thread(self):
        registers = {'rsp': 0x3000}
        scope = self.namespace({0x3000: struct.pack('<Q', 0x4000)}, registers)
        thread = Mock(global_num=7)
        scope['gdb'].selected_thread.return_value = thread
        outer = {'api': 'sceVideoOutSubmitFlip', 'thread': 7}
        inner = {'api': 'sceVideoOutSubmitEopFlip', 'thread': 7}
        outer_probe = scope['Return'](outer)
        inner_probe = scope['Return'](inner)
        registers['rsp'] = 0x2008
        self.assertFalse(outer_probe.stop())
        registers['rsp'] = 0x3008
        thread.global_num = 8
        self.assertFalse(outer_probe.stop())
        thread.global_num = 7
        self.assertTrue(outer_probe.stop())
        self.assertTrue(inner_probe.stop())
        self.assertEqual(scope['pending'], [('return', outer), ('return', inner)])
        scope['consume_return'](outer)
        scope['consume_return'](inner)
        self.assertFalse(outer_probe.is_valid())
        self.assertFalse(inner_probe.is_valid())
        self.assertEqual(scope['frame_returns'], {})

    def test_both_submit_flip_abis_read_stack_arguments_and_buffer_arrays(self):
        for suffix, prefix in (('', []), ('ForWorkload', [99])):
            with self.subTest(suffix=suffix):
                args = prefix + [2, 0x1000, 0x2000, 0, 0, 7, 0xffffffff, 1, 0x1122334455667788]
                registers = dict(zip(('rdi', 'rsi', 'rdx', 'rcx', 'r8', 'r9'), args[:6]), rsp=0x3000)
                scope = self.namespace({0x1000: struct.pack('<QQ', 0x12345678, 0x23456789),
                                        0x2000: struct.pack('<II', 64, 128),
                                        0x3008: struct.pack('<' + 'Q' * (len(args) - 6), *args[6:])}, registers)
                api = 'sceGnmSubmitAndFlipCommandBuffers' + suffix
                observed = scope['capture_args'](api)
                self.assertEqual(observed, args)
                values = scope['fields_before'](api, observed)
                self.assertEqual(values, {'buffer_count': 2, 'dcb_addresses': ['0x12345678', '0x23456789'],
                                         'dcb_bytes': [64, 128], 'handle': 7, 'buffer_index': -1,
                                         'flip_mode': 1, 'flip_arg': '0x1122334455667788'})

    def test_flip_status_signed_fields_and_failed_call_does_not_read_output(self):
        status = struct.pack('<QQQqQQiiiI', 123, 0, 0, 42, 0, 0, 2, 3, -1, 0)
        scope = self.namespace({0x1000: status})
        values = scope['fields_after']('sceVideoOutGetFlipStatus', [7, 0x1000], 0)
        self.assertEqual(values, {'flip_count': 123, 'flip_arg': '0x2a', 'gc_queue_num': 2,
                                 'flip_pending_num': 3, 'current_buffer': -1})
        scope['memory'].reset_mock()
        self.assertEqual(scope['fields_after']('sceVideoOutGetFlipStatus', [7, 0], 0x80290001), {})
        scope['memory'].assert_not_called()

    def test_guest_values_require_known_pc_and_matching_code_signature(self):
        scope = self.namespace()
        self.assertEqual(scope['completion_snapshot'](0x2e81910, 1)['status'],
                         'outside_known_completion_check')
        scope['memory'].assert_not_called()
        scope = self.namespace({0x2e81997: b'\0' * 10})
        self.assertEqual(scope['completion_snapshot'](0x2e8192b, 1)['status'], 'guest_signature_mismatch')
        self.assertEqual(scope['memory'].call_count, 1)

    def test_changing_completion_values_are_preserved_without_claiming_a_stall(self):
        key = 0x166999
        slot = 0x10000 + 24 * (key & 1023)
        scope = self.namespace({0x2e81997: bytes.fromhex('48 8b 48 08 48 8b 09 48 3b 08'),
                                0x5fff258: struct.pack('<Q', 0x10000),
                                slot: struct.pack('<QQQ', key, 0x20000, 0),
                                0x20000: struct.pack('<Q', 0)})
        values = scope['completion_snapshot'](0x2e8192b, key)
        self.assertEqual(values['key'], '0x166999')
        self.assertEqual(values['observed'], '0x0')
        self.assertTrue(values['slot_matches_key'])
        self.assertFalse(values['equal_at_sample'])
        self.assertNotIn('stalled', values)


class FrameGateTests(unittest.TestCase):
    def test_tail_called_functions_keep_both_return_callbacks_at_same_stop(self):
        class Breakpoint:
            def __init__(self, *args, **kwargs):
                pass
        scope = {'gdb': Mock(FinishBreakpoint=Breakpoint), 'pending': []}
        definition = next(node for node in ast.parse(trace.PROBE).body
                          if isinstance(node, ast.ClassDef) and node.name == 'Return')
        exec(compile(ast.Module(body=[definition], type_ignores=[]), '<return-hook>', 'exec'), scope)
        outer, inner = {'api': 'wrapper'}, {'api': 'tail_callee'}
        scope['Return'](outer).stop()
        scope['Return'](inner).stop()
        self.assertEqual(scope['pending'], [('return', outer), ('return', inner)])

    def test_failed_docker_validation_never_launches_or_attaches(self):
        with patch.object(runner.validate, 'launch', side_effect=RuntimeError('fixture failed')), \
                patch.object(runner.startup, 'run') as game:
            with self.assertRaisesRegex(RuntimeError, 'fixture failed'):
                runner.run_validated(Path('/unused'))
            game.assert_not_called()

    def test_successful_gate_selects_frames_and_reuses_existing_game(self):
        result = {'frames_capture_passed': True, 'settings_unchanged': True, 'launcher_unchanged': True}
        with patch.object(runner.validate, 'launch') as validation, \
                patch.object(runner.startup, 'run', return_value=result) as game, \
                contextlib.redirect_stdout(io.StringIO()):
            runner.run_validated(Path('/test-home'))
        validation.assert_called_once_with(profile='frames')
        game.assert_called_once_with(Path('/test-home'), profile='frames', reuse_existing=True)

    def test_video_result_cannot_pass_as_frame_capture(self):
        payload = {'status': 'complete', 'detached': True, 'seconds': 20.1,
                   'stop_reason': 'capture_complete', 'errors': [], 'records': [],
                   'apis': {api: {'calls': 0} for api in trace.VIDEO_APIS}}
        outcome = {'reason': 'capture_complete', 'errors': [], 'returncode': 0,
                   'debugger_exited': True, 'armed': True, 'capture_and_cleanup_seconds': 20.2}
        target = {'status': 'observed', 'tracer_pid': 0, 'state': 'S'}
        failed = trace.build_report('PES_VIDEO_JSON=' + json.dumps(payload), outcome, target, profile='frames')
        self.assertFalse(trace.report_passed(failed))
        payload['apis'] = {api: {'calls': 0} for api in frames.APIS}
        passed = trace.build_report('PES_VIDEO_JSON=' + json.dumps(payload), outcome, target, profile='frames')
        self.assertTrue(trace.report_passed(passed))
        payload['records'] = [{'event': 'enter', 'read_error': 'unreadable stack'}]
        failed = trace.build_report('PES_VIDEO_JSON=' + json.dumps(payload), outcome, target, profile='frames')
        self.assertFalse(trace.report_passed(failed))

    def test_generated_frame_probe_contains_the_profile_and_remains_valid_python(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'probe.py'
            trace.write_probe({'pid': 42}, path, profile='frames')
            code = path.read_text()
            compile(code, '<generated-frame-probe>', 'exec')
            self.assertIn('VideoOutDriver::Flip', code)

    def test_existing_pes_does_not_use_a_desktop_or_launch_a_second_process(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = home / 'wrapper'
            wrapper.write_text('original')
            identity = {'pid': 42, 'start_ticks': '7', 'executable': '/unused', 'sha256': 'fixture'}
            # Stop at the shared debugger step: this tests reuse branching, not GDB.
            with patch.object(startup, 'running_emulators', return_value=[42]), \
                    patch.object(trace, 'find_process', return_value=identity), \
                    patch.object(startup, 'selected_launch', return_value=(wrapper, startup.checksum(wrapper))), \
                    patch.object(startup, 'settings', return_value={'Audio': '7.1'}), \
                    patch.object(startup, 'debugger_prefix', return_value=[]), \
                    patch.object(startup, 'desktop_environment') as desktop, \
                    patch.object(startup, 'launch_game') as launch, \
                    patch.object(trace, 'debugger_command', side_effect=RuntimeError('stop before attach')), \
                    patch.object(trace, 'inspect_target', return_value={'status': 'exited'}), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = startup.run(home, profile='frames', reuse_existing=True)
            desktop.assert_not_called()
            launch.assert_not_called()
            self.assertTrue(result['reused_existing'])
            self.assertTrue(result['settings_unchanged'])
            self.assertEqual(result['errors'], ['RuntimeError: stop before attach'])


if __name__ == '__main__':
    unittest.main()
