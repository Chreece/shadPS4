#!/usr/bin/env python3
"""Check the source contracts for staged ordered GDS without starting a GPU.

This is an additional check to the compiled CPU reference tests. Source shape
checks do not establish correctness of Vulkan scheduling or native build.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "info": "src/shader_recompiler/info.h",
    "opcodes": "src/shader_recompiler/ir/opcodes.inc",
    "emitter_h": "src/shader_recompiler/ir/ir_emitter.h",
    "emitter_cpp": "src/shader_recompiler/ir/ir_emitter.cpp",
    "translator_h": "src/shader_recompiler/frontend/translate/translate.h",
    "translator_cpp": "src/shader_recompiler/frontend/translate/data_share.cpp",
    "spirv_h": "src/shader_recompiler/backend/spirv/emit_spirv_instructions.h",
    "spirv_cpp": "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
    "atomic_cpp": "src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp",
    "inst": "src/shader_recompiler/ir/microinstruction.cpp",
    "reference": "src/shader_recompiler/frontend/ordered_gds.h",
    "resources": "src/shader_recompiler/ir/passes/resource_pass.h",
    "patching": "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "packer": "src/shader_recompiler/frontend/ordered_gds_legacy_packer.h",
    "checkpoint": "src/shader_recompiler/frontend/ordered_gds_checkpoint.h",
    "word_layout": "src/shader_recompiler/frontend/ordered_gds_word_layout.h",
    "device_plan": "src/shader_recompiler/frontend/ordered_gds_device_plan.h",
    "auth": "src/shader_recompiler/frontend/ordered_gds_epoch_auth.h",
    "post_ds_state": "src/shader_recompiler/frontend/ordered_gds_resume_state.h",
    "backend_context": "src/shader_recompiler/backend/spirv/spirv_emit_context.cpp",
    "backend_context_h": "src/shader_recompiler/backend/spirv/spirv_emit_context.h",
    "resource_h": "src/shader_recompiler/resource.h",
    "lds": "src/shader_recompiler/frontend/ordered_gds_lds_checkpoint.h",
    "transfer": "src/video_core/renderer_vulkan/vk_ordered_gds_transfer.h",
    "rasterizer": "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "pipeline": "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "coverage": "src/shader_recompiler/frontend/ordered_gds_capture_coverage.h",
    "packet": "src/shader_recompiler/frontend/ordered_gds_capture_packet.h",
    "tests": "tests/gcn_ordered_gds_complete_batch.cpp",
}

def source(which):
    file = ROOT / FILES[which]
    if not file.is_file() or file.is_symlink():
        raise AssertionError(f"MISSING_OR_SYMLINK_SOURCE={file}")
    return file.read_text()

def require(ok, name):
    if not ok:
        raise AssertionError(name)
    print("PASS " + name)

def function(text, signature):
    if text.count(signature) != 1:
        raise AssertionError("NON_UNIQUE_FUNCTION_" + signature)
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    i = brace + 1
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    if depth:
        raise AssertionError("UNTERMINATED_" + signature)
    return text[start:i]

def main():
    info = source("info")
    codes = source("opcodes")
    emit_h = source("emitter_h")
    emit_cpp = source("emitter_cpp")
    translate_h = source("translator_h")
    translate_cpp = source("translator_cpp")
    backend_h = source("spirv_h")
    backend_cpp = source("spirv_cpp")
    atomic_cpp = source("atomic_cpp")
    inst = source("inst")
    reference = source("reference")
    resources = source("resources")
    patching = source("patching")
    packer = source("packer")
    checkpoint = source("checkpoint")
    word_layout = source("word_layout")
    device_plan = source("device_plan")
    auth = source("auth")
    post_ds_state = source("post_ds_state")
    backend_context = source("backend_context")
    backend_context_h = source("backend_context_h")
    resource_h = source("resource_h")
    lds = source("lds")
    transfer = source("transfer")
    rasterizer = source("rasterizer")
    pipeline = source("pipeline")
    coverage = source("coverage")
    packet = source("packet")
    tests = source("tests")

    require(info.count("bool uses_ordered_gds{};") == 1 and
            "std::vector<u32> ordered_gds_site_pcs;" in info,
            "PRESERVE_SHADER_ORDERED_GDS_CAPABILITY_AND_SITE_TABLE")
    match = re.search(r"^OPCODE\(OrderedGdsCount,\s+U32,\s+U32,\s+U32,\s+U64,\s+U32,\s+U32\s*\)$",
                      codes, re.M)
    require(match is not None and codes.count("OPCODE(OrderedGdsCount") == 1,
            "TYPED_IR_M0_ADDR_EXEC_CONTROL")
    require("U32 OrderedGdsCount(const U32& m0, const U32& address," in emit_h,
            "IR_EMITTER_API")
    body = function(emit_cpp, "U32 IREmitter::OrderedGdsCount(")
    require("Inst<U32>(Opcode::OrderedGdsCount, m0, address, guest_exec_mask, encoded_control," in body,
            "LIVE_M0_ADDR_EXEC_AND_IMMEDIATE")
    require("void DS_ORDERED_COUNT(const GcnInst& inst);" in translate_h,
            "FRONTEND_DECLARATION")
    require("case Opcode::DS_ORDERED_COUNT:" in translate_cpp and
            "return DS_ORDERED_COUNT(inst);" in translate_cpp,
            "GCN_DATA_SHARE_CASE")
    body = function(translate_cpp, "void Translator::DS_ORDERED_COUNT(")
    require("info.uses_ordered_gds = true" in body and
            "ir.OrderedGdsCount(ir.GetM0(), GetSrc(inst.src[0]), exec_mask, control, site_pc)" in body,
            "GCN_TRANSLATOR_HANDS_LIVE_OPERANDS_TO_IR")
    require("offset0 | (offset1 << 8u)" in body and
            "SetDst(inst.dst[0], old_value)" in body,
            "GUEST_CONTROL_AND_RETURN_REGISTER")
    require("ir.Ballot(ir.GetExec())" in body,
            "FULL_64_BIT_GUEST_EXEC_FOR_CIK_POPCOUNT")
    require("OPCODE(OrderedGdsCheckpointRegister, Void, U32, U32, U32, U32, U32, U32)" in codes and
            "void OrderedGdsCheckpointRegister(" in emit_h and
            "void IREmitter::OrderedGdsCheckpointRegister(" in emit_cpp and
            "void EmitOrderedGdsCheckpointRegister(" in backend_h and
            "void EmitOrderedGdsCheckpointRegister(" in backend_cpp and
            "RecordOrderedGdsCheckpoint(site_pc, exec_mask, pc, inst.dst[0].code," in body and
            "case Opcode::OrderedGdsCheckpointRegister:" in
            function(inst, "bool Inst::MayHaveSideEffects() const noexcept"),
            "GCN_CAPTURE_IR_SIDE_EFFECTS_SURVIVE_DCE")
    live_body = function(translate_cpp, "void Translator::RecordOrderedGdsCheckpoint(")
    require("IR::NumScalarRegs" in live_body and
            "runtime_info.props.num_allocated_vgprs" in live_body and
            "ir.GetScalarReg<IR::U32>" in live_body and
            "ir.GetVectorReg<IR::U32>" in live_body and
            "ir.GetM0()" in live_body and
            "ir.GetVccLo()" in live_body and
            "ir.GetVccHi()" in live_body and
            "ir.GetScc()" in live_body and
            "ir.UnpackUint2x32(exec_mask)" in live_body and
            "ir.OrderedGdsCheckpointRegister(" in live_body and
            "runtime_info.hw_stage == HwStage::Compute" in live_body,
            "REAL_PRE_DS_GUEST_SGPR_VGPR_M0_VCC_SCC_EXEC_SNAPSHOT")
    # The GPU writer is no longer the old UNREACHABLE stub. It consumes
    # live SSA values at the real guest DS instruction, using physical
    # wave slots and logical lane IDs, never assigning guest creation rank.
    gpu_write = function(backend_cpp, "void EmitOrderedGdsCheckpointRegister(")
    require("ctx.OpStore(pointer, real_value);" in gpu_write and
            "Gds::CheckpointRelativeDword(vgprs, bank, index, 0)" in gpu_write and
            "Gds::CheckpointStrideDwords(vgprs)" in gpu_write and
            "ctx.has_ordered_gds_checkpoint_binding" in gpu_write and
            "ctx.OpIEqual(ctx.U1[1], lane, ctx.u32_zero_value)" in gpu_write and
            "ctx.OpBranchConditional(first_lane, write_label, merge_label)" in gpu_write and
            "ctx.OpIAdd(ctx.U32[1], dword_offset, lane)" in gpu_write and
            "ctx.OpAccessChain(pointer_type, storage, ctx.u32_zero_value, dword_offset)" in gpu_write and
            "ctx.AddLabel(merge_label)" in gpu_write and
            "std::vector" not in gpu_write,
            "REAL_SSA_GUEST_REGISTER_GPU_SSBO_WRITER")
    # Stage genuine decoded-site header fields through the same typed IR and
    # SPIR-V SSBO writer, but deliberately do not claim authenticated epoch,
    # continuation token or a resumed guest shader.
    require("CheckpointRegisterBank::SiteMetadata" in word_layout and
            "offsetof(GpuGcnWaveCheckpoint, next_guest_pc)" in word_layout and
            "offsetof(GpuGcnWaveCheckpoint, destination_vgpr)" in word_layout and
            "offsetof(GpuGcnWaveCheckpoint, logical_slot)" in word_layout and
            "offsetof(GpuGcnWaveCheckpoint, tag)" in word_layout and
            "offsetof(GpuGcnWaveCheckpoint, flags)" in word_layout and
            "capture(3, 1, site_pc)" in live_body and
            "capture(3, 2, ir.Imm32(next_guest_pc))" in live_body and
            "capture(3, 3, logical_slot)" in live_body and
            "capture(3, 4, ir.Imm32(destination_vgpr))" in live_body and
            "capture(3, 0, ir.Imm32(0u))" in live_body and
            "capture(3, 8, ir.Imm32(0u))" in live_body and
            "SiteMetadataCount" in coverage and
            "incomplete decoded-site checkpoint header after IR passes" in coverage and
            "std::array<u32, 11>" in word_layout and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "REAL_GCN_SITE_CHECKPOINT_METADATA_UNAUTHENTICATED_AND_FAIL_CLOSED")
    # The 16-dword packet is written on the real GPU from pre-DS guest
    # SSA registers and decoded ISA, not a synthetic host counter value.
    # Tag/epoch/token/flags stay zero: no authentication claim without
    # a synchronized dispatch and genuine resumable shader.
    capture_fields = (
        "capture(4, 0, ir.Imm32(0u))", "capture(4, 1, ir.Imm32(0u))",
        "capture(4, 2, site_pc)", "capture(4, 3, ir.Imm32(encoded_control))",
        "capture(4, 4, logical_slot)", "capture(4, 5, ir.GetM0())",
        "capture(4, 6, IR::U32{ir.CompositeExtract(exec_halves, 0)})",
        "capture(4, 7, IR::U32{ir.CompositeExtract(exec_halves, 1)})",
        "capture(4, 8, source_lane)", "capture(4, 9, cik_payload)",
        "capture(4, 10, ir.Imm32(0u))", "capture(4, 11, ir.Imm32(0u))",
        "capture(4, 12, ir.Imm32(0u))", "capture(4, 13, ir.Imm32(0u))",
        "capture(4, 14, ir.Imm32(0u))", "capture(4, 15, ir.Imm32(0u))",
    )
    require(all(field in live_body for field in capture_fields) and
            live_body.count("capture(4, ") == 16 and
            "ir.FindILsb(exec_mask)" in live_body and
            "ir.BitCount(exec_mask)" in live_body and
            "OrderedGdsCapture," in resource_h and
            "BufferType::OrderedGdsCapture" in patching and
            "NUM_BUFFERS - 3" in patching and
            "BufferType::OrderedGdsCapture" in backend_context and
            "has_ordered_gds_capture_binding" in backend_context and
            "GpuCapturePacketDwordOffset(0, index)" in gpu_write and
            "Gds::GpuCapturePacketDwords" in gpu_write and
            "ctx.ordered_gds_capture_index" in gpu_write and
            "BufferType::OrderedGdsCapture" in gpu_write and
            "desc.buffer_type == Shader::BufferType::OrderedGdsCapture" in rasterizer and
            "packets.Handle() != buffer_cache.GetGdsBuffer()->Handle()" in rasterizer and
            "runtime.IsBufferAccessed(&packets, 0, bytes, true)" in rasterizer and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "REAL_GCN_GPU_CAPTURE_PACKET_BODY_FROM_LIVE_ISA_FAIL_CLOSED")
    # The host alone supplies a unique, nonzero token for each physical
    # storage slot in a specific epoch. SPIR-V loads these from a third,
    # read-only device buffer, and the CPU checks the GPU packet against
    # the exact host token table BEFORE any ordered-counter calculation.
    # This is authorization TRANSPORT, not guest execution or packer seed.
    require("struct alignas(16) GpuGcnOrderedEpochAuth" in auth and
            "RequireGcnEpochAuth(" in auth and
            "RequireCaptureTokenAuthority(" in auth and
            "missing or duplicate guest continuation token" in auth and
            "GPU continuation token or dispatch epoch disagrees" in auth and
            "epoch_auth_bytes" in device_plan and
            "capture_auth{instance, 0, plan.epoch_auth_bytes" in transfer and
            "PrepareEpochAuthority(Runtime& runtime, Scheduler& scheduler" in transfer and
            "RequireGcnEpochAuth(plan.waves, epoch, tokens_by_slot)" in transfer and
            "auth_storage" in gpu_write and
            "ctx.ordered_gds_auth_index" in gpu_write and
            "GcnEpochAuthTag" in gpu_write and
            "GcnEpochAuthFlags" in gpu_write and
            "ctx.OpSelect(ctx.U32[1], valid, value, ctx.u32_zero_value)" in gpu_write and
            "CheckpointRegisterBank::AuthenticatedIdentity" in word_layout and
            "CheckpointIdentityDwords == std::array<u32, 3>{1, 8, 9}" in word_layout and
            "capture(5, 0, ir.Imm32(0u))" in live_body and
            "capture(5, 1, ir.Imm32(0u))" in live_body and
            "capture(5, 2, ir.Imm32(0u))" in live_body and
            "AuthIdentityCount = 3" in coverage and
            "incomplete host epoch/token identity after GCN SSA passes" in coverage and
            "BufferType::OrderedGdsEpochAuth" in patching and
            "has_ordered_gds_auth_binding" in backend_context and
            "OrderedGdsEpochAuth" in resource_h and
            "desc.buffer_type == Shader::BufferType::OrderedGdsEpochAuth" in rasterizer and
            "active_ordered_gds_capture_->AuthorizedEpoch() != 0" in rasterizer and
            "RequireCaptureTokenAuthority(captures, authority, epoch)" in rasterizer and
            "owned.CaptureAuthority()" in rasterizer and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "TRUSTED_DISPATCH_EPOCH_TOKEN_GPU_TRANSPORT_STILL_FAIL_CLOSED")
    require("enum class CheckpointRegisterBank : u32" in word_layout and
            "case 0: return 5;" in word_layout and
            "case 1: return 16;" in word_layout and
            "case 2: return 17;" in word_layout and
            "case 3: return 18;" in word_layout and
            "case 4: return 6;" in word_layout and
            "case 5: return 7;" in word_layout and
            "CheckpointHeaderDwords + CheckpointScalarDwords +" in word_layout and
            "IR::NumScalarRegs" in word_layout and
            "GcnWaveLanes" in word_layout,
            "GUEST_CHECKPOINT_GPU_HOST_DWORD_LAYOUT_MATCHES")
    require("OrderedGdsCheckpoint," in resource_h and
            "desc.buffer_type == BufferType::OrderedGdsCheckpoint" in backend_context and
            "has_ordered_gds_checkpoint_binding = true" in backend_context and
            "BufferType::OrderedGdsCheckpoint" in patching and
            "OrderedGdsCheckpoint" in rasterizer and
            "active_ordered_gds_capture_" in rasterizer and
            "checkpoint.Handle() != buffer_cache.GetGdsBuffer()->Handle()" in rasterizer and
            "bound_buffers.emplace_back(&checkpoint, 0" in rasterizer,
            "GUEST_CHECKPOINT_DESCRIPTOR_SEPARATE_AND_RUNTIME_GATED")
    require("ir.GetAttributeU32(IR::Attribute::LocalInvocationIndex)" in live_body and
            "ir.GetAttributeU32(IR::Attribute::WorkgroupIndex)" in live_body and
            "ir.IMod(local_index, ir.Imm32(64u))" in live_body and
            "ir.IDiv(local_index, ir.Imm32(64u))" in live_body and
            "value, logical_slot, lane" in live_body,
            "REAL_GUEST_WAVE64_LOGICAL_ADDRESS_PASSED_IN_IR")

    require("class LiveCheckpointCoverage" in coverage and
            "incomplete live SGPR snapshot after IR passes" in coverage and
            "incomplete live VGPR snapshot after IR passes" in coverage and
            "incomplete EXEC/M0/VCC/SCC snapshot after IR passes" in coverage and
            "incomplete decoded-site checkpoint header after IR passes" in coverage and
            "incomplete host epoch/token identity after GCN SSA passes" in coverage and
            "LiveCheckpointCoverage coverage(" in pipeline and
            "ir_inst.GetOpcode() != Shader::IR::Opcode::OrderedGdsCheckpointRegister" in pipeline and
            "coverage.Record(site.U32(), bank.U32(), index.U32())" in pipeline and
            "coverage.RequireComplete()" in pipeline and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "POST_SSA_GCN_REGISTER_COVERAGE_AUDITED")
    require("pc - inst.length" in body and "sites.push_back(guest_site_pc)" in body and
            "ir.Imm32(guest_site_pc)" in body,
            "UNIQUE_GUEST_ORDERED_GDS_SITE_PC")
    require("Id EmitOrderedGdsCount(" in backend_h,
            "SPIRV_OPCODE_LINKAGE_DECLARATION")
    require("Id EmitOrderedGdsCount(" not in atomic_cpp,
            "NO_DUPLICATE_SPIRV_ORDERED_GDS_SYMBOL")
    body = function(backend_cpp, "Id EmitOrderedGdsCount(")
    require("UNREACHABLE_MSG(" in body and
            "wave-creation-order" in body and
            not any(bad in body for bad in ("OpAtomicIAdd", "OpAtomicExchange",
                                            "SharedAtomicIAdd", "ctx.OpStore")),
            "UNORDERED_GPU_ATOMICS_REJECTED")
    side = function(inst, "bool Inst::MayHaveSideEffects() const noexcept")
    require("case Opcode::OrderedGdsCount:" in side,
            "ATOMIC_SIDE_EFFECT_PRESERVED_FROM_DCE")
    # AMD CIK documents DS_ORDERED_COUNT as using dedicated ordered-count
    # packer registers, which are NOT verified as aliases of ordinary 64 KiB
    # GDS RAM. Only ordinary append/consume and other GDS operations may
    # demand BufferType::GdsBuffer automatically. Ordered IR is retained.
    resource_kind = function(resources, "inline bool IsDataRingInstruction(")
    require("case IR::Opcode::OrderedGdsCount:" not in resource_kind and
            "case IR::Opcode::DataAppend:" in resource_kind and
            "case IR::Opcode::DataConsume:" in resource_kind,
            "CIK_ORDERED_PACKERS_NOT_ALIASED_TO_RAW_GDS_RAM")
    patch = function(patching, "void PatchGlobalDataShareAccess(")
    require("if (inst.GetOpcode() == IR::Opcode::OrderedGdsCount)" not in patch and
            ".buffer_type = BufferType::GdsBuffer" in patch and
            "ir.BufferAtomicIAdd" in patch,
            "NORMAL_GDS_STILL_BINDS_WITHOUT_ORDERED_ALIAS")
    # The unverified origin is an enum declaration, not necessarily a
    # fully-qualified expression. Check the real fail-closed seed guard.
    seed_guard = function(packer, "MakeExplicitOrderedCounterSeed(")
    require("CikOrderedCountIncrement" in packer and
            "std::popcount" in packer and
            re.search(r"enum class OrderedCounterSeedOrigin\s*\{[^}]*\bUnverifiedRawGdsMemory\b",
                      packer, re.S) is not None and
            "origin != OrderedCounterSeedOrigin::DedicatedPackerState" in seed_guard and
            "throw std::invalid_argument" in seed_guard,
            "LEGACY_PACKER_PAYLOAD_AND_SEED_PROVENANCE")
    require("CikOrderedCountIncrement(guest_exec_mask)" in packet and
            "packet.payload != guest_cik_payload" in packet and
            ".payload = guest_cik_payload" in packet and
            "ordered_gds_legacy_packer.h" in packet,
            "CIK_GPU_PACKET_EXEC_POPCOUNT_NOT_FIRST_LANE_ADDR")

    require("FromCompleteCikComputeM0" in packet and
            "packet.runtime_m0 & 0x1FFFu" in packet and
            "wave_crawler_increment != 0" in packet and
            "packet.flags != CaptureFlags" in packet and
            "rank_by_slot[packet.logical_slot] != InvalidCreationRank" in packet,
            "CIK_COMPUTE_WAVE_ORDER_FROM_LIVE_GUEST_M0_ONLY")

    require("info.ordered_gds_sites.push_back({" in translate_cpp and
            "inst.dst[0].field == OperandField::VectorGPR" in translate_cpp and
            "std::vector<OrderedGdsSite> ordered_gds_sites;" in info,
            "INDEPENDENT_GCN_VDST_AND_CONTROL_CAPTURE_METADATA")
    require("RequireCompleteGcnWaveCheckpoints(" in checkpoint and
            "checkpoint.destination_vgpr != match->destination_vgpr" in checkpoint and
            "checkpoint.next_guest_pc != checkpoint.site_pc + 8" in checkpoint and
            "shared_lds_bytes" in checkpoint,
            "GUEST_REGISTERS_CHECKPOINT_GATED_BY_DECODED_GCN_SITE")
    require("GcnCheckpointAbiVersion = 2u" in checkpoint and
            "static_assert(sizeof(GpuGcnWaveCheckpoint) == 80)" in checkpoint and
            "checkpoint.scc > 1" in checkpoint and
            "checkpoint.abi_version != GcnCheckpointAbiVersion" in checkpoint and
            "RequireResolvedGcnResumeHandoff(" in checkpoint and
            "guest_rank != order.RankForSlot(capture.logical_slot)" in checkpoint,
            "GCN_VCC_SCC_AND_RESUME_WAVE_IDENTITY_VERIFIED")
    # Source changed from five individual pair checks to a single complete
    # pairwise check covering all six source/result buffers. Validate the
    # real invariant, not the obsolete spelling of one comparison.
    bridge = function(rasterizer, "Rasterizer::ResolveCompleteCikComputeOrderedGdsEpoch(")
    handle_set = re.search(r"const std::array handles\s*\{([^}]+)\};", bridge, re.S)
    required_handles = (
        "device_capture.Handle()", "device_checkpoints.Handle()",
        "device_lds.Handle()", "device_resume.Handle()",
        "device_resume_checkpoints.Handle()", "device_resume_lds.Handle()",
    )
    unique_handles = handle_set is not None and all(
        handle_set.group(1).count(name) == 1 for name in required_handles
    ) and handle_set.group(1).count(".Handle()") == len(required_handles)
    require("static void UploadCheckpoint(" in transfer and
            "copy_to_compute" in transfer and
            "guest checkpoint resume upload range invalid" in transfer and
            "RequireResolvedGcnResumeHandoff(resume, captures, *order, epoch)" in bridge and
            "OrderedGdsPacketTransfer::UploadCheckpoint(" in bridge and
            unique_handles and
            "for (std::size_t i = 0; i < handles.size(); ++i)" in bridge and
            "for (std::size_t j = i + 1; j < handles.size(); ++j)" in bridge and
            "if (handles[i] == handles[j])" in bridge and
            "throw std::invalid_argument(\"ordered GDS GPU checkpoint buffers alias\")"
            in function(bridge, "if (handles[i] == handles[j])"),
            "PRODUCTION_GPU_REGISTER_STATE_RESUME_UPLOAD_GUARDED")
    # After validated host counter ordering, the real second-pass guest
    # register image must have its old counter result in the actual DECODED
    # VDST for EXEC-active lanes; never replay the original DS instruction.
    # The original GPU checkpoint and all inactive/other registers stay
    # byte-identical, with neither a fabricated seed nor shader reentry.
    require("MaterializeGcnPostOrderedCountState(" in post_ds_state and
            "RequireCompleteGcnWaveCheckpoints(" in post_ds_state and
            "RequireResolvedGcnResumeHandoff(" in post_ds_state and
            "match->destination_vgpr" in post_ds_state and
            "result.previous" in post_ds_state and
            "if (((exec >> lane) & 1u) == 0)" in post_ds_state and
            "std::memcpy(post.data() + byte_offset" in post_ds_state and
            "const auto post_ds_state = MaterializeGcnPostOrderedCountState(" in bridge and
            "post_ds_state.data(), post_ds_state.size()" in bridge and
            "RequireCaptureTokenAuthority(captures, authority, epoch)" in bridge and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "REAL_POST_DS_GCN_RESOLVED_VDST_STATE_PREPARED_NO_GUEST_REENTRY")
    require("GcnLdsCheckpointLayout" in lds and
            "RequireCompleteGcnLdsCheckpoints(" in lds and
            "waves_at_barrier != layout.waves_per_group" in lds and
            "LdsCheckpointQuiescentFlag" in lds and
            "OrderedGdsLdsStateTransfer::Download(" in rasterizer and
            "OrderedGdsLdsStateTransfer::Upload(" in rasterizer and
            "RequireOptionalGcnLdsCheckpoint(" in lds and
            "guest_shared_lds_bytes == 0" in lds and
            "candidate.workgroups || candidate.waves_per_group ||" in lds and
            "std::uint64_t(verified.workgroups) * verified.waves_per_group !=" in lds and
            "const auto checked_lds = RequireOptionalGcnLdsCheckpoint(" in rasterizer and
            "if (!checked_lds && (lds_capture_offset || resume_lds_offset))" in rasterizer and
            "if (checked_lds)" in rasterizer and
            "lds_state = OrderedGdsLdsStateTransfer::Download(" in rasterizer and
            "*checked_lds, epoch" in rasterizer and
            "class OrderedGdsLdsStateTransfer" in transfer and
            "dstAccessMask = vk::AccessFlagBits2::eShaderStorageRead" in transfer,
            "PRODUCTION_GCN_LDS_STATE_TRANSFER_AND_GUARD")

    # A real GPU epoch owner must validate every guest-controlled size
    # BEFORE allocating independent packet, SGPR/VGPR and optional LDS buffers.
    # The native adapter must never infer a guest creation rank from slots,
    # use guest GDS RAM as a checkpoint, or make zero LDS allocate fake storage.
    require("struct GcnOrderedGdsDevicePlan" in device_plan and
            "GcnCheckpointLayout::Require(" in device_plan and
            "DispatchLayout::Create(" in device_plan and
            "GcnLdsCheckpointLayout::Require(" in device_plan and
            "(threads % GcnWaveLanes) != 0" in device_plan and
            "combined > MaxEpochDeviceBytes" in device_plan and
            "class OrderedGdsEpochDeviceBuffers" in transfer and
            "plan{Plan::Require(" in transfer and
            "capture_packets{instance, 0, plan.packet_bytes" in transfer and
            "resume_packets{instance, 0, plan.packet_bytes" in transfer and
            "capture_registers{instance, 0, plan.register_layout.total_bytes" in transfer and
            "resume_registers{instance, 0, plan.register_layout.total_bytes" in transfer and
            "if (plan.lds_layout)" in transfer and
            "Rasterizer::AllocateOrderedGdsEpochBuffers(" in rasterizer and
            "Rasterizer::ResolveOwnedCikComputeOrderedGdsEpoch(" in rasterizer and
            "owned.capture_registers" in rasterizer and
            "owned.resume_registers" in rasterizer and
            "owned.capture_packets" in rasterizer and
            "owned.resume_packets" in rasterizer and
            "plan.lds_layout.value_or(" in rasterizer and
            "OrderedGdsCaptureBinding::OrderedGdsCaptureBinding(" in rasterizer and
            "OrderedGdsCaptureBinding::~OrderedGdsCaptureBinding()" in rasterizer and
            "active_ordered_gds_capture_ = &epoch" in rasterizer and
            "active_ordered_gds_capture_ = nullptr" in rasterizer and
            "ASSERT_MSG(!program.info.uses_ordered_gds" in backend_cpp,
            "REAL_GPU_DISPATCH_OWNED_GDS_RESOURCES_BOUNDED_AND_RUNTIME_GATED")

    require("class OrderedGdsPhases" in reference and
            "CompleteWaveCollector" in reference and
            "wrong ordered-count phase" in reference and
            "created_waves_" in reference,
            "TWO_SITE_ORDERED_GDS_PHASE_COLLECTOR")
    require("OrderedGdsPhases phases(n, {17, 19}" in tests and
            "OrderedGdsPhases repeated(1, {17, 17}" in tests,
            "TWO_SITES_AND_REPEAT_EXECUTION_TESTED")
    combined = "\n".join((info, codes, emit_cpp, translate_cpp, backend_cpp, reference))
    require(not any(x in combined for x in ("CUSA34384", "0xdbaa6ae4",
                                            "skip_gds", "DynamoRIO")),
            "NO_TITLE_HASH_OR_OPCODE_SKIP_HACK")
    print("ORDERED_GDS_TYPED_IR_CONTRACT=PASS; GPU_RUNTIME_NOT_IMPLEMENTED")

if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError) as exc:
        print("ORDERED_GDS_TYPED_IR_CONTRACT=FAIL " + str(exc),
              file=sys.stderr)
        raise SystemExit(1)
