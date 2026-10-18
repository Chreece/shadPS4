#!/usr/bin/env python3
"""Read-only-by-default surgical GPU command/resource diagnostic overlay.

Input code belongs to user's exact protected shadPS4 GoW source, not a
historical upstream source tree. Never infer semantics of SET_QUEUE_REG.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
from pathlib import Path

EXPECTED = {
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp":
        "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        "f20a0753f49fe7da02a3beef5f3365ee96bb031528a2447c21f14e7cd329120c",
}
LIVERPOOL = "src/video_core/amdgpu/liverpool.cpp"
SRT = next(x for x in EXPECTED if "flatten_" in x)
RESOURCES = next(x for x in EXPECTED if "resource_patching" in x)
MARKER = "GOW_GPU_RESOURCE_TRACE_20261018"

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def once(s: str, old: str, new: str, tag: str) -> str:
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"UNRECOGNIZED_{tag}_ANCHOR_MATCHES={n}")
    return s.replace(old, new, 1)

def srt_patch(src: str) -> str:
    src = once(src, "    u16 dst_off_dw;\n",
               "    u16 dst_off_dw;\n    u64 diagnostic_pgm_hash{};\n",
               "SRT_CONTEXT_FIELD")
    src = once(src, "    pass_info.dst_off_dw = NUM_USER_DATA_REGS;\n",
               "    pass_info.dst_off_dw = NUM_USER_DATA_REGS;\n"
               "    pass_info.diagnostic_pgm_hash = info.pgm_hash;\n",
               "SRT_CONTEXT_INITIALIZE")
    anchor = "static void VisitPointer(const IR::Value& off_dw, IR::Inst* subtree, PassInfo& pass_info,"
    helper = r'''struct SrtMissingOffsetReasons {
    u32 missing_flatbuf{};
    u32 unsupported{};
    u32 visited{};
};

// Diagnostics only: bounded walk of the failing offset's IR expression.
// No guest access, shader mutation, or fallback resource assumptions.
static SrtMissingOffsetReasons InspectSrtMissingOffset(const IR::Value& first) {
    SrtMissingOffsetReasons result{};
    std::vector<IR::Value> queue{first};
    std::unordered_set<const IR::Inst*> seen;
    for (size_t i = 0; i < queue.size() && i < 128; ++i) {
        const auto& value = queue[i];
        if (value.IsImmediate()) {
            continue;
        }
        const auto* inst = value.TryInst();
        if (!inst || !seen.insert(inst).second) {
            continue;
        }
        ++result.visited;
        const auto op = inst->GetOpcode();
        if (op == IR::Opcode::ReadConst || op == IR::Opcode::ReadConstBuffer) {
            if (GetFlatbufOffset(inst) == 0) {
                ++result.missing_flatbuf;
            }
            continue;
        }
        if (op == IR::Opcode::GetUserData) {
            continue;
        }
        if (!IsAllowedOffsetInstruction(inst)) {
            ++result.unsupported;
            continue;
        }
        for (u32 arg = 0; arg < inst->NumArgs(); ++arg) {
            if (auto v = inst->Arg(arg); !v.IsImmediate() && v.TryInst()) {
                queue.push_back(v);
            }
        }
    }
    return result;
}

static void ReportSrtMissingOffset(PassInfo& pass_info, const IR::Value& offset,
                                   const char* phase) {
    const auto reasons = InspectSrtMissingOffset(offset);
    const IR::Inst* origin = offset.TryInst();
    const auto op = origin ? u32(origin->GetOpcode()) : 0xffffffffu;
    LOG_ERROR(Render_Recompiler,
              "Failed to compute offset for SRT walker "
              "GOW_SRT_OFFSET_REASON phase={} shader={:#x} op={} "
              "zero_flatbuf={} unsupported={} visited={}",
              phase, pass_info.diagnostic_pgm_hash, op, reasons.missing_flatbuf,
              reasons.unsupported, reasons.visited);
}

''' + MARKER + r'''
'''
    # Avoid introducing an arbitrary C++ identifier: use an inert comment marker.
    helper = helper.replace(MARKER + "\n", "// " + MARKER + "\n")
    src = once(src, anchor, helper + anchor, "SRT_INSPECTION_HELPER")
    first = '''    if (!PushPtr(c, pass_info, off_dw)) {
        LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
        return;
    }'''
    replacement = '''    if (!PushPtr(c, pass_info, off_dw)) {
        ReportSrtMissingOffset(pass_info, off_dw, "pointer");
        return;
    }'''
    src = once(src, first, replacement, "SRT_ROOT_FAILURE")
    second = '''                c.setSize(code_begin);
                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
                continue;'''
    src = once(src, second, '''                c.setSize(code_begin);
                ReportSrtMissingOffset(pass_info, src_off_dw, "member");
                continue;''', "SRT_MEMBER_FAILURE")
    root = "    const auto plan = MakeSrtRootDependencyPlan(pass_info);\n"
    src = once(src, root, root + '''    if (plan.cyclic || plan.ambiguous || plan.capped) {
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_ROOT_PLAN shader={:#x} root_count={} cyclic={} ambiguous={} capped={}",
                    info.pgm_hash, roots.size(), plan.cyclic, plan.ambiguous, plan.capped);
    }
''', "SRT_ROOT_PLAN_DIAGNOSTIC")
    return src

def resource_patch(src: str) -> str:
    src = once(src, "SharpLocation SharpLocationFromSource(const IR::Inst* inst) {",
               "SharpLocation SharpLocationFromSource(const IR::Inst* inst, u64 shader_hash) {",
               "SHARP_SOURCE_SIGNATURE")
    src = once(src, '        LOG_WARNING(Render_Recompiler, "Sharp source was not flatenned");',
               '''        LOG_WARNING(Render_Recompiler,
                    "Sharp source was not flatenned GOW_SHARP_REASON shader={:#x} opcode={} "
                    "flatbuf_location={}",
                    shader_hash, u32(inst->GetOpcode()), location);''', "SHARP_WARNING")
    src = once(src, "SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp) {",
               "SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp, const Info& info) {",
               "SHARP_FETCH_SIGNATURE")
    src = once(src, "sharp_fetch.offsets[i] = SharpLocationFromSource(dword.Inst());",
               "sharp_fetch.offsets[i] = SharpLocationFromSource(dword.Inst(), info.pgm_hash);",
               "SHARP_FETCH_LOOKUP")
    for label in ("AmdGpu::Buffer", "AmdGpu::Image", "AmdGpu::Sampler"):
        import re
        pattern = f"ConstructSharpFetch<{label}>(resource.sharps["
        matches = list(re.finditer(re.escape(pattern) + r"(\d+)\]\)", src))
        if len(matches) != 1:
            raise RuntimeError(f"SHARP_FETCH_CALL_{label}_COUNT_{len(matches)}")
        m = matches[0]
        src = src[:m.start()] + f"ConstructSharpFetch<{label}>(resource.sharps[{m[1]}], info)" + src[m.end():]
    src = once(src,
               "SharpLocationFromSource(resource.sharps[0].dwords[0].Inst())",
               "SharpLocationFromSource(resource.sharps[0].dwords[0].Inst(), info.pgm_hash)",
               "SHARP_FMASK_LOOKUP")
    src = once(src, "SharpLocationFromSource(lod_prod.Inst())",
               "SharpLocationFromSource(lod_prod.Inst(), info.pgm_hash)",
               "SHARP_LOD_LOOKUP")
    return src

def liverpool_patch(src: str) -> str:
    # The protected older backend ignores this command and writes hundreds
    # of identical warnings. We do NOT implement guessed queue semantics.
    src = once(src, "#include <boost/preprocessor/stringize.hpp>",
               "#include <boost/preprocessor/stringize.hpp>\n#include <unordered_map>",
               "QUEUE_HASH_MAP_INCLUDE")
    original = '''        case PM4ItOpcode::SetQueueReg: {
            const auto* set_data = reinterpret_cast<const PM4CmdSetQueueReg*>(header);
            LOG_WARNING(Render, "Encountered compute SetQueueReg: vqid = {}, reg_offset = {:#x}",
                        set_data->vqid.Value(), set_data->reg_offset.Value());
            break;
        }'''
    diagnostic = '''        case PM4ItOpcode::SetQueueReg: {
            const auto* set_data = reinterpret_cast<const PM4CmdSetQueueReg*>(header);
            const u32 queue_id = set_data->vqid.Value();
            const u32 register_offset = set_data->reg_offset.Value();
            const u32 count = set_data->Size() / sizeof(u32);
            const u32 data0 = count > 0 ? set_data->data[0] : 0;
            const u32 data1 = count > 1 ? set_data->data[1] : 0;
            const u32 data2 = count > 2 ? set_data->data[2] : 0;
            // Count by queue, register and first value; never guess whether
            // register 0x0b is a shader, scheduling, or control register.
            static thread_local std::unordered_map<u64, u64> seen;
            const u64 key = (u64(queue_id) << 40) |
                            (u64(register_offset) << 32) | data0;
            u64 occurrence = 0;
            if (auto it = seen.find(key); it != seen.end()) {
                occurrence = ++it->second;
            } else if (seen.size() < 128) {
                occurrence = seen.emplace(key, 1).first->second;
            }
            if (occurrence && ((occurrence & (occurrence - 1)) == 0)) {
                LOG_WARNING(Render,
                            "GOW_QUEUE_REG_PACKET vqid={} reg_offset={:#x} "
                            "defer_exec={} words={} data0={:#x} data1={:#x} "
                            "data2={:#x} occurrences={}",
                            queue_id, register_offset, set_data->defer_exec.Value(),
                            count, data0, data1, data2, occurrence);
            }
            break;
        }'''
    return once(src, original, diagnostic, "SET_QUEUE_REG_HANDLER")

def patch_one(rel: str, original: str) -> str:
    return (
        srt_patch(original) if rel == SRT else
        resource_patch(original) if rel == RESOURCES else
        liverpool_patch(original) if rel == LIVERPOOL else
        original
    )

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source_dir", type=Path)
    p.add_argument("report_dir", type=Path)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    a.report_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for rel in (SRT, RESOURCES, LIVERPOOL):
        src = a.source_dir / rel
        if not src.is_file() or src.is_symlink():
            raise SystemExit(f"UNSAFE_OR_MISSING_SOURCE={rel}")
        old = src.read_bytes()
        digest = sha(old)
        # If the user's local file differs from the previously audited file,
        # save its exact source for the next diagnosis before failing closed.
        reference_copy = a.report_dir / ("actual_" + rel.replace("/", "_"))
        reference_copy.write_bytes(old)
        if rel in EXPECTED and digest != EXPECTED[rel]:
            raise SystemExit(f"AUDITED_LOCAL_SHA_CHANGED={rel} got={digest}")
        if MARKER in old.decode("utf-8"):
            raise SystemExit(f"PROBE_ALREADY_INSTALLED={rel}")
        try:
            new = patch_one(rel, old.decode("utf-8")).encode("utf-8")
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(f"SOURCE_CONTRACT_FAILED={rel} reason={exc}") from exc
        if old == new:
            raise SystemExit(f"NO_SOURCE_CHANGE={rel}")
        outputs.append((src, rel, old, new))
    # Stage complete diffs and SHA values before any source changes.
    summary = []
    for src, rel, old, new in outputs:
        diff = "".join(difflib.unified_diff(
            old.decode().splitlines(True), new.decode().splitlines(True),
            fromfile="protected/" + rel, tofile="isolated/" + rel))
        target = a.report_dir / (rel.replace("/", "_") + ".diff")
        target.write_text(diff)
        summary.append({"file":rel, "before_sha256":sha(old), "after_sha256":sha(new),
                        "diff":str(target)})
    import json
    (a.report_dir / "file-hashes.json").write_text(json.dumps(summary,indent=2)+"\n")
    if a.apply:
        for src, rel, old, new in outputs:
            src.write_bytes(new)
    print("REAL_SOURCE_GPU_RESOURCE_TRACE=PASS")
    print("CHANGES_APPLIED=" + str(a.apply))
    print("QUEUE_SEMANTICS=UNCHANGED")
    print("PROTECTED_SRT_AND_RESOURCE_HASHES=MATCH_AUDIT")

if __name__ == "__main__":
    main()
