#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add only read-only Ghost diagnostics to the already-built shadPS4 source.

This script is intentionally NOT a shader/graphics fix. It records whether
guest flips continue after the first frame and which shaders expose unsupported
SRT offsets. Both source files are backed up before modification.
"""
from pathlib import Path
import hashlib
import sys

TRACE = "GHOST_TRACE"

def replace_one(src: str, old: str, new: str, desc: str) -> str:
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f"Expected exactly one {desc} source anchor, found {n}")
    return src.replace(old, new)

def patch_flatten(text: str) -> str:
    text = replace_one(text,
        "    u16 dst_off_dw;\n\n    PtrUserList* GetUsesAsPointer",
        "    u16 dst_off_dw;\n    u64 trace_shader_hash{};\n\n    PtrUserList* GetUsesAsPointer",
        "add read-only shader hash field")
    text = replace_one(text,
        '    PassInfo pass_info;\n\n    // traverse at end',
        '    PassInfo pass_info;\n    pass_info.trace_shader_hash = program.info.pgm_hash;\n\n    // traverse at end',
        "initialize shader hash")
    text = replace_one(text,
        '        LOG_ERROR(Render_Recompiler, "Unexpected instruction for offset computation, {}",\n'
        '                  magic_enum::enum_name(inst->GetOpcode()));',
        '        LOG_ERROR(Render_Recompiler,\n'
        '                  "Unexpected instruction for offset computation, {} shader={:#x}",\n'
        '                  magic_enum::enum_name(inst->GetOpcode()), pass_info.trace_shader_hash);',
        "unsupported-offset opcode log")
    old = 'LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");'
    if text.count(old) != 2:
        raise RuntimeError(f"Expected two SRT walker failure logs, found {text.count(old)}")
    text = text.replace(old,
        'LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker shader={:#x}",\n'
        '                          pass_info.trace_shader_hash);')
    return text

def patch_videoout(text: str) -> str:
    old = (
        "        timer.End();\n"
        "    }\n"
        "}\n"
        "\n"
        "} // namespace Libraries::VideoOut"
    )
    new = (
        "        timer.End();\n"
        "        // Playtest-only diagnostics: sample guest flip progress every 180 vblanks.\n"
        "        // The lock scopes are separate; neither stays locked while logging.\n"
        "        if (vblank_status.count % 180 == 0) {\n"
        "            u64 guest_flips{};\n"
        "            u64 guest_pending{};\n"
        "            {\n"
        "                std::scoped_lock lock{main_port.port_mutex};\n"
        "                guest_flips = main_port.flip_status.count;\n"
        "                guest_pending = main_port.flip_status.flip_pending_num;\n"
        "            }\n"
        "            size_t host_queue{};\n"
        "            {\n"
        "                std::scoped_lock lock{mutex};\n"
        "                host_queue = requests.size();\n"
        "            }\n"
        "            LOG_INFO(Lib_VideoOut,\n"
        '                     "GHOST_TRACE vblank={} guest_flips={} pending={} queued={} startup_active={}",\n'
        "                     vblank_status.count, guest_flips, guest_pending, host_queue,\n"
        "                     Core::Startup::progress.IsActive());\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "} // namespace Libraries::VideoOut"
    )
    return replace_one(text, old, new, "end-of-present-loop")

def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: instrument_ghost_trace.py SOURCE_WORKTREE BACKUP_DIR")
    root = Path(sys.argv[1]).resolve()
    backup = Path(sys.argv[2]).resolve()
    transformations = {
        "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp": patch_flatten,
        "src/core/libraries/videoout/driver.cpp": patch_videoout,
    }
    proposed = {}
    for path, fn in transformations.items():
        original = (root / path).read_bytes()
        if TRACE.encode() in original:
            raise RuntimeError("Already instrumented: " + path)
        transformed = fn(original.decode("utf-8")).encode("utf-8")
        if transformed == original:
            raise RuntimeError("No diagnostic changes for " + path)
        proposed[path] = (original, transformed)
        digest = hashlib.sha256(original).hexdigest()
        print(f"Validated source anchor: {path} sha256={digest}")

    # Only after all source anchors validate do we save backups and edit sources.
    for path, (before, _) in proposed.items():
        p = backup / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(before)
    for path, (_, after) in proposed.items():
        (root / path).write_bytes(after)
        print(f"Applied read-only diagnostics: {path}")
    print("Instrumentation ready. No shader behavior or graphics commands changed.")

if __name__ == "__main__":
    main()
