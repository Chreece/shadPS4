#!/usr/bin/env python3
"""Read-only 5-second-window analysis of existing GoW dense/sparse CPU-upload logs.

Never launches a game or changes source, executables, game files, settings,
GPU processes, Ollama, Sunshine, or SSH. Correlation is approximate because
GPU and Audio3D counters are logged from different threads.
"""
import collections
import json
import pathlib
import re
import statistics
import sys

FIELDS = ('calls', 'uploaded_bytes', 'dirty_ranges', 'scan_sum_us', 'all_sum_us', 'max_us')
SYNC = re.compile(
    r'GOW_BIG_BUFFER_SYNC\s+calls=(\d+)\s+size=(\d+)\s+written=(\w+)'
    r'\s+uploaded_bytes=(\d+)\s+dirty_ranges=(\d+)\s+scan_sum_us=(\d+)'
    r'\s+all_sum_us=(\d+)\s+max_us=(\d+)')
BIND = re.compile(
    r'GOW_BIG_BUFFER_BIND\s+.*?calls=(\d+)\s+requested=(\d+)'
    r'\s+clamped=(\d+)\s+written=(\w+)\s+tracked_write=(\d+)'
    r'\s+last_us=(\d+)\s+sum_us=(\d+)\s+max_us=(\d+)')
FRAME = re.compile(
    r'VIDEOOUT_FLIP_TIMING\s+window_ms=(\d+)\s+game_flips=(\d+)'
    r'\s+fps_x1000=(\d+)')
AUDIO = re.compile(
    r'AUDIO_PRODUCER_TIMING\s+port=126\s+.*?ready=(\d+)'
    r'\s+no_guest_buffer=(\d+)')


def parse(lines):
    previous = {'sync': {}, 'bind': {}}
    rows, events = [], collections.Counter()
    pending = collections.Counter()
    ready = missing = elapsed = 0
    for line in lines:
        if 'GOW_BIG_BUFFER_SYNC' in line and (match := SYNC.search(line)):
            keys = ('calls', 'size', 'written', 'uploaded_bytes',
                    'dirty_ranges', 'scan_sum_us', 'all_sum_us', 'max_us')
            values = dict(zip(keys, match.groups()))
            nums = {key: int(values[key]) for key in FIELDS}
            last = previous['sync']
            if last and nums['calls'] < last['calls']:
                events['sync_counter_reset_or_other_thread'] += 1
                last = {}
            for key in FIELDS[:-1]:
                delta = nums[key] - last.get(key, 0)
                if delta < 0:
                    events['sync_counter_regression'] += 1
                    delta = 0
                pending['s_' + key] += delta
            if nums['max_us'] > last.get('max_us', 0):
                pending['new_sync_peak_us'] = max(
                    pending['new_sync_peak_us'], nums['max_us'])
            previous['sync'] = nums
            events['sync_records'] += 1
        elif 'GOW_BIG_BUFFER_BIND' in line and (match := BIND.search(line)):
            (calls, requested, clamped, written,
             tracked, last_us, sum_us, max_us) = match.groups()
            nums = {'calls': int(calls), 'sum_us': int(sum_us), 'max_us': int(max_us)}
            last = previous['bind']
            if last and nums['calls'] < last['calls']:
                events['bind_counter_reset_or_other_thread'] += 1
                last = {}
            pending['b_calls'] += max(0, nums['calls'] - last.get('calls', 0))
            pending['b_sum_us'] += max(0, nums['sum_us'] - last.get('sum_us', 0))
            if nums['max_us'] > last.get('max_us', 0):
                pending['new_bind_peak_us'] = max(
                    pending['new_bind_peak_us'], nums['max_us'])
            previous['bind'] = nums
            events['bind_records'] += 1
        elif 'AUDIO_PRODUCER_TIMING port=126' in line and (match := AUDIO.search(line)):
            ready += int(match[1])
            missing += int(match[2])
            events['audio_windows'] += 1
        elif 'VIDEOOUT_FLIP_TIMING' in line and (match := FRAME.search(line)):
            window_ms, flips, fps = (int(value) for value in match.groups())
            if window_ms <= 0:
                continue
            elapsed += window_ms
            row = dict(pending)
            row.update(n=len(rows) + 1, t_s=round(elapsed / 1000, 1),
                       fps=fps / 1000, frames=flips,
                       audio=ready / (ready + missing) if ready + missing else None,
                       ready=ready, missing=missing)
            rows.append(row)
            pending = collections.Counter()
            ready = missing = 0
            events['frame_windows'] += 1
    return rows, events, previous


def fmt(value, precision=1):
    return 'NA' if value is None else f'{value:.{precision}f}'


def compare(rows, name):
    def ms(row, key):
        return row.get(key, 0) / 1000

    def mib(row, key):
        return row.get(key, 0) / 1048576

    healthy = [row for row in rows if row['audio'] is not None and row['audio'] >= .9]
    starved = [row for row in rows if row['audio'] is not None and row['audio'] <= .5]
    print(f'=== {name} WINDOWS={len(rows)} HEALTHY={len(healthy)} STARVED={len(starved)} ===')
    print('End(s)  FPS  AudioReady  SyncCalls  ScanCPUms  SyncCPUms  '
          'UploadMiB  DirtyRanges  BindCPUms  NewBindPeakms')
    for row in rows:
        print(f'{row["t_s"]:6.1f} {row["fps"]:5.1f} '
              f'{fmt(row["audio"] * 100 if row["audio"] is not None else None, 0):>10} '
              f'{row.get("s_calls", 0):10} {ms(row, "s_scan_sum_us"):10.1f} '
              f'{ms(row, "s_all_sum_us"):10.1f} {mib(row, "s_uploaded_bytes"):9.1f} '
              f'{row.get("s_dirty_ranges", 0):12} {ms(row, "b_sum_us"):9.1f} '
              f'{ms(row, "new_bind_peak_us"):13.1f}')
    for label, group in [('HEALTHY', healthy), ('STARVED', starved)]:
        if not group:
            continue
        print(f'{label}_MEDIAN_FPS={statistics.median(row["fps"] for row in group):.2f} '
              f'SYNC_MS_PER_WINDOW={statistics.median(ms(row, "s_all_sum_us") for row in group):.1f} '
              f'SCAN_MS_PER_WINDOW={statistics.median(ms(row, "s_scan_sum_us") for row in group):.1f} '
              f'UPLOAD_MIB_PER_WINDOW={statistics.median(mib(row, "s_uploaded_bytes") for row in group):.1f} '
              f'CALLS_PER_WINDOW={statistics.median(row.get("s_calls", 0) for row in group):.0f}')
    peaks = sorted(rows, key=lambda row: row.get('new_bind_peak_us', 0), reverse=True)[:3]
    print('NEW_BIND_PEAKS=' + json.dumps([
        {'t_s': row['t_s'], 'peak_ms': round(ms(row, 'new_bind_peak_us'), 2),
         'fps': row['fps'], 'audio': row['audio']} for row in peaks]))
    print('WINDOW_ALIGNMENT=APPROXIMATE_LINE_ORDER_NO_CAUSALITY_CLAIM')


def self_test():
    lines = [
        'GOW_BIG_BUFFER_SYNC calls=256 size=100000000 written=false uploaded_bytes=10000 dirty_ranges=4 scan_sum_us=1000 all_sum_us=3000 max_us=25',
        'GOW_BIG_BUFFER_BIND shader=0xdfd7cccc calls=256 requested=700 clamped=500 written=false tracked_write=0 last_us=10 sum_us=5000 max_us=100',
        'AUDIO_PRODUCER_TIMING port=126 ready=220 no_guest_buffer=10',
        'VIDEOOUT_FLIP_TIMING window_ms=5000 game_flips=150 fps_x1000=30000',
        'GOW_BIG_BUFFER_SYNC calls=512 size=100000000 written=false uploaded_bytes=20000 dirty_ranges=10 scan_sum_us=2200 all_sum_us=6200 max_us=25',
        'GOW_BIG_BUFFER_BIND shader=0xdfd7cccc calls=512 requested=700 clamped=500 written=false tracked_write=0 last_us=20 sum_us=6600 max_us=200',
        'AUDIO_PRODUCER_TIMING port=126 ready=80 no_guest_buffer=150',
        'VIDEOOUT_FLIP_TIMING window_ms=5000 game_flips=80 fps_x1000=16000',
    ]
    rows, events, previous = parse(lines)
    assert len(rows) == 2 and events['sync_records'] == 2
    assert rows[0]['s_calls'] == rows[1]['s_calls'] == 256
    assert rows[0]['s_all_sum_us'] == 3000 and rows[1]['s_all_sum_us'] == 3200
    assert rows[1]['s_dirty_ranges'] == 6 and rows[1]['s_uploaded_bytes'] == 10000
    assert round(rows[0]['audio'], 4) == round(220 / 230, 4)
    assert rows[1]['new_bind_peak_us'] == 200
    print('SYNTHETIC_PARSER_REGRESSION=PASS cases=6')


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--self-test':
        self_test()
        return
    if len(sys.argv) != 2:
        raise SystemExit('USAGE: script.py BASE_AB_DIRECTORY | --self-test')
    base = pathlib.Path(sys.argv[1])
    for mode in ('A_DENSE_CPU_UPLOAD', 'B_SPARSE_CPU_UPLOAD'):
        path = base / mode / 'full-console.log'
        if not path.is_file():
            raise SystemExit(f'MISSING_CAPTURE={path}')
        with path.open(errors='replace') as file:
            rows, events, previous = parse(file)
        print(f'MODE={mode} EVENTS={dict(events)} FINAL={previous}')
        compare(rows, mode)
        if len(rows) < 12:
            print('DATA_INCOMPLETE_FEWER_THAN_12_FRAME_WINDOWS=YES')
    print('READ_ONLY=YES OLLAMA_SUNSHINE_SSH_GAME_UNTOUCHED=YES')


if __name__ == '__main__':
    main()
