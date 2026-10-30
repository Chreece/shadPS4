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
    "lds": "src/shader_recompiler/frontend/ordered_gds_lds_checkpoint.h",
    "transfer": "src/video_core/renderer_vulkan/vk_ordered_gds_transfer.h",
    "rasterizer": "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
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
    lds = source("lds")
    transfer = source("transfer")
    rasterizer = source("rasterizer")
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
    require("OPCODE(OrderedGdsCheckpointRegister, Void, U32, U32, U32, U32)" in codes and
            "void OrderedGdsCheckpointRegister(" in emit_h and
            "void IREmitter::OrderedGdsCheckpointRegister(" in emit_cpp and
            "void EmitOrderedGdsCheckpointRegister(" in backend_h and
            "void EmitOrderedGdsCheckpointRegister(" in backend_cpp and
            "RecordOrderedGdsCheckpoint(site_pc, exec_mask)" in body and
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
    require("GcnLdsCheckpointLayout" in lds and
            "RequireCompleteGcnLdsCheckpoints(" in lds and
            "waves_at_barrier != layout.waves_per_group" in lds and
            "LdsCheckpointQuiescentFlag" in lds and
            "OrderedGdsLdsStateTransfer::Download(" in rasterizer and
            "OrderedGdsLdsStateTransfer::Upload(" in rasterizer and
            "u64(lds_layout.workgroups) * lds_layout.waves_per_group != guest_created_waves" in rasterizer and
            "class OrderedGdsLdsStateTransfer" in transfer and
            "dstAccessMask = vk::AccessFlagBits2::eShaderStorageRead" in transfer,
            "PRODUCTION_GCN_LDS_STATE_TRANSFER_AND_GUARD")

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
