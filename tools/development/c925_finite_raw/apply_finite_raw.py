#!/usr/bin/env python3
"""Exact-source guarded finite raw image-read correction. No title/hash gates."""
from pathlib import Path
import hashlib,json,difflib,re
EXPECTED={'src/shader_recompiler/ir/passes/resource_discover_pass.cpp': 'afbce4084a993892a0cae1d48f994436eaf102f896f1c9970bf264559ff8ce34', 'src/shader_recompiler/ir/passes/resource_pass.h': 'f53b13a33c202bae951c075d19963e341abc21f719ff4f7bf34c614a25963367', 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': '342f6947a46f8c7aca642f0855af0e7e91dc9552aa53eee8b4d286b7164393d6', 'src/shader_recompiler/ir/opcodes.inc': 'd2d52aab0cc8ad965aed4a2f3b450f29c7da5d79ccbb87ccf6949147d4f81096', 'src/shader_recompiler/ir/ir_emitter.h': '9f630ba16a1bd536933fcb2df528f1d1e298898f0b2214a71567abf3f355c7a7', 'src/shader_recompiler/ir/ir_emitter.cpp': 'd1434174688b29c147a581d1f35996e7b7482230ab428cf8df31597c83030b9b', 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h': 'cbd900ac5d47830dafd163203bb45bb3faaac5cbc19f70f89b8ecbef2c1059c2', 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': 'e06145b1aef3d862959dd353b61296b362a9475f735fed4f1d217469f379f2a5', 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': '11a7e65df7c302536422a3ba8fc78c074b94e9767756c0c5d7eaf2b91b4fd224'}
FILES=list(EXPECTED)
NEW="src/shader_recompiler/ir/passes/finite_raw_image_plan.h"

def transform(original):
    for p, sha in EXPECTED.items():
        if hashlib.sha256(original[p]).hexdigest() != sha:
            raise ValueError('SOURCE_CHANGED:' + p)
    a = {p: data.decode() for p, data in original.items()}
    o = dict(a)

    def change(p, old, new):
        if a[p].count(old) != 1:
            raise ValueError((p, old[:80], a[p].count(old)))
        a[p] = a[p].replace(old, new)
    p = FILES[0]
    change(p, '#include "shader_recompiler/ir/passes/resource_pass.h"', '#include "shader_recompiler/ir/passes/resource_pass.h"\n#include "shader_recompiler/ir/passes/finite_raw_image_plan.h"')
    insert = '\n// Resolve only the finite domain proved by the real SSA latch/backedge.\n// The loop and selection still run on the GPU. Each image access is guarded,\n// so unselected textures are never speculatively read.\nstatic void SplitFiniteRawImageReads(\n    IR::Program& program, std::unordered_map<IR::Inst*, IR::Value>& guards) {\n    std::size_t images = 0;\n    for (const auto* block : program.blocks) {\n        for (const auto& inst : block->Instructions()) images += IsImageInstruction(inst);\n    }\n    std::size_t budget = images < NUM_IMAGES ? NUM_IMAGES - images : 0;\n    for (auto* block : program.blocks) {\n        for (auto it = block->begin(); it != block->end();) {\n            IR::Inst& inst = *it++;\n            const auto plan = FiniteRawImage::Analyze(inst);\n            if (!plan || plan->range.count - 1 > budget) continue;\n            const auto point = IR::Block::InstructionList::s_iterator_to(inst);\n            IR::IREmitter ir{*block, point};\n            IR::Value result = ir.CompositeConstruct(ir.Imm32(0.0f), ir.Imm32(0.0f),\n                                                     ir.Imm32(0.0f), ir.Imm32(0.0f));\n            for (u32 side = 0; side < plan->range.count; ++side) {\n                std::array<IR::Value, 8> words;\n                for (u32 word = 0; word < 8; ++word) {\n                    words[word] = ir.ReadConst(plan->base, ir.Imm32(plan->offsets[side][word]));\n                }\n                const auto low = ir.CompositeConstruct(words[0], words[1], words[2], words[3]);\n                const auto high = ir.CompositeConstruct(words[4], words[5], words[6], words[7]);\n                const auto handle = ir.ImageHandle(low, high);\n                const IR::U1 selected = ir.IEqual(IR::U32{plan->selector},\n                                                 ir.Imm32(plan->range.first + side));\n                auto* branch_read = &*block->PrependNewInst(point, inst);\n                branch_read->SetArg(0, handle);\n                guards.emplace(branch_read, selected);\n                result = SelectSideResult(ir, selected, IR::Value{branch_read}, result);\n            }\n            budget -= plan->range.count - 1;\n            LOG_INFO(Render_Recompiler,\n                     "FiniteRawImageRead: shader={:#x} first={} count={} "\n                     "cfg_proven=true gpu_guarded=true",\n                     program.info.pgm_hash, plan->range.first, plan->range.count);\n            inst.ReplaceUsesWithAndRemove(result);\n        }\n    }\n}\n\n'
    anchor = 'ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {'
    change(p, anchor, insert + anchor)
    change(p, '    ResourceDiscoveryList sharp_usages;', '    std::unordered_map<IR::Inst*, IR::Value> finite_image_guards;\n    SplitFiniteRawImageReads(program, finite_image_guards);\n    ResourceDiscoveryList sharp_usages;')
    change(p, '                DiscoverImageSharp(*block, inst, sharp_usages);', '                DiscoverImageSharp(*block, inst, sharp_usages);\n                if (const auto guard = finite_image_guards.find(&inst);\n                    guard != finite_image_guards.end()) {\n                    sharp_usages.back().image_read_guard = guard->second;\n                }')
    change(FILES[1], '    IR::Inst* user{};', '    IR::Inst* user{};\n    IR::Value image_read_guard{};')
    change(FILES[2], '        auto texel = ir.ImageRead(image_handle, coords, lod, ms, inst_info);', '        auto texel = resource.image_read_guard.IsEmpty()\n            ? ir.ImageRead(image_handle, coords, lod, ms, inst_info)\n            : ir.ImageReadGuarded(image_handle, coords, lod, ms,\n                                  IR::U1{resource.image_read_guard}, inst_info);')
    change(FILES[2], '    auto image = image_res.GetSharp(info);\n    ASSERT(image.GetType() != AmdGpu::ImageType::Invalid);', '    if (!resource.image_read_guard.IsEmpty()) {\n        LOG_INFO(Render_Recompiler, "FiniteRawImageSharp: shader={:#x} resolved={}",\n                 info.pgm_hash, image_res.sharp_fetch.summary !=\n                                    SharpFetch<AmdGpu::Image>::Summary::Invalid);\n    }\n    auto image = image_res.GetSharp(info);\n    ASSERT(image.GetType() != AmdGpu::ImageType::Invalid);')
    p = FILES[3]
    line = next((x for x in a[p].splitlines() if x.startswith('OPCODE(ImageRead,')))
    change(p, line, line + '\nOPCODE(ImageReadGuarded,                                    F32x4,          Opaque,         Opaque,         U32,            U32,            U1,             )')
    p = FILES[4]
    anchor = '    [[nodiscard]] Value ImageRead(const Value& handle, const Value& coords, const U32& lod,\n                                   const U32& multisampling, TextureInstInfo info);'
    m = re.search('    \\[\\[nodiscard\\]\\] Value ImageRead\\([\\s\\S]*?TextureInstInfo info\\);', a[p])
    assert m
    change(p, m[0], m[0] + '\n    [[nodiscard]] Value ImageReadGuarded(const Value& handle, const Value& coords,\n        const U32& lod, const U32& multisampling, const U1& guard, TextureInstInfo info);')
    p = FILES[5]
    anchor = 'Value IREmitter::ImageRead('
    idx = a[p].index(anchor)
    end = a[p].index('\n}', idx) + 2
    func = a[p][idx:end]
    change(p, func, func + '\n\nValue IREmitter::ImageReadGuarded(const Value& handle, const Value& coords, const U32& lod,\n                                    const U32& multisampling, const U1& guard,\n                                    TextureInstInfo info) {\n    return Inst(Opcode::ImageReadGuarded, Flags{info}, handle, coords, lod, multisampling, guard);\n}')
    p = FILES[6]
    anchor = 'Id EmitImageRead(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod, Id ms);'
    change(p, anchor, anchor + '\nId EmitImageReadGuarded(EmitContext& ctx, IR::Inst* inst, u32 handle,\n                        Id coords, Id lod, Id ms, Id guard);')
    p = FILES[7]
    anchor = 'void EmitImageWrite('
    func = 'Id EmitImageReadGuarded(EmitContext& ctx, IR::Inst* inst, u32 handle,\n                        Id coords, Id lod, Id ms, Id guard) {\n    const Id taken = ctx.OpLabel();\n    const Id skipped = ctx.OpLabel();\n    const Id merge = ctx.OpLabel();\n    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);\n    ctx.OpBranchConditional(guard, taken, skipped);\n    ctx.AddLabel(taken);\n    const Id texel = EmitImageRead(ctx, inst, handle, coords, lod, ms);\n    // Retain the actual last label if EmitImageRead gains nested control flow.\n    const Id taken_end = ctx.last_label;\n    ctx.OpBranch(merge);\n    ctx.AddLabel(skipped);\n    const Id zero = ctx.ConstF32(0.0f, 0.0f, 0.0f, 0.0f);\n    ctx.OpBranch(merge);\n    ctx.AddLabel(merge);\n    return ctx.OpPhi(ctx.F32[4], texel, taken_end, zero, skipped);\n}\n\n'
    change(p, anchor, func + anchor)
    p = FILES[8]
    for key in ['ShaderMetaVersion', 'ShaderBinaryVersion']:
        a[p], n = re.subn('(static constexpr u32 ' + key + '\\s*=\\s*)(?:0x[0-9a-fA-F]+|\\d+)u?(;)', '\\g<1>0x46524901u\\2', a[p])
        assert n == 1
    a[NEW] = Path(__file__).with_name('finite_raw_image_plan.h').read_text()
    return {p: s.encode() for p, s in a.items()}

def apply(root,evid):
    original={p:(root/p).read_bytes() for p in FILES}
    if (root/NEW).exists(): raise ValueError("FINITE_RAW_ALREADY_EXISTS")
    changed=transform(original)
    evid.mkdir(parents=True,exist_ok=True)
    manifest={};diff=[]
    for p,data in changed.items():
        old=original.get(p,b'')
        for label,value in (("before",old),("after",data)):
            if label=="before" and p not in original:continue
            dest=evid/("source-"+label)/p
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(value)
        manifest[p]={"before":hashlib.sha256(old).hexdigest() if p in original else None,"after":hashlib.sha256(data).hexdigest()}
        diff.extend(difflib.unified_diff(old.decode().splitlines(True),data.decode().splitlines(True),"a/"+p if p in original else "/dev/null","b/"+p))
    (evid/"source-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    (evid/"finite-raw-image.patch").write_text("".join(diff))
    for p,data in changed.items():
        dest=root/p;tmp=dest.with_name(dest.name+".finite-raw-pending")
        tmp.write_bytes(data);tmp.replace(dest)
    return manifest
