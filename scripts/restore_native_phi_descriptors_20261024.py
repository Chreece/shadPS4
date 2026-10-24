#!/usr/bin/env python3
"""Restore generic GPU Phi descriptor handling from a proven shadPS4 source.

Exact-preimage backport from retained source commit 94ed29b7, not a wholesale
old renderer merge. A Phi is a shader control-flow value, never a scalar sharp.
Handle only provably reconstructible read-only resources via exact branch
selection. All unsupported dynamic sources remain rejected. Never substitute
guest values, skip draws, change descriptor layouts, use DynamoRIO or title IDs.
"""
from __future__ import annotations
import hashlib
import re

ORIGINAL_REF="94ed29b7c5767718ead87164558d6ec7523bcc9a"
ORIGINAL_DISCOVER_BLOB="ceca5641b086887a29e85b9456ccf8386f98bb64"
ORIGINAL_HEADER_BLOB="e54cb2897c2f888c2b2b8322c19615f484588bc1"
DISCOVER="src/shader_recompiler/ir/passes/resource_discover_pass.cpp"
PREFLIGHT="src/shader_recompiler/ir/passes/image_phi_preflight.h"
CACHE="src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
EXPECTED={
 DISCOVER:"daea11b7bc2f6339ba859043a3fd6a29450268b1",
 CACHE:"e94a3b23e7083dc584ce383b79bade399f308275",
}
OUT_PATHS=(DISCOVER,PREFLIGHT,CACHE)
INSERT_START="static bool IsSelectableBufferLoad(const IR::Inst& inst) {"
INSERT_END="// Dynamic T#/S# tables need a GPU-selected descriptor binding."
BIND_ANCHOR="struct IndexedImageTableSource {"
PASS_ANCHOR="""ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {
    ResourceDiscoveryList sharp_usages;
"""
PASS_NEW="""ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {
    // Preserve the incoming GPU Phi selections as shader control flow.
    // Perform read-only resource splits before discovery; the transforms
    // may erase their original intrusive-list instructions.
    std::size_t original_samples = 0;
    for (const IR::Block* const block : program.blocks) {
        for (const IR::Inst& inst : block->Instructions()) {
            original_samples += inst.GetOpcode() == IR::Opcode::ImageSampleRaw;
        }
    }
    const std::size_t resource_capacity = std::min<std::size_t>(NUM_IMAGES, NUM_SAMPLERS);
    std::size_t image_split_budget = original_samples < resource_capacity
        ? resource_capacity - original_samples : 0;
    for (IR::Block* const block : program.blocks) {
        for (auto it = block->begin(); it != block->end();) {
            IR::Inst& inst = *it++;
            if (IsSelectableBufferLoad(inst)) {
                SplitBufferLoadBySharpPhi(*block, inst);
            } else if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw) {
                SplitImageSampleBySharpPhi(*block, inst, image_split_budget, program.info);
            }
        }
    }
    ResourceDiscoveryList sharp_usages;
"""

def blob(content:bytes)->str:
    return hashlib.sha1(b"blob "+str(len(content)).encode()+b"\0"+content).hexdigest()

def transform(discover:str,cache:str,original:str,header:str):
    if blob(discover.encode())!=EXPECTED[DISCOVER]:
        raise RuntimeError("NATIVE_RESOURCE_DISCOVERY_EXACT_PREIMAGE_MISMATCH")
    if blob(cache.encode())!=EXPECTED[CACHE]:
        raise RuntimeError("NATIVE_SHADER_CACHE_EXACT_PREIMAGE_MISMATCH")
    if blob(original.encode())!=ORIGINAL_DISCOVER_BLOB:
        raise RuntimeError("OLD_GENERIC_PHI_SOURCE_BLOB_CHANGED")
    if blob(header.encode())!=ORIGINAL_HEADER_BLOB:
        raise RuntimeError("OLD_GENERIC_IMAGE_PHI_PREFLIGHT_CHANGED")
    if discover.count(PASS_ANCHOR)!=1 or discover.count(BIND_ANCHOR)!=1:
        raise RuntimeError("PHI_PASS_INSERTION_POINT_CHANGED")
    start=original.find(INSERT_START)
    end=original.find(INSERT_END,start)
    if start<0 or end<=start:
        raise RuntimeError("VERIFIED_GENERIC_PHI_SOURCE_RANGE_MISSING")
    bridge=original[start:end].rstrip()+"\n\n"
    for required in (
        "SplitBufferLoadBySharpPhi(", "SplitImageSampleBySharpPhi(",
        "CanRebuildSharpValue(", "SelectSideResult(",
        "CanSplitSharpImagePhi(", "remaining_budget -= branches",
        "ReplaceUsesWithAndRemove(result)",
    ):
        if required not in bridge:
            raise RuntimeError("GENERIC_PHI_RESTORE_INCOMPLETE="+required)
    for forbidden in ("CUSA", "SHADPS4_GOT", "GotSceneDepthAB",
                      "return AmdGpu::Image::Null(", "VK_NULL_HANDLE"):
        if forbidden in bridge:
            raise RuntimeError("UNACCEPTABLE_PHI_REPLAY_SOURCE="+forbidden)
    for directive,anchor in (
        ("#include <algorithm>\n","#include \"common/assert.h\"\n"),
        ("#include <cstdlib>\n","#include \"common/assert.h\"\n"),
        ("#include <span>\n","#include \"common/assert.h\"\n"),
        ("#include <unordered_map>\n","#include \"common/assert.h\"\n"),
        ("#include \"common/logging/log.h\"\n","#include \"common/assert.h\"\n"),
        ("#include \"shader_recompiler/ir/ir_emitter.h\"\n",
         "#include \"shader_recompiler/ir/passes/ir_passes.h\"\n"),
        ("#include \"shader_recompiler/ir/passes/image_phi_preflight.h\"\n",
         "#include \"shader_recompiler/ir/passes/ir_passes.h\"\n"),
    ):
        if directive not in discover:
            if discover.count(anchor)!=1:
                raise RuntimeError("GENERIC_PHI_CPP_INCLUDE_ANCHOR_CHANGED="+anchor)
            discover=discover.replace(anchor,directive+anchor,1)
    discover=discover.replace(BIND_ANCHOR,bridge+BIND_ANCHOR,1)
    discover=discover.replace(PASS_ANCHOR,PASS_NEW,1)
    # A genuinely dynamic Phi can still exceed the safe static-split rules.
    # Keep the assertion, but record its actual incoming opcodes first so
    # it is never confused with the unrelated masked-descriptor regression.
    unsupported='''        ASSERT_MSG(IsSharpSource(source),
                   "Unsupported descriptor producer opcode={} at dword={}",
                   static_cast<u32>(source->GetOpcode()), i);'''
    detailed='''        if (source->GetOpcode() == IR::Opcode::Phi) {
            const auto incoming_opcode = [&](std::size_t side) -> s32 {
                const IR::Inst* const incoming = source->Arg(side).TryInst();
                return incoming ? static_cast<s32>(incoming->GetOpcode()) : -1;
            };
            LOG_ERROR(Render_Recompiler,
                      "UnresolvedShaderPhiDescriptor: dword={} alternatives={} "
                      "incoming0={} incoming1={} incoming2={}",
                      i, source->NumArgs(),
                      source->NumArgs() > 0 ? incoming_opcode(0) : -2,
                      source->NumArgs() > 1 ? incoming_opcode(1) : -2,
                      source->NumArgs() > 2 ? incoming_opcode(2) : -2);
        }
''' + unsupported
    if discover.count(unsupported)!=1:
        raise RuntimeError("PHI_ASSERT_PROVENANCE_ANCHOR_CHANGED")
    discover=discover.replace(unsupported,detailed,1)
    for required in ("SplitImageSampleBySharpPhi(*block, inst, image_split_budget, program.info)",
                     "SplitBufferLoadBySharpPhi(*block, inst)",
                     "IsSelectableBufferLoad(inst)", "auto it = block->begin()",
                     "MarkReadConstBufferSharpSources(", "PeelSharpDwordMasks(",
                     "AnalyzeIndexedImageTable("):
        if required not in discover:
            raise RuntimeError("GENERIC_PHI_SOURCE_CONTRACT_MISSING="+required)
    # Shader binaries and metadata require a fresh cache after resource count
    # and handle selection change; never override the normal user cache itself.
    bump={
        "ShaderBinaryVersion":("0xeu","0xfu"),
        "ShaderMetaVersion":("0x9u","0xau"),
    }
    for field,(old,new) in bump.items():
        pattern=r"(?m)^static constexpr u32 "+field+r" = "+re.escape(old)+r";$"
        cache,n=re.subn(pattern,"static constexpr u32 "+field+" = "+new+";",cache)
        if n!=1:
            raise RuntimeError("SHADER_CACHE_VERSION_SOURCE_CHANGED="+field)
    return {
        DISCOVER:discover,
        PREFLIGHT:header,
        CACHE:cache,
    }

def self_test():
    # Minimal static interface guard: semantic tests run on the compiled
    # C++ source and actual produced shader IR, not these Python strings.
    assert "IR::Opcode::Phi" in PASS_NEW or "ImageSampleRaw" in PASS_NEW
    assert "SplitBufferLoadBySharpPhi" in PASS_NEW
    assert "SplitImageSampleBySharpPhi" in PASS_NEW
    assert "return AmdGpu::Image::Null(" not in PASS_NEW
    print("GENERIC_PHI_RESTORE_SOURCE_TEST=PASS",flush=True)

if __name__=="__main__":
    import sys
    if sys.argv[1:]==["--self-test"]:
        self_test()
    else:
        raise SystemExit("Usage: --self-test")
