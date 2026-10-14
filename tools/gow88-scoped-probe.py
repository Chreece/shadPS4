#!/usr/bin/env python3
"""GoW88 presenter probe with scoped matching for every insertion site.
Original source is never modified; all eight probe sections are planned and
validated before generating an exact contextual Git patch for a private overlay.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import json

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
BASE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '3136a7c46f044e9cd4d6fb9a6af4430efd6b76ea/tools/gow88-frame-provenance.py')
BASE_SHA = '70bb5e03d02412f91dd5d3a37ffd21a63779130703348394d31467ba737809a2'
PATCH_SHA = '713a042d4a6257f6f4165195c898d7a1da79e5efb3913407750086ca7123ba69'
PRESENTER = 'src/video_core/renderer_vulkan/vk_presenter.cpp'
GUEST_LINE = '    const u32 capture_game_only_count = VideoCore::ConsumeGameOnlyScreenshotRequests();\n'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_base():
    scripts = ROOT / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    candidates = [scripts/'gow88.py', scripts/'gow88-probe-base.py']
    path = next((p for p in candidates if p.is_file() and not p.is_symlink()
                 and sha(p.read_bytes()) == BASE_SHA), None)
    if path is None:
        fd, name = tempfile.mkstemp(prefix='.gow88c-base-', dir=scripts)
        os.close(fd)
        temporary = Path(name)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', BASE_URL, '-o', str(temporary)], check=True, timeout=380)
            if sha(temporary.read_bytes()) != BASE_SHA:
                raise RuntimeError('Original Phase88 runner checksum mismatch')
            path = candidates[1]
            if path.is_symlink():
                raise RuntimeError('Refusing a symlinked probe runner')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    module = types.ModuleType('gow88_source_matched_base')
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    if module.PRESENTER != PRESENTER or module.PATCH_SHA != PATCH_SHA:
        raise RuntimeError('Unexpected probe or source target')
    return module


def parse_patch(data: bytes):
    if sha(data) != PATCH_SHA:
        raise RuntimeError('Original frame-probe patch checksum mismatch')
    lines = data.decode('utf-8').splitlines(keepends=True)
    if lines[:2] != ['--- a/'+PRESENTER+'\n', '+++ b/'+PRESENTER+'\n']:
        raise RuntimeError('Unexpected patch target')
    hunks = []
    i = 2
    while i < len(lines):
        match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', lines[i])
        if not match:
            raise RuntimeError('Unrecognized patch header')
        i += 1
        rows = []
        while i < len(lines) and not lines[i].startswith('@@ '):
            if lines[i][:1] not in (' ', '+', '-'):
                raise RuntimeError('Unrecognized patch row')
            rows.append(lines[i]); i += 1
        before = ''.join(row[1:] for row in rows if row[0] != '+')
        after = ''.join(row[1:] for row in rows if row[0] != '-')
        if len(before.splitlines()) != int(match[2]) or len(after.splitlines()) != int(match[4]):
            raise RuntimeError('Corrupt hunk length')
        hunks.append({'line': int(match[1]), 'old': before, 'new': after,
                      'removed': ''.join(r[1:] for r in rows if r[0] == '-'),
                      'added': ''.join(r[1:] for r in rows if r[0] == '+')})
    if len(hunks) != 8 or sum(h['removed'] == GUEST_LINE for h in hunks) != 1:
        raise RuntimeError('Unexpected probe patch structure')
    return hunks


def mask_cpp(text: str) -> str:
    # Ignore strings/comments when locating the actual C++ method and brace span.
    token = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<delim>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=delim)"|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return token.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


# Anchors below are C++ statements, scoped to their owning function. None uses a
# historical source line number or surrounding log/barrier/comment text.
TOKEN = re.compile(r'[A-Za-z_]\w*|\d+|::|->|\S')


def tokens(text):
    return [(m[0], m.start(), m.end()) for m in TOKEN.finditer(mask_cpp(text))]


def occurrences(items, sequence, lo=0, hi=None):
    want = [x[0] for x in tokens(sequence)]
    stop = len(items) if hi is None else hi
    return [i for i in range(lo, stop-len(want)+1)
            if [x[0] for x in items[i:i+len(want)]] == want]


def balanced(items, opening):
    pairs = {'(': ')', '{': '}', '[': ']'}
    stack = []
    for i in range(opening, len(items)):
        value = items[i][0]
        if value in pairs:
            stack.append(pairs[value])
        elif value in pairs.values():
            if not stack or value != stack.pop():
                raise RuntimeError('Unbalanced C++ delimiters in probe target')
            if not stack:
                return i
    raise RuntimeError('Incomplete C++ scope in probe target')


def function_scope(items, name):
    found = []
    length = len(tokens(name+'('))
    for index in occurrences(items, name+'('):
        closing = balanced(items, index+length-1)
        if closing+1 < len(items) and items[closing+1][0] == '{':
            opening = closing+1
            found.append((opening, balanced(items, opening)))
    if len(found) != 1:
        raise RuntimeError('Missing or ambiguous function definition: '+name)
    return found[0]


def scoped_statement(items, scope, statement):
    indices = occurrences(items, statement, scope[0]+1, scope[1])
    if len(indices) != 1:
        raise RuntimeError('Missing or ambiguous executable statement: '+statement)
    n = len(tokens(statement))
    return indices[0], indices[0]+n-1


def addition_runs(patch):
    groups = []
    for line in patch.decode('utf-8').splitlines(keepends=True)[2:]:
        if line.startswith('@@ '):
            groups.append([])
            current = None
        elif line.startswith('+'):
            if current is None:
                current = len(groups[-1]); groups[-1].append('')
            groups[-1][current] += line[1:]
        else:
            current = None
    if list(map(len, groups)) != [1, 1, 1, 2, 1, 1, 1, 1]:
        raise RuntimeError('Unexpected insertion blocks in pinned probe')
    return groups


def adapt_source(source: bytes, patch: bytes):
    hunks = parse_patch(patch)  # Integrity pin and exact eight-hunk structure.
    blocks = addition_runs(patch)
    if len(source) > 2_000_000 or b'\x00' in source or b'\r' in source:
        raise RuntimeError('Unexpected presenter source encoding/size')
    text = source.decode('utf-8')
    if 'GOW88_FRAME_PROVENANCE' in text or re.search(r'\bFrameProbe\w*\b', mask_cpp(text)):
        raise RuntimeError('Probe already present or helper name collision')
    items = tokens(text)
    scopes = {name: function_scope(items, name) for name in (
        'BuildScreenshotPaths', 'SavePendingScreenshot',
        'Presenter::PrepareFrame', 'Presenter::Present')}
    edits, audit = [], []
    # These parameter names are referenced by the pinned logging expressions.
    for name, required in (
        ('Presenter::PrepareFrame', ('VAddr cpu_address',)),
        ('Presenter::Present', ('Frame* frame', 'bool is_reusing_frame', 'bool is_game_frame'))):
        prefix = occurrences(items, name+'(')
        if len(prefix) != 1:
            raise RuntimeError('Ambiguous method signature: '+name)
        for signature in required:
            if not occurrences(items, signature, prefix[0], scopes[name][0]):
                raise RuntimeError('Unexpected method parameters: '+name+' -> '+signature)

    def at_statement(name, statement):
        span = scoped_statement(items, scopes[name], statement)
        # Do not place unconditional probe logic inside an unknown #if branch.
        region = mask_cpp(text)[items[scopes[name][0]][2]:items[span[0]][1]]
        depth = 0
        for directive in re.findall(r'^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b', region, re.M):
            if directive in ('if', 'ifdef', 'ifndef'):
                depth += 1
            elif directive == 'endif':
                depth -= 1
        if depth != 0:
            raise RuntimeError('Probe target is conditional preprocessor code: '+statement)
        return span

    def line_start(index):
        position = items[index][1]
        start = text.rfind('\n', 0, position)+1
        if text[start:position].strip():
            raise RuntimeError('Probe statement shares a line with other executable code')
        return start, text[start:position]

    def reindent(code, indent):
        import textwrap
        return textwrap.indent(textwrap.dedent(code), indent)

    def add(start, end, value, label, group):
        edits.append((start, end, value, label))
        audit.append({'section': group, 'target': label,
                      'source_line': text.count('\n', 0, start)+1,
                      'original_hunk_line': hunks[group]['line'],
                      'match': 'unique_scoped_tokens'})

    def insert_before(index, value, label, group):
        start, indent = line_start(index)
        add(start, start, reindent(value, indent), label, group)

    def insert_after(index, value, label, group):
        # Inserting at the semicolon retains any following local barrier/log/comment.
        first, _ = index
        last = index[1]
        _, indent = line_start(first)
        pos = items[last][2]
        add(pos, pos, '\n'+reindent(value, indent).rstrip('\n'), label, group)

    # Header additions are idempotent; unrelated includes retain their exact bytes.
    needed = [name for name in ('cstdlib', 'mutex') if not re.search(
        r'^\s*#\s*include\s*<\s*'+name+r'\s*>', text, re.M)]
    if needed:
        matches = list(re.finditer(r'^\s*#\s*include\s*<\s*atomic\s*>[^\n]*\n', text, re.M))
        if len(matches) != 1:
            raise RuntimeError('Cannot uniquely locate the atomic include')
        pos = matches[0].end()
        add(pos, pos, ''.join('#include <'+n+'>\n' for n in needed), 'header additions', 0)
    else:
        audit.append({'section': 0, 'match': 'headers_already_present'})

    structs = occurrences(items, 'struct ScreenshotReadback {')
    enums = occurrences(items, 'enum class ScreenshotKind : u8 {')
    if len(structs) != 1 or len(enums) != 1 or enums[0] >= structs[0]:
        raise RuntimeError('Screenshot kind/readback types do not match the probe contract')
    insert_before(structs[0], blocks[1][0], 'screenshot helper definitions', 1)

    name = 'BuildScreenshotPaths'
    stmt = 'const auto& screenshots_dir = Common::FS::GetUserPath(Common::FS::PathType::ScreenshotsDir);'
    paths_anchor = at_statement(name, stmt)
    first,last = scopes[name]
    # Early count==0 return must stay before our alternative output path.
    required = ('static std::atomic<u64> screenshot_sequence{0};',
                'std::vector<std::filesystem::path> paths{};', 'if (count == 0) { return paths; }')
    for code in required:
        r = at_statement(name, code)
        if r[1] >= paths_anchor[0]:
            raise RuntimeError('Screenshot path setup is in an unexpected order')
    insert_before(paths_anchor[0], blocks[2][0], name+' output directory', 2)

    name = 'SavePendingScreenshot'
    empty = at_statement(name, 'if (readback.paths.empty()) { return; }')
    rgba = at_statement(name, 'std::vector<u8> rgba;')
    converted = at_statement(name, 'if (!ConvertReadbackToRgba8(readback, rgba)) { return; }')
    primary = at_statement(name, 'const auto& primary_path = readback.paths.front();')
    if not empty[1] < rgba[0] < converted[0] < converted[1] < primary[0]:
        raise RuntimeError('Readback validation/conversion order differs from probe contract')
    insert_before(rgba[0], blocks[3][0], name+' host invalidation', 3)
    insert_before(primary[0], blocks[3][1], name+' pixel statistics', 3)

    # These declarations/calls must remain in the expected methods, even if the
    # source has additional diagnostics, different formatting, or local barriers.
    for name, request, index, buffer_call, barrier_section in (
        ('Presenter::PrepareFrame', 'const u32 capture_game_only_count = VideoCore::ConsumeGameOnlyScreenshotRequests();', 4,
         'runtime.DownloadImage(&image, &readback.buffer, std::span{&copy_region, 1});', 5),
        ('Presenter::Present', 'const u32 capture_with_overlays_count = VideoCore::ConsumeWithOverlaysScreenshotRequests();', 6,
         'CopyImageToReadback(cmdbuf, swapchain_image, vk::ImageLayout::eTransferSrcOptimal, readback);', 7)):
        request_span = at_statement(name, request)
        transfer = at_statement(name, buffer_call)
        if request_span[1] >= transfer[0]:
            raise RuntimeError('Readback copy precedes the screenshot request')
        start, indent = line_start(request_span[0])
        finish = items[request_span[1]][2]
        # Only replace this validated declaration; all surrounding code is retained.
        code = blocks[index][0]
        variable = 'gow88_guest_probe' if index == 4 else 'gow88_display_probe'
        if occurrences(items, variable):
            raise RuntimeError('Local probe variable name collision')
        code = re.sub(r'\bprobe\b', variable, code)
        add(start, finish, reindent(code, indent).rstrip('\n'), name+' capture request', index)
        barrier = re.sub(r'\bhost_read\b', 'gow88_host_read', blocks[barrier_section][0])
        insert_after(transfer, barrier, name+' completed copy visibility', barrier_section)

    # Verify names and preparation used by the logging expressions, not adjacent lines.
    guest = scopes['Presenter::PrepareFrame']
    guest_first = at_statement('Presenter::PrepareFrame',
        'const u32 capture_game_only_count = VideoCore::ConsumeGameOnlyScreenshotRequests();')[0]
    for declaration in ('const auto cmdbuf = draw_scheduler.CommandBuffer();',
                        'VideoCore::ImageViewInfo view_info{};',
                        'const vk::Extent2D image_size = {image.info.size.width, image.info.size.height};'):
        loc = at_statement('Presenter::PrepareFrame', declaration)
        if loc[1] >= guest_first:
            raise RuntimeError('Guest framebuffer variables are initialized too late')
    present_first = at_statement('Presenter::Present',
        'const u32 capture_with_overlays_count = VideoCore::ConsumeWithOverlaysScreenshotRequests();')[0]
    for declaration in ('auto& scheduler = present_scheduler;',
                        'const auto cmdbuf = scheduler.CommandBuffer();'):
        loc = at_statement('Presenter::Present', declaration)
        if loc[1] >= present_first:
            raise RuntimeError('Display scheduler variables are initialized too late')

    ordered = sorted(edits)
    previous_end = -1
    for a,b,new,label in ordered:
        if a < previous_end:
            raise RuntimeError('Overlapping source edits: '+label)
        previous_end = b
    result, cursor, reverse = '', 0, []
    for a,b,new,label in ordered:
        result += text[cursor:a]
        pos = len(result)
        result += new
        reverse.append((pos, pos+len(new), text[a:b], label))
        cursor = b
    result += text[cursor:]
    restored = result
    for a,b,old,label in reversed(reverse):
        restored = restored[:a]+old+restored[b:]
    if restored != text:
        raise RuntimeError('Unrelated original source bytes were changed')
    for marker in ('GOW88_FRAME_PROVENANCE', 'FrameProbeTick(false)', 'FrameProbeTick(true)'):
        if result.count(marker) != 1:
            raise RuntimeError('Probe structure check failed: '+marker)
    # Validate resulting braces and method structure as well as the original.
    output_tokens = tokens(result)
    for name in scopes:
        function_scope(output_tokens, name)
    diff = ''.join(difflib.unified_diff(text.splitlines(keepends=True),
        result.splitlines(keepends=True), fromfile='a/'+PRESENTER, tofile='b/'+PRESENTER, n=3)).encode()
    return result.encode(), diff, {'input_sha256': sha(source), 'output_sha256': sha(result.encode()),
        'reverse_preservation_verified': True, 'hunks': audit, 'sections_validated': 8,
        'matching_policy': 'all eight sections use scoped executable anchors; no historical context matching'}


class SourceMatchedHelper:
    """Delegate the unchanged build helper; adapt only the pinned frame probe."""
    def __init__(self, helper):
        self.helper = helper
        self.plans = {}

    def __getattr__(self, name):
        return getattr(self.helper, name)

    def run(self, command, log, cwd=None, timeout=900):
        args = list(map(str, command))
        if args[:2] != ['git', 'apply']:
            return self.helper.run(command, log, cwd=cwd, timeout=timeout)
        if args[2:-1] not in ([], ['--check']):
            raise RuntimeError('Unexpected frame-probe git flags')
        patch = Path(args[-1]).read_bytes()
        if sha(patch) != PATCH_SHA:
            raise RuntimeError('Unexpected patch in source-matched build')
        overlay = Path(cwd).resolve()
        target = overlay/PRESENTER
        if not target.is_file() or target.is_symlink():
            raise RuntimeError('Private presenter source missing or symlinked')
        out = Path(log).parent
        original = target.read_bytes()
        key = str(overlay)
        if '--check' in args:
            if len(original) <= 2_000_000:
                (out/'presenter-before.cpp').write_bytes(original)
            try:
                after, diff, proof = adapt_source(original, patch)
            except Exception as exc:
                (out/'probe-source-match.json').write_text(json.dumps({
                    'status': 'FAILED', 'error': str(exc), 'source_sha256': sha(original)}, indent=2)+'\n')
                raise
            matched = out/'source-matched-frame-probe.patch'
            matched.write_bytes(diff)
            (out/'presenter-after.cpp').write_bytes(after)
            (out/'probe-source-match.json').write_text(json.dumps(proof, indent=2)+'\n')
            self.plans[key] = (sha(original), sha(after), matched)
        if key not in self.plans:
            raise RuntimeError('Cannot apply without an exact-source dry run')
        before_hash, after_hash, matched = self.plans[key]
        if sha(original) != before_hash:
            raise RuntimeError('Private source changed since dry-run check')
        result = self.helper.run(['git', 'apply', *args[2:-1], str(matched)],
                                  log, cwd=cwd, timeout=timeout)
        expected = before_hash if '--check' in args else after_hash
        if sha(target.read_bytes()) != expected:
            raise RuntimeError('Source hash differs from checked result')
        return result


def self_test(base):
    """Validate the actual source and record it even if a compatibility guard fails."""
    import datetime
    import tarfile
    report_dir = base.LOGS / ('gow88-source-check-'+
        datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    report_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'status': 'STARTED', 'full_emulator_compiled': False,
              'scope': 'real source compatibility plus local helper regression tests'}
    original = None
    try:
        original_path = base.BASE84/'source'/PRESENTER
        if not original_path.is_file() or original_path.is_symlink():
            raise RuntimeError('Retained presenter missing or symlinked')
        original = original_path.read_bytes()
        if len(original) > 2_000_000:
            raise RuntimeError('Unexpected presenter source size')
        (report_dir/'presenter-before.cpp').write_bytes(original)
        patch_file = base.get_file(base.PATCH_URL, report_dir/'original-probe.patch', PATCH_SHA)
        patch = patch_file.read_bytes()
        after, diff, actual = adapt_source(original, patch)
        (report_dir/'presenter-after.cpp').write_bytes(after)
        (report_dir/'exact-source.patch').write_bytes(diff)
        report['actual_source'] = actual
        # Compile/link helpers and binary-priority collection are unchanged.
        base.self_test()
        tests = ROOT/'tests'; tests.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='gow88c-check-', dir=tests) as tmp:
            folder = Path(tmp)
            def verify_git(value, generated, contextual):
                overlay = folder/'overlay'
                target = overlay/PRESENTER; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value)
                p = folder/'contextual.patch'; p.write_bytes(contextual)
                for flags in (['--check'], []):
                    subprocess.run(['git', 'apply', *flags, str(p)], cwd=overlay, check=True,
                        capture_output=True, timeout=20)
                if target.read_bytes() != generated:
                    raise RuntimeError('Generated Git patch differs from the planned source')
            verify_git(original, after, diff)
            # Exercise nearby changes at ALL readback insertion sites, not just
            # the first failing line. Added bytes must survive the adaptation.
            variant = original.decode('utf-8')
            locations = tokens(variant)
            edits = []
            for func, call in (
                ('Presenter::PrepareFrame', 'runtime.DownloadImage(&image, &readback.buffer, std::span{&copy_region, 1});'),
                ('Presenter::Present', 'CopyImageToReadback(cmdbuf, swapchain_image, vk::ImageLayout::eTransferSrcOptimal, readback);')):
                span = scoped_statement(locations, function_scope(locations, func), call)
                edits.append((locations[span[1]][2], '\n        /* PRESERVE_GOW88C_LOCAL_CODE */'))
            for pos, added in sorted(edits, reverse=True):
                variant = variant[:pos]+added+variant[pos:]
            next_source, next_patch, proof = adapt_source(variant.encode(), patch)
            if next_source.count(b'PRESERVE_GOW88C_LOCAL_CODE') != 2:
                raise RuntimeError('Nearby source modifications were not preserved')
            verify_git(variant.encode(), next_source, next_patch)
            # The matcher must still reject a changed operation, duplicates and
            # reapplication; never force a patch across an unknown layout.
            original_tokens = tokens(original.decode('utf-8'))
            span = scoped_statement(original_tokens, function_scope(original_tokens, 'Presenter::PrepareFrame'),
                'runtime.DownloadImage(&image, &readback.buffer, std::span{&copy_region, 1});')
            a,b = original_tokens[span[0]][1], original_tokens[span[1]][2]
            value = original.decode('utf-8')
            bad = [value[:a]+value[b:], value[:b]+'\n        '+value[a:b]+value[b:], after.decode('utf-8')]
            for invalid in bad:
                try:
                    adapt_source(invalid.encode(), patch)
                except RuntimeError:
                    continue
                raise RuntimeError('Ambiguous/missing/already-patched source was accepted')
        if original_path.read_bytes() != original:
            raise RuntimeError('Original presenter changed during the source check')
        report['status'] = 'PASS'
        print('GOW88_SCOPED_SELF_TEST=PASS all 8 probe sections, actual source Git check/apply, '
              'reverse byte preservation, both copy-site regressions and rejection guards', flush=True)
    except Exception as exc:
        report.update(status='FAILED', error=str(exc))
        raise
    finally:
        (report_dir/'source-check.json').write_text(json.dumps(report, indent=2)+'\n')
        archive = report_dir.with_name(report_dir.name+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            for p in sorted(report_dir.iterdir()):
                if p.is_file() and not p.is_symlink():
                    tar.add(p, arcname=report_dir.name+'/'+p.name)
        print('GOW88_SOURCE_CHECK_REPORT='+str(archive), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    base = load_base()
    if args.self_test:
        self_test(base)
        return 0
    factory = base.helper_module
    base.helper_module = lambda: SourceMatchedHelper(factory())
    # Same Phase87 pins, 11 reused objects, overlay header checks, DMA transaction,
    # binary-first evidence, independent runtime, and bounded framebuffer capture.
    return base.main()


if __name__ == '__main__':
    raise SystemExit(main())
