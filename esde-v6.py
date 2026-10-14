#!/usr/bin/env python3
"""shadPS4 ES-DE v6 installer: resolve reviewed GoW cache-version merge only.

Use official upstream main; never mix in newly added diagnostics from floating
fork branches. The v1 source comes from a single immutable GitHub commit and is
hash-verified before an explicitly audited transformation. The original Python
installer remains printable using --print-source.
"""
from __future__ import annotations
import base64
import hashlib
import re
import subprocess
import sys
import zlib

BASE_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
            '153775e23f3e7f2c77fced12a37e9a69b26ddfd1/'
            'scripts/build-esde-proven-20261014.py')
BASE_SHA256 = '4351dd9a97ca439215d543cf70307cd6653cb8e39d356d19633ded6691da0b85'
EXPECTED_GOW = 'bde2b3c1fdc8add6b73b24894d4d47837b1f2db3'
EXPECTED_GOT = '3c067d02124a242f1b13b7245a7c8b558dd87152'


def patch_one(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f'SAFE_STOP: installer baseline changed at {label}; matches={count}')
    return source.replace(old, new, 1)


def load_verified_baseline() -> str:
    command = ['curl', '-4fLsS', '--http1.1', '--retry', '2',
               '--connect-timeout', '20', '--max-time', '120', BASE_URL]
    result = subprocess.run(command, capture_output=True, timeout=150)
    if result.returncode:
        raise RuntimeError('Cannot fetch immutable installer: ' +
                           result.stderr.decode(errors='replace')[-1500:])
    if not (0 < len(result.stdout) < 100_000):
        raise RuntimeError('Unsafe installer payload length')
    wrapper = result.stdout.decode('utf-8')
    hits = re.findall(r"PACKED\s*=\s*'''([A-Za-z0-9+/=]+)'''", wrapper)
    if len(hits) != 1 or len(hits[0]) != 9876:
        raise RuntimeError('Original installer payload missing or changed')
    compressed = base64.b64decode(hits[0], validate=True)
    decompressor = zlib.decompressobj()
    source = decompressor.decompress(compressed, 100_000)
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise RuntimeError('Incomplete or unexpectedly large baseline source')
    if hashlib.sha256(source).hexdigest() != BASE_SHA256:
        raise RuntimeError('Original installer SHA-256 mismatch')
    return source.decode('utf-8')


def rewrite(source: str) -> str:
    # The two branches have advanced. Validate the immutable commits are still
    # ancestors; fetch content ONLY from the pins. Never auto-integrate new commits.
    source = patch_one(source, """    if git('rev-parse', 'refs/remotes/proven/' + GOW_BRANCH, stdout=True) != GOW_HEAD:
        raise RuntimeError('GoW provenance branch changed; refusing unreviewed commits')
    if git('rev-parse', 'refs/remotes/proven/' + GOT_BRANCH, stdout=True) != GOT_HEAD:
        raise RuntimeError('GoT provenance branch changed; refusing unreviewed commits')
""", """    for label, branch, verified in (('GoW', GOW_BRANCH, GOW_HEAD),
                                    ('GoT', GOT_BRANCH, GOT_HEAD)):
        branch_head = git('rev-parse', 'refs/remotes/proven/' + branch, stdout=True)
        if git('cat-file', '-t', verified, stdout=True) != 'commit':
            raise RuntimeError(f'{label} pinned commit unavailable: {verified}')
        check = git('merge-base', '--is-ancestor', verified, branch_head, check=False)
        if check.returncode != 0:
            raise RuntimeError(f'{label} branch no longer contains the pinned commit; refusing it')
        out('VERIFIED_PROVENANCE', label, 'pinned=', verified,
            'branch_current=', branch_head, 'ancestor=PASS')
""", 'branch commit ancestry')

    source = patch_one(source, """    git('merge', '--no-ff', '--no-edit', '-m', 'ES-DE: current upstream main plus pinned GoW baseline',
        'refs/remotes/proven/' + GOW_BRANCH)
""", """    try:
        git('merge', '--no-ff', '--no-edit', '-m',
            'ES-DE: current upstream main plus pinned GoW baseline', GOW_HEAD)
    except RuntimeError as exc:
        conflicts = git('diff', '--name-only', '--diff-filter=U', stdout=True, check=False)
        (EV / 'upstream-merge-conflicts.txt').write_text(conflicts + '\\n')
        diff = git('diff', '--cc', stdout=True, check=False)
        (EV / 'upstream-merge-conflicts.diff').write_text(diff + '\\n')
        # The 14 Oct 2026 upstream and Phase91 trees differ in ONLY the
        # serialization version pair. This is an exact-blob, single-file exception.
        cache = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
        upstream_blob = '08108e16888984bb8b06cb87ddedc6c094b995d3'
        proven_blob = 'adc6045a0064fbe284848a54c4ea33ed431e37cc'
        approved = conflicts.strip() == cache
        if approved:
            ours = git('rev-parse', ':2:' + cache, stdout=True)
            theirs = git('rev-parse', ':3:' + cache, stdout=True)
            approved = ours == upstream_blob and theirs == proven_blob
            if approved:
                current = show(':2', cache).decode('utf-8')
                retained = show(':3', cache).decode('utf-8')
                ours_pair = ('static constexpr u32 ShaderBinaryVersion = 9u;',
                             'static constexpr u32 ShaderMetaVersion = 6u;')
                retained_pair = ('static constexpr u32 ShaderBinaryVersion = 11u;',
                                 'static constexpr u32 ShaderMetaVersion = 8u;')
                approved = (all(current.count(x) == 1 for x in ours_pair)
                            and all(retained.count(x) == 1 for x in retained_pair))
                if approved:
                    normalized_current = current.replace(ours_pair[0], 'VERSION_BINARY').replace(
                        ours_pair[1], 'VERSION_META')
                    normalized_retained = retained.replace(retained_pair[0], 'VERSION_BINARY').replace(
                        retained_pair[1], 'VERSION_META')
                    approved = normalized_current == normalized_retained
        if not approved:
            git('merge', '--abort', check=False)
            raise RuntimeError('Unreviewed upstream conflict: ' + conflicts +
                               '; ES-DE unchanged. See ' + str(EV)) from exc
        try:
            git('checkout', '--theirs', '--', cache)
            git('add', '--', cache)
            git('commit', '--no-edit')
            if git('rev-parse', 'HEAD:' + cache, stdout=True) != proven_blob:
                raise RuntimeError('Resolved serialization file differs from verified Phase91 blob')
            if git('merge-base', '--is-ancestor', GOW_HEAD, 'HEAD', check=False).returncode != 0:
                raise RuntimeError('GoW baseline not retained after merge resolution')
            out('REVIEWED_MERGE_RESOLUTION=PASS', cache,
                'upstream=9/6', 'proven_cache=11/8', 'other_differences=NONE')
        except BaseException:
            git('merge', '--abort', check=False)
            raise
""", 'merge pinned GoW and capture conflict')

    # Bring the quit-dialog input interception forward without reviving the
    # deleted legacy installer or undoing upstream FPS pinning in layer.cpp.
    source = patch_one(source, """    git('cherry-pick', '--no-edit', QUIT_SHA)
""", """    try:
        git('cherry-pick', '--no-edit', QUIT_SHA)
    except RuntimeError as exc:
        layer = 'src/core/devtools/layer.cpp'
        legacy = 'scripts/install_local_default.py'
        paths = set(git('diff', '--name-only', '--diff-filter=U', stdout=True).splitlines())
        if paths != {layer, legacy}:
            raise RuntimeError('Quit-dialog cherry-pick has unreviewed conflicts: ' +
                               repr(sorted(paths))) from exc
        theirs = show(':3', layer).decode('utf-8')
        ours = show(':2', layer).decode('utf-8')
        if 'static bool fps_pinned = false;' not in ours or (
            'static float fps_anchor_width = FLT_MAX;' not in theirs):
            raise RuntimeError('Unknown upstream/fork FPS code; leaving ES-DE unchanged')
        if not all(x in theirs for x in ('static QuitDialog quit_dialog;',
            'static void ApplyQuitAction(QuitDialog::Action action)',
            'bool ProcessQuitEvent(const SDL_Event& event)',
            'bool IsQuitInputCaptured()')):
            raise RuntimeError('Pinned quit-dialog implementation incomplete')
        def once(value, original, replacement, label):
            if value.count(original) != 1:
                raise RuntimeError('Unexpected quit-dialog source region: ' + label)
            return value.replace(original, replacement, 1)
        for line in ('#include <SDL3/SDL_error.h>',
                     '#include "common/logging/log.h"',
                     '#include "quit_dialog.h"'):
            if ours.count(line) == 0:
                anchor_include = ('#include <SDL3/SDL_events.h>' if 'SDL_error' in line
                    else '#include "common/singleton.h"' if 'logging' in line
                    else '#include "video_core/renderer_vulkan/vk_presenter.h"')
                ours = once(ours, anchor_include, line + '\\n' + anchor_include,
                            'additional include ' + line)
        ours = once(ours, 'static bool show_quit_window = false;',
                    'static QuitDialog quit_dialog;', 'state')
        ours = once(ours, 'return DebugState.IsShowingDebugMenuBar();',
                    'return quit_dialog.IsVisible() || DebugState.IsShowingDebugMenuBar();',
                    'render lifecycle')
        original_window = ours.index('    if (show_quit_window) {')
        window_end = ours.index('    if (show_volume) {', original_window)
        repaired_window = theirs.index('    if (quit_dialog.IsVisible()) {')
        repaired_end = theirs.index('    if (show_volume) {', repaired_window)
        ours = ours[:original_window] + theirs[repaired_window:repaired_end] + ours[window_end:]
        start = ours.index('void ToggleQuitWindow() {')
        end = ours.index('void ShowVolume() {', start)
        fixed_start = theirs.index('static void ApplyQuitAction(QuitDialog::Action action) {')
        fixed_end = theirs.index('void ShowVolume() {', fixed_start)
        ours = ours[:start] + theirs[fixed_start:fixed_end] + ours[end:]
        if ('show_quit_window' in ours or 'fps_anchor_width' in ours
            or 'static bool fps_pinned = false;' not in ours
            or 'bool ProcessQuitEvent(const SDL_Event& event)' not in ours):
            raise RuntimeError('Quit-dialog merge invariants failed')
        (SRC / layer).write_text(ours)
        git('add', '--', layer)
        # The deleted installer is obsolete and must remain deleted.
        git('rm', '--', legacy)
        if git('diff', '--name-only', '--diff-filter=U', stdout=True).strip():
            raise RuntimeError('Quit-dialog still has unresolved paths')
        git('-c', 'core.editor=true', 'cherry-pick', '--continue')
        out('QUIT_DIALOG_RECONCILIATION=PASS',
            'upstream_FPS=retained', 'gamepad_input=retained',
            'legacy_installer=absent')
""", 'integrate quit dialog with current upstream FPS semantics')

    # A direct 3-way loading-GUI patch can conflict in CMake because GoW's
    # CMake changed. Preserve the current CMake and add only source registrations.
    source = patch_one(source, """    git('apply', '--3way', '--index', str(patchpath))
    git('commit', '-m', 'ES-DE: preserve centered loading through black frames')
""", """    try:
        git('apply', '--3way', '--index', str(patchpath))
    except RuntimeError as exc:
        conflicts = git('diff', '--name-only', '--diff-filter=U', stdout=True).splitlines()
        if conflicts != ['CMakeLists.txt']:
            raise RuntimeError('Unexpected loading GUI patch conflicts: ' +
                               repr(conflicts)) from exc
        # Stage 2 is the current official-upstream + GoW/quit CMake file.
        stage2 = show(':2', 'CMakeLists.txt').decode('utf-8')
        stage3 = show(':3', 'CMakeLists.txt').decode('utf-8')
        core_old = 'set(CORE src/core/aerolib/stubs.cpp'
        core_new = 'set(CORE src/core/startup_progress.h\\n         src/core/aerolib/stubs.cpp'
        imgui_old = '          src/imgui/imgui_translations.cpp'
        imgui_new = ('          src/imgui/startup_loading.cpp\\n'
                     '          src/imgui/startup_loading.h\\n'
                     '          src/imgui/imgui_translations.cpp')
        if any(stage2.count(v) != 1 for v in (core_old, imgui_old)):
            raise RuntimeError('Cannot locate current CMake source registration slots')
        if any(stage3.count(v) != 1 for v in (core_new, imgui_new)):
            raise RuntimeError('Proven loading GUI patch changed unexpectedly')
        candidate = stage2.replace(core_old, core_new, 1).replace(imgui_old, imgui_new, 1)
        if candidate.count('src/core/startup_progress.h') != 1 or (
                candidate.count('src/imgui/startup_loading.cpp') != 1 or
                candidate.count('src/imgui/startup_loading.h') != 1):
            raise RuntimeError('Loading GUI CMake result failed unique-entry check')
        path = SRC / 'CMakeLists.txt'
        path.write_text(candidate)
        git('add', '--', 'CMakeLists.txt')
        remaining = git('diff', '--name-only', '--diff-filter=U', stdout=True)
        if remaining.strip():
            raise RuntimeError('Unresolved GUI patch conflicts: ' + remaining)
        out('LOADING_GUI_CMAKE=PASS', 'upstream_GoW_options=retained',
            'added=CoreStartup+ImGuiLoading')
    git('commit', '-m', 'ES-DE: preserve centered loading through black frames')
""", 'loading GUI CMake registration merge')

    source = patch_one(source, """show('refs/remotes/proven/' + GOW_BRANCH, rel)""",
                       "show(GOW_HEAD, rel)", 'GoW immutable source')
    source = patch_one(source, """show('refs/remotes/proven/' + GOT_BRANCH, srt)""",
                       "show(GOT_HEAD, srt)", 'GoT immutable source')

    # Verify the manifest and exact game test binaries before cloning more Git.
    source = patch_one(source, """        integration()
        binary, runtime = build()
""", """        STEP = 'verify existing GoW/GoT artifacts before cloning'
        gow_sources = phase91_sources()
        got_sources_verified = got_sources()
        out('PROVEN_EVIDENCE=PASS', 'GoW_source_units=', len(gow_sources),
            'GoT_source_units=', len(got_sources_verified))
        integration()
        binary, runtime = build()
""", 'early artifact preflight')

    # On the server that already cloned official main, reuse available Git objects.
    # A reference clone does NOT modify the old checkout; --dissociate avoids
    # retaining a dependency on it. The new clone still contacts official origin.
    source = patch_one(source, """    run('git', '-c', 'http.version=HTTP/1.1', 'clone', '--filter=blob:none',
        'https://github.com/shadps4-emu/shadPS4.git', SRC)
""", """    clone_args = ['git', '-c', 'http.version=HTTP/1.1', 'clone', '--filter=blob:none']
    for older in sorted((ROOT / 'ESDE').glob('release-*/source'), reverse=True):
        if older == SRC or older.is_symlink() or not (older / '.git').is_dir():
            continue
        if not older.resolve().is_relative_to((ROOT / 'ESDE').resolve()):
            continue
        remote = subprocess.run(['git', '-C', str(older), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=8)
        if remote.returncode == 0 and remote.stdout.strip() == 'https://github.com/shadps4-emu/shadPS4.git':
            clone_args.extend(['--reference-if-able', str(older), '--dissociate'])
            out('REUSING_READONLY_GIT_OBJECTS=', older)
            break
    run(*clone_args, 'https://github.com/shadps4-emu/shadPS4.git', SRC)
""", 'immutable independent reference clone')

    # Phase91 manifest contains object files built from unchanged Git sources.
    # Such sources have no standalone overlay file. Only skip their overlay
    # import when the pinned Git content is byte-identical to the source now
    # present after merging the pinned GoW baseline.
    source = patch_one(source, """        if not matches:
            raise RuntimeError(f'Cannot locate exact compiled overlay source for {rel}')
        selected[rel] = require(matches[0], root=GOW / 'tests')
""", """        if not matches:
            pinned = show(GOW_HEAD, rel)
            actual = require(SRC / rel, root=SRC).read_bytes()
            if actual != pinned:
                raise RuntimeError(
                    f'No Phase91 overlay for {rel}, and checked-out source differs '
                    'from immutable GoW Git provenance')
            out('UNCHANGED_GIT_SOURCE', rel, 'sha256=', hashlib.sha256(actual).hexdigest())
            continue
        selected[rel] = require(matches[0], root=GOW / 'tests')
""", 'unmodified object source fallback')

    # A pre-clone source check cannot compare the missing overlay with pinned
    # Git content. Run the existing full checks after clone/merge instead.
    source = patch_one(source, """        STEP = 'verify existing GoW/GoT artifacts before cloning'
        gow_sources = phase91_sources()
        got_sources_verified = got_sources()
        out('PROVEN_EVIDENCE=PASS', 'GoW_source_units=', len(gow_sources),
            'GoT_source_units=', len(got_sources_verified))
        integration()
        binary, runtime = build()
""", """        STEP = 'integrate with verified immutable GoW/GoT source provenance'
        integration()
        binary, runtime = build()
""", 'defer source provenance until pinned git is available')

    # Include diagnostic proof in the final manifest.
    source = patch_one(source, """        'startup_gui': GUI_HEAD, 'controller_exit': QUIT_SHA,
""", """        'startup_gui': GUI_HEAD, 'controller_exit': QUIT_SHA,
        'gow_pinned_sha': GOW_HEAD, 'got_pinned_sha': GOT_HEAD,
        'branch_head_not_automatically_imported': True,
""", 'provenance manifest')

    compile(source, '<esde-upstream-v2-verified>', 'exec')
    assert f"GOW_HEAD = '{EXPECTED_GOW}'" in source
    assert f"GOT_HEAD = '{EXPECTED_GOT}'" in source
    assert "'branch_current=', branch_head, 'ancestor=PASS'" in source
    return source


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] not in
                              ('--print-source', '--check-source')):
        raise SystemExit('Usage: script.py [--print-source|--check-source]')
    source = rewrite(load_verified_baseline())
    if '--print-source' in sys.argv:
        sys.stdout.write(source)
        return 0
    if '--check-source' in sys.argv:
        print('ESDE_V2_SOURCE=PASS original SHA-256 verified, patches and syntax checked')
        return 0
    # Execute as __main__ to retain the original installer's exit status.
    namespace = {'__name__': '__main__', '__file__': '<verified-esde-v2>'}
    exec(compile(source, namespace['__file__'], 'exec'), namespace)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('SAFE_STOP_ESDE_V2:', exc, file=sys.stderr)
        raise SystemExit(1)
