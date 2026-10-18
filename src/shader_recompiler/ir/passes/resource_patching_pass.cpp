// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdlib>
#include <vector>
#include <bit>
#include <unordered_map>

#include <limits>
#include "shader_recompiler/info.h"
#include "shader_recompiler/ir/basic_block.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/sharp_fetch_coalesce.h"
#include "shader_recompiler/ir/opcodes.h"
#include "shader_recompiler/ir/operand_helper.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "shader_recompiler/ir/passes/resource_pass.h"
#include "shader_recompiler/ir/reinterpret.h"
#include "shader_recompiler/profile.h"
#include "shader_recompiler/resource.h"
#include "shader_recompiler/guest_tsharp_array.h"
#include "video_core/amdgpu/resource.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"

namespace Shader::Optimization {
namespace {

IR::Type BufferDataType(const IR::Inst& inst, const Profile& profile) {
    switch (inst.GetOpcode()) {
    case IR::Opcode::LoadBufferU8:
    case IR::Opcode::StoreBufferU8:
        return IR::Type::U8;
    case IR::Opcode::LoadBufferU16:
    case IR::Opcode::StoreBufferU16:
        return IR::Type::U16;
    case IR::Opcode::LoadBufferU64:
    case IR::Opcode::StoreBufferU64:
    case IR::Opcode::BufferAtomicIAdd64:
    case IR::Opcode::BufferAtomicSMax64:
    case IR::Opcode::BufferAtomicSMin64:
    case IR::Opcode::BufferAtomicUMax64:
    case IR::Opcode::BufferAtomicUMin64:
        return IR::Type::U64;
    case IR::Opcode::BufferAtomicFMax32:
    case IR::Opcode::BufferAtomicFMin32:
        return profile.supports_buffer_fp32_atomic_min_max ? IR::Type::F32 : IR::Type::U32;
    case IR::Opcode::LoadBufferFormatF32:
    case IR::Opcode::StoreBufferFormatF32:
        // Formatted buffer loads can use a variety of types.
        return IR::Type::U32 | IR::Type::F32 | IR::Type::U16 | IR::Type::U8;
    default:
        return IR::Type::U32;
    }
}

u32 BufferAddressShift(const IR::Inst& inst, AmdGpu::DataFormat data_format) {
    switch (inst.GetOpcode()) {
    case IR::Opcode::LoadBufferU8:
    case IR::Opcode::StoreBufferU8:
        return 0;
    case IR::Opcode::LoadBufferU16:
    case IR::Opcode::StoreBufferU16:
        return 1;
    case IR::Opcode::LoadBufferU64:
    case IR::Opcode::StoreBufferU64:
    case IR::Opcode::BufferAtomicIAdd64:
    case IR::Opcode::BufferAtomicSMax64:
    case IR::Opcode::BufferAtomicSMin64:
    case IR::Opcode::BufferAtomicUMax64:
    case IR::Opcode::BufferAtomicUMin64:
        return 3;
    case IR::Opcode::LoadBufferFormatF32:
    case IR::Opcode::StoreBufferFormatF32: {
        switch (data_format) {
        case AmdGpu::DataFormat::Format8:
            return 0;
        case AmdGpu::DataFormat::Format8_8:
        case AmdGpu::DataFormat::Format16:
            return 1;
        case AmdGpu::DataFormat::Format8_8_8_8:
        case AmdGpu::DataFormat::Format16_16:
        case AmdGpu::DataFormat::Format10_11_11:
        case AmdGpu::DataFormat::Format2_10_10_10:
        case AmdGpu::DataFormat::Format16_16_16_16:
        case AmdGpu::DataFormat::Format32:
        case AmdGpu::DataFormat::Format32_32:
        case AmdGpu::DataFormat::Format32_32_32:
        case AmdGpu::DataFormat::Format32_32_32_32:
            return 2;
        default:
            return 0;
        }
        break;
    }
    case IR::Opcode::ReadConstBuffer:
        // Provided address is already in dwords
        return 0;
    default:
        return 2;
    }
}

class Descriptors {
public:
    explicit Descriptors(Info& info_)
        : info{info_}, buffer_resources{info_.buffers}, image_resources{info_.images},
          sampler_resources{info_.samplers}, fmask_resources(info_.fmasks) {}

    u32 Add(const BufferResource& desc) {
        const u32 index{Add(buffer_resources, desc, [&desc](const auto& existing) {
            return desc.sharp_fetch == existing.sharp_fetch && desc.post_op == existing.post_op &&
                   desc.post_op_dw1_mask == existing.post_op_dw1_mask &&
                   desc.buffer_type == existing.buffer_type;
        })};
        auto& buffer = buffer_resources[index];
        buffer.used_types |= desc.used_types;
        buffer.is_written |= desc.is_written;
        buffer.is_formatted |= desc.is_formatted;
        return index;
    }

    u32 Add(const ImageResource& desc) {
        const u32 index{Add(image_resources, desc, [&desc](const auto& existing) {
            return desc.sharp_fetch == existing.sharp_fetch && desc.is_array == existing.is_array &&
                   desc.is_atomic_uint_view == existing.is_atomic_uint_view &&
                   desc.mip_fallback_mode == existing.mip_fallback_mode &&
                   desc.constant_mip_index == existing.constant_mip_index &&
                   desc.post_op == existing.post_op &&
                   desc.is_guest_indexed == existing.is_guest_indexed &&
                   desc.oversized_guest_table_probe == existing.oversized_guest_table_probe &&
                   desc.indexed_guest_entries == existing.indexed_guest_entries &&
                   desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes &&
                   desc.indexed_descriptor_offset_bytes == existing.indexed_descriptor_offset_bytes &&
                   desc.indexed_table_fetch == existing.indexed_table_fetch;
        })};
        auto& image = image_resources[index];
        image.is_atomic |= desc.is_atomic;
        image.is_written |= desc.is_written;
        return index;
    }

    u32 Add(const SamplerResource& desc) {
        const u32 index{Add(sampler_resources, desc, [this, &desc](const auto& existing) {
            return desc.sharp_fetch == existing.sharp_fetch && desc.post_op == existing.post_op &&
                   desc.post_op_tsharp_dw3_off == existing.post_op_tsharp_dw3_off &&
                   desc.is_depth == existing.is_depth &&
                   desc.is_integer == existing.is_integer &&
                   desc.is_guest_indexed == existing.is_guest_indexed &&
                   desc.indexed_guest_entries == existing.indexed_guest_entries &&
                   desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes &&
                   desc.indexed_sampler_offset_bytes == existing.indexed_sampler_offset_bytes &&
                   desc.indexed_table_fetch == existing.indexed_table_fetch;
        })};
        return index;
    }

    u32 Add(const FMaskResource& desc) {
        u32 index = Add(fmask_resources, desc, [&desc](const auto& existing) {
            return desc.sharp_idx == existing.sharp_idx;
        });
        return index;
    }

private:
    template <typename Descriptors, typename Descriptor, typename Func>
    static u32 Add(Descriptors& descriptors, const Descriptor& desc, Func&& pred) {
        const auto it{std::ranges::find_if(descriptors, pred)};
        if (it != descriptors.end()) {
            return static_cast<u32>(std::distance(descriptors.begin(), it));
        }
        descriptors.push_back(desc);
        return static_cast<u32>(descriptors.size()) - 1;
    }

    const Info& info;
    BufferResourceList& buffer_resources;
    ImageResourceList& image_resources;
    SamplerResourceList& sampler_resources;
    FMaskResourceList& fmask_resources;
};

} // Anonymous namespace

using SharpSources = boost::container::small_vector<const IR::Inst*, 4>;

static bool SrtSourceAuditEnabled() {
    static const bool enabled = [] {
        const char* const flag = std::getenv("SHADPS4_SRT_SOURCE_AUDIT");
        return flag && flag[0] == '1' && flag[1] == '\0';
    }();
    return enabled;
}

SharpLocation SharpLocationFromSource(const IR::Inst* inst, const Info& info, u32 word) {
    SharpLocation location{};
    if (inst->GetOpcode() == IR::Opcode::GetUserData) {
        return static_cast<SharpLocation>(inst->Arg(0).ScalarReg());
    } else if (inst->GetOpcode() == IR::Opcode::ReadConstBuffer) {
        location = inst->Flags<IR::BufferInstInfo>().flatbuf_off_dw;
    } else {
        location = inst->Flags<SharpLocation>();
    }
    if (location == 0) {
        if (SrtSourceAuditEnabled()) {
            const auto arg = inst->NumArgs() > 1 ? inst->Arg(1) : IR::Value{};
            const IR::Inst* arg_inst = arg.TryInst();
            LOG_WARNING(Render_Recompiler,
                        "SrtSourceAudit: shader={:#x} stage={} dword={} producer={} "
                        "flattened_offset={} index_immediate={} index_value={} index_opcode={} "
                        "dma={}",
                        info.pgm_hash, info.hw_stage, word, inst->GetOpcode(), u32(location),
                        arg.IsImmediate(), arg.IsImmediate() && arg.Type() == IR::Type::U32
                                               ? arg.U32() : 0u,
                        arg_inst ? arg_inst->GetOpcode() : IR::Opcode::Phi,
                        info.uses_dma);
        }
        // Repeated shader variants previously emitted tens of thousands of
        // identical warnings per run. Preserve early and exponential
        // occurrences with stage/hash provenance; no resource semantics change.
        static thread_local std::unordered_map<u64,u64> missing_counts;
        const u64 key=(static_cast<u64>(info.pgm_hash)<<8) |
                      static_cast<u8>(info.hw_stage);
        const u64 seen=++missing_counts[key];
        if (seen<=4 || std::has_single_bit(seen)) {
            LOG_WARNING(Render_Recompiler,
                        "Sharp source was not flatenned shader={:#x} stage={} "
                        "occurrences={}",info.pgm_hash,info.hw_stage,seen);
        }
        return UNKNOWN_LOCATION;
    }
    return location;
}

template <typename T>
SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp, const Info& info) {
    using Summary = SharpFetch<T>::Summary;
    SharpFetch<T> sharp_fetch{};
    for (u32 i = 0; i < sharp.num_dwords; i++) {
        auto dword = sharp.dwords[i];
        sharp_fetch.clear_masks[i] = sharp.clear_masks[i];
        if (dword.IsImmediate()) {
            sharp_fetch.immediates[i] = dword.U32();
        } else {
            sharp_fetch.offsets[i] = SharpLocationFromSource(dword.Inst(), info, i);
            sharp_fetch.load_mask |= (1 << i);
            if (sharp_fetch.offsets[i] == UNKNOWN_LOCATION) {
                sharp_fetch.summary = Summary::Invalid;
            }
        }
    }
    // Compile-time diagnostic only: measure whether the guest actually
    // masks a resource descriptor in any shader. Default disabled, bounded
    // to 128 source reports to avoid costly synchronous logging.
    static const bool audit_sharp_masks = [] {
        const char* const flag=std::getenv("SHADPS4_SHARP_MASK_AUDIT");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    static const auto audit_hashes = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_SHARP_MASK_AUDIT_HASHES"));
    if (audit_sharp_masks && audit_hashes.Matches(info.pgm_hash) &&
        !SharpMasksAreZero(sharp_fetch.clear_masks, sharp.num_dwords)) {
        static thread_local u32 reports=0;
        if (reports++ < 128) {
            u32 masked_dwords=0;
            for (u32 i=0;i<sharp.num_dwords;++i)
                masked_dwords += sharp_fetch.clear_masks[i]!=0;
            LOG_INFO(Render_Recompiler,
                     "SharpMaskPreserved: shader={:#x} stage={} descriptor_words={} "
                     "masked_dwords={} first_mask={:#x}",
                     info.pgm_hash,info.hw_stage,sharp.num_dwords,masked_dwords,
                     sharp_fetch.clear_masks[0]);
        }
    }
    // Coalesce only genuine consecutive flat-buffer loads. In particular,
    // partially-immediate T#/S#/V# descriptors must retain their immediate
    // words: offsets belonging to those words are not meaningful, and can
    // accidentally form a consecutive sequence with the other offsets.
    if (sharp_fetch.summary != Summary::Invalid) {
        if (CanCoalesceSharpDwords(sharp_fetch.load_mask, sharp_fetch.offsets,
                                   sharp.num_dwords) &&
            SharpMasksAreZero(sharp_fetch.clear_masks, sharp.num_dwords)) {
            sharp_fetch.summary = Summary::SingleLoad;
        } else {
            // Opt-in proof of when the previous offsets-only fast path
            // discarded immediate T#/S#/V# descriptor words. Read-only.
            static const bool audit = [] {
                const char* value = std::getenv("SHADPS4_SHARP_MIXED_AUDIT");
                return value && value[0] == '1' && value[1] == '\0';
            }();
            if (audit && sharp.num_dwords > 0 &&
                sharp.num_dwords <= SharpFetch<T>::N) {
                bool old_would_coalesce = true;
                const u32 base = sharp_fetch.offsets[0];
                for (u32 i = 1; i < sharp.num_dwords; ++i) {
                    if (u32(sharp_fetch.offsets[i] - base) != i) {
                        old_would_coalesce = false;
                        break;
                    }
                }
                if (old_would_coalesce) {
                    static thread_local u32 reported = 0;
                    if (reported++ < 96) {
                        LOG_INFO(Render_Recompiler,
                                 "SharpMixedCorrected: shader={:#x} stage={} "
                                 "descriptor_words={} source_mask={:#x}",
                                 info.pgm_hash, info.hw_stage,
                                 sharp.num_dwords, u32(sharp_fetch.load_mask));
                    }
                }
            }
        }
    }
    return sharp_fetch;
}

// Read-only provenance for invalid, real guest SHARP descriptors.
// A CSV hash filter confines this work to shaders the user is investigating;
// no runtime resource, codegen or descriptor value changes occur here.
static bool InvalidSharpTraceEnabled(const Info& info) {
    static const auto hash_filter = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_INVALID_SHARP_TRACE_HASHES"));
    return hash_filter.enabled && hash_filter.valid &&
           hash_filter.Matches(info.pgm_hash);
}

template <typename Fetch>
static u32 MissingSharpWordMask(const SharpReference& source, const Fetch& fetch) {
    u32 mask = 0;
    for (u32 i = 0; i < source.num_dwords; ++i) {
        if (!source.dwords[i].IsImmediate() && fetch.offsets[i] == UNKNOWN_LOCATION) {
            mask |= (1u << i);
        }
    }
    return mask;
}

static std::string_view SharpProducerName(const SharpReference& source, u32 word) {
    if (word >= source.num_dwords) return "not-present";
    const IR::Value value = source.dwords[word];
    if (value.IsImmediate()) return "immediate";
    const auto* const inst = value.TryInst();
    return inst ? IR::NameOf(inst->GetOpcode()) : "missing";
}

void PatchBufferSharp(const ResourceDiscovery& resource, Info& info, Descriptors& descriptors,
                      const Profile& profile) {
    IR::Inst& inst = *resource.user;

    const auto& source = resource.sharps[0];
    const auto fetch = ConstructSharpFetch<AmdGpu::Buffer>(source, info);
    if (InvalidSharpTraceEnabled(info) &&
        fetch.summary == SharpFetch<AmdGpu::Buffer>::Summary::Invalid) {
        static thread_local std::unordered_map<u64, u32> per_shader;
        const u64 shader_key = (static_cast<u64>(info.pgm_hash) << 8) |
                                static_cast<u8>(info.hw_stage);
        if (per_shader[shader_key]++ < 48) {
            const auto flags = inst.Flags<IR::BufferInstInfo>();
            LOG_INFO(Render_Recompiler,
                     "InvalidVSharpSource: shader={:#x} stage={} op={} "
                     "missing_mask={:#x} index_en={} voffset_en={} "
                     "dw0={} dw1={} dw2={} dw3={}",
                     info.pgm_hash, info.hw_stage, IR::NameOf(inst.GetOpcode()),
                     MissingSharpWordMask(source, fetch),
                     bool(flags.index_enable), bool(flags.voffset_enable),
                     SharpProducerName(source, 0), SharpProducerName(source, 1),
                     SharpProducerName(source, 2), SharpProducerName(source, 3));
        }
    }
    const u32 buffer_binding = descriptors.Add(BufferResource{
        .sharp_fetch = fetch,
        .used_types = BufferDataType(inst, profile),
        .buffer_type = BufferType::Guest,
        .is_written = IsBufferStore(inst),
        .is_formatted = inst.GetOpcode() == IR::Opcode::LoadBufferFormatF32 ||
                        inst.GetOpcode() == IR::Opcode::StoreBufferFormatF32,
        .post_op = resource.sharps[0].post_op,
        .post_op_dw1_mask = resource.sharps[0].post_op_data.dw1_mask,
        .post_op_dw2_shift = resource.sharps[0].post_op_data.dw2_shift,
    });

    // Replace handle with binding index in buffer resource list.
    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};
    inst.SetArg(0, ir.Imm32(buffer_binding));
}

void PatchImageSharp(const ResourceDiscovery& resource, Info& info, Descriptors& descriptors,
                     const Profile& profile, bool allow_strided_guest_tables) {
    IR::Inst& inst = *resource.user;

    // Read image sharp.
    const auto inst_info = inst.Flags<IR::TextureInstInfo>();
    const bool is_atomic = IsImageAtomicInstruction(inst);
    const bool is_written = inst.GetOpcode() == IR::Opcode::ImageWrite || is_atomic;
    // ImageRead with !is_written gets emitted as OpImageFetch with LOD operand, doesn't
    // need fallback (TODO is this 100% true?)
    const bool needs_mip_storage_fallback =
        inst_info.has_lod && is_written && !profile.supports_image_load_store_lod;
    const auto& source = resource.sharps[0];
    const auto fetch = ConstructSharpFetch<AmdGpu::Image>(source, info);
    if (InvalidSharpTraceEnabled(info) &&
        fetch.summary == SharpFetch<AmdGpu::Image>::Summary::Invalid) {
        static thread_local std::unordered_map<u64, u32> per_shader;
        const u64 shader_key = (static_cast<u64>(info.pgm_hash) << 8) |
                                static_cast<u8>(info.hw_stage);
        if (per_shader[shader_key]++ < 48) {
            LOG_INFO(Render_Recompiler,
                     "InvalidTSharpSource: shader={:#x} stage={} op={} "
                     "missing_mask={:#x} dw0={} dw1={} dw2={} dw3={} "
                     "dw4={} dw5={} dw6={} dw7={}",
                     info.pgm_hash, info.hw_stage, IR::NameOf(inst.GetOpcode()),
                     MissingSharpWordMask(source, fetch),
                     SharpProducerName(source, 0), SharpProducerName(source, 1),
                     SharpProducerName(source, 2), SharpProducerName(source, 3),
                     SharpProducerName(source, 4), SharpProducerName(source, 5),
                     SharpProducerName(source, 6), SharpProducerName(source, 7));
        }
    }
    ImageResource image_res = {
        .sharp_fetch = fetch,
        .is_depth = bool(inst_info.is_depth),
        .is_atomic = is_atomic,
        .is_array = bool(inst_info.is_array),
        .is_written = is_written,
        .is_r128 = bool(inst_info.is_r128),
        .post_op = resource.sharps[0].post_op,
    };

    // Only a fully represented *actual guest table* is eligible. Do not
    // truncate or guess the texture ID: an out-of-range GPU ID samples the
    // extra null slot in the sampled-image array.
    if (resource.has_indexed_table &&
        (resource.indexed_record_stride_bytes == 0u || allow_strided_guest_tables) &&
        profile.supports_sampled_image_nonuniform_indexing &&
        (resource.indexed_record_stride_bytes == 0u ||
         (resource.has_indexed_sampler_table &&
          profile.supports_sampler_array_nonuniform_indexing &&
          resource.sharps[1].post_op == SharpFetchPostOp::None)) &&
        !is_written && !inst_info.is_depth && !inst_info.is_array &&
        !inst_info.is_gather &&
        !inst_info.has_lod && !inst_info.force_level0 &&
        !inst_info.has_lod_clamp && !inst_info.is_r128 &&
        !inst_info.is_unnormalized) {
        const auto table_fetch =
            ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp, info);
        AmdGpu::Buffer table{};
        if (table_fetch.FetchChecked(info.flattened_ud_buf.data(),
                                    info.flattened_ud_buf.size(), &table) &&
            table.Valid() && table.base_address) {
            const u64 full_size = table.stride==0
                ? static_cast<u64>(table.num_records)
                : static_cast<u64>(table.stride)*table.num_records;
            const u32 record_stride=resource.indexed_record_stride_bytes;
            const u32 record_offset=resource.indexed_descriptor_offset_bytes;
            const u32 entries = record_stride != 0u
                ? CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    record_stride,record_offset,profile.max_guest_indexed_image_entries)
                : CompleteGuestTsharpTableEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    profile.max_guest_indexed_image_entries);
            if (entries != 0u) {
                image_res.indexed_table_fetch=table_fetch;
                image_res.indexed_guest_entries=entries;
                image_res.indexed_record_stride_bytes=record_stride;
                image_res.indexed_descriptor_offset_bytes=record_offset;
                image_res.is_guest_indexed=true;
                LOG_INFO(Render_Recompiler,
                         "RealIndexedTSharpEnabled: shader={:#x} stage={} entries={} "
                         "stride={} record_stride={} descriptor_offset={} "
                         "full_guest_table=YES",
                         info.pgm_hash, info.hw_stage, entries,
                         static_cast<u32>(table.stride),
                         record_stride,record_offset);
            } else {
                // Keep the real V# pointer available for a bounded, opt-in
                // runtime census. This does not mark the table as bound and
                // does not modify any image selection or Vulkan descriptors.
                image_res.indexed_table_fetch = table_fetch;
                image_res.oversized_guest_table_probe = true;
                LOG_INFO(Render_Recompiler,
                         "RealIndexedTSharpUnsupportedSize: shader={:#x} stage={} "
                         "entries={} full_size={}",
                         info.pgm_hash, info.hw_stage, entries, full_size);
            }
        }
    }

    auto image = image_res.GetSharp(info);
    ASSERT(image.GetType() != AmdGpu::ImageType::Invalid);

    // Integer image atomics require integer sampled types and texel pointers.
    // Reinterpret compatible 32-bit float/signed storage through an R32_UINT
    // view. Keep sampled descriptors separate to preserve numeric sampling.
    if (is_atomic && image.GetDataFmt() == AmdGpu::DataFormat::Format32) {
        const auto nfmt = image.GetNumberFmt();
        const bool is_float_min_max =
            inst.GetOpcode() == IR::Opcode::ImageAtomicFMin32 ||
            inst.GetOpcode() == IR::Opcode::ImageAtomicFMax32;
        const bool uses_native_float_atomic =
            is_float_min_max && profile.supports_image_fp32_atomic_min_max &&
            nfmt == AmdGpu::NumberFormat::Float;
        image_res.is_atomic_uint_view =
            !uses_native_float_atomic &&
            (nfmt == AmdGpu::NumberFormat::Float || nfmt == AmdGpu::NumberFormat::Sint);
    }

    if (needs_mip_storage_fallback) {
        // If the mip level to IMAGE_(LOAD/STORE)_MIP is a constant, set up ImageResource
        // so that we will only bind a single level.
        // If index is dynamic, we will bind levels as an array
        const auto view_type = image.GetViewType(image_res.is_array);

        IR::Inst* body = inst.Arg(1).Inst();
        const auto lod_arg = [&] -> IR::Value {
            switch (view_type) {
            case AmdGpu::ImageType::Color1D: // x, [lod]
                return body->Arg(1);
            case AmdGpu::ImageType::Color1DArray: // x, slice, [lod]
            case AmdGpu::ImageType::Color2D:      // x, y, [lod]
                return body->Arg(2);
            case AmdGpu::ImageType::Color2DArray: // x, y, slice, [lod]
            case AmdGpu::ImageType::Cube:         // x, y, face, [lod]
            case AmdGpu::ImageType::Color3D:      // x, y, z, [lod]
                return body->Arg(3);
            case AmdGpu::ImageType::Color2DMsaa:
            case AmdGpu::ImageType::Color2DMsaaArray:
            default:
                UNREACHABLE_MSG("Invalid image type {}", view_type);
            }
        }();

        if (lod_arg.IsImmediate()) {
            image_res.mip_fallback_mode = MipStorageFallbackMode::ConstantIndex;
            image_res.constant_mip_index = lod_arg.U32();
        } else {
            image_res.mip_fallback_mode = MipStorageFallbackMode::DynamicIndex;
        }
    }

    // Patch image instruction if image is FMask.
    if (AmdGpu::IsFmask(image.GetDataFmt())) {
        ASSERT_MSG(!is_written, "FMask storage instructions are not supported");

        IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};
        switch (inst.GetOpcode()) {
        case IR::Opcode::ImageRead:
        case IR::Opcode::ImageSampleRaw: {
            IR::F32 fmaskx = ir.BitCast<IR::F32>(ir.Imm32(0x76543210));
            IR::F32 fmasky = ir.BitCast<IR::F32>(ir.Imm32(0xfedcba98));
            inst.ReplaceUsesWith(ir.CompositeConstruct(fmaskx, fmasky));
            return;
        }
        case IR::Opcode::ImageQueryLod:
            inst.ReplaceUsesWith(ir.Imm32(1));
            return;
        case IR::Opcode::ImageQueryDimensions: {
            IR::Value dims = ir.CompositeConstruct(ir.Imm32(static_cast<u32>(image.width)),  // x
                                                   ir.Imm32(static_cast<u32>(image.height)), // y
                                                   ir.Imm32(1), ir.Imm32(1)); // depth, mip
            inst.ReplaceUsesWith(dims);

            // Track FMask resource to do specialization.
            descriptors.Add(FMaskResource{
                .sharp_idx = SharpLocationFromSource(resource.sharps[0].dwords[0].Inst(), info, 0),
            });
            return;
        }
        default:
            UNREACHABLE_MSG("Can't patch fmask instruction {}", inst.GetOpcode());
        }
    }

    u32 image_binding = descriptors.Add(image_res);

    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};

    if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw) {
        auto& lod_prod = resource.sharps[1].post_op_data.lod_prod;
        SamplerResource sampler_res{
            .sharp_fetch = ConstructSharpFetch<AmdGpu::Sampler>(resource.sharps[1], info),
            .post_op = resource.sharps[1].post_op,
            .post_op_tsharp_dw3_off =
                lod_prod.IsEmpty() ? UNKNOWN_LOCATION : SharpLocationFromSource(lod_prod.Inst(), info, 3),
            .is_depth = bool(inst_info.is_depth),
            .is_integer = AmdGpu::IsInteger(image.GetNumberFmt()),
        };
        if (image_res.is_guest_indexed &&
            image_res.indexed_record_stride_bytes != 0u) {
            // T# and S# use the SAME original GPU record selector, with
            // byte-accurate offsets. Do not bind default S# for a live table.
            ASSERT(resource.has_indexed_sampler_table);
            sampler_res.is_guest_indexed = true;
            sampler_res.indexed_table_fetch = image_res.indexed_table_fetch;
            sampler_res.indexed_guest_entries = image_res.indexed_guest_entries;
            sampler_res.indexed_record_stride_bytes =
                image_res.indexed_record_stride_bytes;
            sampler_res.indexed_sampler_offset_bytes =
                resource.indexed_sampler_offset_bytes;
        }
        const u32 sampler_binding = descriptors.Add(sampler_res);
        inst.SetArg(0, ir.Imm32(image_binding | sampler_binding << 16));
        if (image_res.is_guest_indexed) {
            // Discovery has already captured the original sampler descriptor.
            // Preserve the real GPU texture ID in the otherwise unused raw
            // sample Arg(1) until lowering creates Indexed SPIR-V sampling.
            inst.SetArg(1, resource.indexed_texture_id);
        }
    } else {
        inst.SetArg(0, ir.Imm32(image_binding));
    }
}

static IR::Value IsMbcntWithExec(IR::Value value, bool is_hi, IR::U1 exec) {
    if (value.IsImmediate()) {
        return {};
    }
    const IR::Inst* inst = value.Inst();
    if (inst->GetOpcode() != IR::Opcode::MaskedBitCount32 || inst->Arg(0).IsImmediate() ||
        inst->Arg(2).U1() != is_hi) {
        return {};
    }
    IR::Inst* prod = inst->Arg(0).Inst();
    if (prod->GetOpcode() != IR::Opcode::CompositeExtractU32x2 || prod->Arg(0).IsImmediate() ||
        prod->Arg(1).U32() != is_hi) {
        return {};
    }
    prod = prod->Arg(0).Inst();
    if (prod->GetOpcode() != IR::Opcode::UnpackUint2x32 || prod->Arg(0).IsImmediate()) {
        return {};
    }
    prod = prod->Arg(0).Inst();
    if (prod->GetOpcode() != IR::Opcode::Ballot || (prod->Arg(0) != exec && !is_hi)) {
        return {};
    }
    return inst->Arg(1);
}

static IR::Use FindUniqueUser(IR::Inst* inst, auto&& pred) {
    IR::Use picked{};
    for (auto& use : inst->Uses()) {
        if (pred(use.user)) {
            ASSERT(!picked.user);
            picked = use;
        }
    }
    ASSERT(picked.user);
    return picked;
}

static void RemoveAppendBufferLaneOffset(IR::Inst& vx) {
    // Attempt to detect either of the following patterns and
    // remove the lane id addition/subtraction to ds instruction.
    //
    // ds_append       vX gds
    // v_mbcnt_hi_u32_b32 vY, exec_hi, vX
    // v_mbcnt_lo_u32_b32 idx, exec_lo, vY
    //
    // v_mbcnt_hi_u32_b32 vY, exec_hi, 0
    // v_mbcnt_lo_u32_b32 vZ, exec_lo, vY
    // ds_append       vX gds
    // v_add_i32       idx, vcc, vX, vZ
    //
    // ds_consume      vX gds
    // v_mbcnt_hi_u32_b32 vY, exec_hi, 0
    // v_mbcnt_lo_u32_b32 vZ, exec_lo, vY
    // v_sub_i32       vK, vcc, vX, vZ
    // v_subrev_i32    idx, vcc, 1, vK / v_add_i32    idx, vcc, -1, vK
    //
    // ds_consume      vX gds
    // v_mbcnt_hi_u32_b32 vY, exec_hi, 0
    // v_mbcnt_lo_u32_b32 vZ, exec_lo, vY
    // v_add_i32       vK, vcc, -1, vX
    // v_sub_i32       idx, vcc, vK, vZ

    IR::U1 exec{vx.Arg(1)};
    const auto uses = vx.Uses();
    for (auto use : uses) {
        const auto& [user, operand] = use;
        if (user->GetOpcode() == IR::Opcode::MaskedBitCount32) {
            // First pattern
            ASSERT(vx.GetOpcode() == IR::Opcode::DataAppend);
            IR::Inst* vy = user;
            auto vx_2 = IsMbcntWithExec(IR::Value{vy}, true, exec);
            ASSERT(!vx_2.IsEmpty() && vx_2 == IR::Value{&vx});
            const auto [idx, operand] = FindUniqueUser(vy, [](const IR::Inst* inst) {
                return inst->GetOpcode() == IR::Opcode::MaskedBitCount32;
            });
            auto vy_2 = IsMbcntWithExec(IR::Value{idx}, false, exec);
            ASSERT(!vy_2.IsEmpty() && vy_2 == IR::Value{vy});
            idx->ReplaceUsesWithAndRemove(IR::Value{&vx});
            continue;
        }
        if (user->GetOpcode() != IR::Opcode::IAdd32 && user->GetOpcode() != IR::Opcode::ISub32) {
            continue;
        }
        const auto other = user->Arg(1 - operand);
        if (other.IsImmediate()) {
            ASSERT_MSG(other.U32() == 1u || other.U32() == std::numeric_limits<u32>::max() &&
                                                vx.GetOpcode() == IR::Opcode::DataConsume,
                       "Unexpected constant offset {} to DataConsume result", other.U32());
            use = FindUniqueUser(user, [](const IR::Inst* inst) {
                return inst->GetOpcode() == IR::Opcode::IAdd32 ||
                       inst->GetOpcode() == IR::Opcode::ISub32;
            });
        }

        // Other patterns
        auto vz = user->Arg(1 - operand);
        auto vy = IsMbcntWithExec(vz, false, exec);
        ASSERT(!vy.IsEmpty() && !vy.IsImmediate());
        auto zero_const = IsMbcntWithExec(vy, true, exec);
        ASSERT(!zero_const.IsEmpty() && zero_const.IsImmediate() && zero_const.U32() == 0u);
        user->SetArg(1 - operand, IR::Value{u32{0u}});
    }
}

void PatchGlobalDataShareAccess(IR::Inst& inst, Info& info, Descriptors& descriptors,
                                const Profile& profile) {
    const u32 binding = descriptors.Add(BufferResource{
        .used_types = IR::Type::U32,
        .buffer_type = BufferType::GdsBuffer,
        .is_written = true,
    });

    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};

    if (inst.GetOpcode() == IR::Opcode::DataAppend || inst.GetOpcode() == IR::Opcode::DataConsume) {
        RemoveAppendBufferLaneOffset(inst);
        const IR::Value replacement =
            inst.GetOpcode() == IR::Opcode::DataAppend
                ? ir.BufferAtomicIAdd(ir.Imm32(binding), inst.Arg(0), ir.Imm32(1u), {})
                : ir.BufferAtomicISub(ir.Imm32(binding), inst.Arg(0), ir.Imm32(1u), {});
        inst.ReplaceUsesWithAndRemove(replacement);
        return;
    }

    // Convert shared memory opcode to storage buffer atomic to GDS buffer.
    auto& buffer = info.buffers[binding];
    const IR::U32 offset = IR::U32{inst.Arg(0)};
    const IR::U32 address_words = ir.ShiftRightLogical(offset, ir.Imm32(1));
    const IR::U32 address_dwords = ir.ShiftRightLogical(offset, ir.Imm32(2));
    const IR::U32 address_qwords = ir.ShiftRightLogical(offset, ir.Imm32(3));
    const IR::U32 handle = ir.Imm32(binding);
    switch (inst.GetOpcode()) {
    case IR::Opcode::SharedAtomicIAdd32:
        inst.ReplaceUsesWith(ir.BufferAtomicIAdd(handle, address_dwords, inst.Arg(1), {}));
        break;
    case IR::Opcode::SharedAtomicIAdd64:
        inst.ReplaceUsesWith(ir.BufferAtomicIAdd(handle, address_qwords, IR::U64{inst.Arg(1)}, {}));
        break;
    case IR::Opcode::SharedAtomicISub32:
        inst.ReplaceUsesWith(ir.BufferAtomicISub(handle, address_dwords, inst.Arg(1), {}));
        break;
    case IR::Opcode::SharedAtomicSMin32:
    case IR::Opcode::SharedAtomicUMin32: {
        const bool is_signed = inst.GetOpcode() == IR::Opcode::SharedAtomicSMin32;
        inst.ReplaceUsesWith(
            ir.BufferAtomicIMin(handle, address_dwords, inst.Arg(1), is_signed, {}));
        break;
    }
    case IR::Opcode::SharedAtomicSMax32:
    case IR::Opcode::SharedAtomicUMax32: {
        const bool is_signed = inst.GetOpcode() == IR::Opcode::SharedAtomicSMax32;
        inst.ReplaceUsesWith(
            ir.BufferAtomicIMax(handle, address_dwords, inst.Arg(1), is_signed, {}));
        break;
    }
    case IR::Opcode::SharedAtomicInc32:
        inst.ReplaceUsesWith(ir.BufferAtomicInc(handle, address_dwords, {}));
        break;
    case IR::Opcode::SharedAtomicDec32:
        inst.ReplaceUsesWith(ir.BufferAtomicDec(handle, address_dwords, {}));
        break;
    case IR::Opcode::SharedAtomicAnd32:
        inst.ReplaceUsesWith(ir.BufferAtomicAnd(handle, address_dwords, inst.Arg(1), {}));
        break;
    case IR::Opcode::SharedAtomicOr32:
        inst.ReplaceUsesWith(ir.BufferAtomicOr(handle, address_dwords, inst.Arg(1), {}));
        break;
    case IR::Opcode::SharedAtomicXor32:
        inst.ReplaceUsesWith(ir.BufferAtomicXor(handle, address_dwords, inst.Arg(1), {}));
        break;
    case IR::Opcode::SharedAtomicCmpSwap32:
        // Args are (address, value, cmp_value)
        inst.ReplaceUsesWith(
            ir.BufferAtomicCmpSwap(handle, address_dwords, inst.Arg(1), inst.Arg(2), {}));
        break;
    case IR::Opcode::LoadSharedU16: {
        inst.ReplaceUsesWith(ir.LoadBufferU16(handle, address_words, {}));
        buffer.used_types |= IR::Type::U16;
        break;
    }
    case IR::Opcode::LoadSharedU32:
        inst.ReplaceUsesWith(ir.LoadBufferU32(1, handle, address_dwords, {}));
        break;
    case IR::Opcode::LoadSharedU64: {
        inst.ReplaceUsesWith(ir.LoadBufferU64(handle, address_qwords, {}));
        buffer.used_types |= IR::Type::U64;
        break;
    }
    case IR::Opcode::WriteSharedU16: {
        ir.StoreBufferU16(handle, address_words, IR::U16{inst.Arg(1)}, {});
        inst.Invalidate();
        buffer.used_types |= IR::Type::U16;
        break;
    }
    case IR::Opcode::WriteSharedU32:
        ir.StoreBufferU32(1, handle, address_dwords, inst.Arg(1), {});
        inst.Invalidate();
        break;
    case IR::Opcode::WriteSharedU64: {
        ir.StoreBufferU64(handle, address_qwords, IR::U64{inst.Arg(1)}, {});
        inst.Invalidate();
        buffer.used_types |= IR::Type::U64;
        break;
    }
    default:
        UNREACHABLE_MSG("Unexpected opcode {}", inst.GetOpcode());
    }
}

IR::U32 CalculateBufferAddress(IR::IREmitter& ir, const IR::Inst& inst, const Info& info,
                               const AmdGpu::Buffer& buffer, u32 stride) {
    const auto inst_info = inst.Flags<IR::BufferInstInfo>();
    const u32 inst_offset = inst_info.inst_offset.Value();
    const auto is_inst_typed = inst_info.inst_data_fmt != AmdGpu::DataFormat::FormatInvalid;
    const auto data_format = is_inst_typed
                                 ? AmdGpu::RemapDataFormat(inst_info.inst_data_fmt.Value())
                                 : buffer.GetDataFmt();
    const u32 shift = BufferAddressShift(inst, data_format);
    const u32 mask = (1 << shift) - 1;
    const IR::U32 soffset = IR::GetBufferSOffsetArg(&inst);

    // If address calculation is of the form "index * const_stride + offset" with offset constant
    // and both const_stride and offset are divisible with the element size, apply shift directly.
    if (inst_info.index_enable && !inst_info.voffset_enable && soffset.IsImmediate() &&
        !buffer.swizzle_enable && !buffer.add_tid_enable && (stride & mask) == 0) {
        const u32 total_offset = soffset.U32() + inst_offset;
        if ((total_offset & mask) == 0) {
            // buffer_offset = index * (const_stride >> shift) + (offset >> shift)
            const IR::U32 index = IR::GetBufferIndexArg(&inst);
            return ir.IAdd(ir.IMul(index, ir.Imm32(stride >> shift)),
                           ir.Imm32(total_offset >> shift));
        }
    }

    // index = (inst_idxen ? vgpr_index : 0) + (const_add_tid_enable ? thread_id[5:0] : 0)
    IR::U32 index = ir.Imm32(0U);
    if (inst_info.index_enable) {
        const IR::U32 vgpr_index = IR::GetBufferIndexArg(&inst);
        index = ir.IAdd(index, vgpr_index);
    }
    if (buffer.add_tid_enable) {
        ASSERT_MSG(info.sw_stage == SwStage::Compute,
                   "Thread ID buffer addressing is not supported outside of compute.");
        const IR::U32 thread_id{ir.LaneId()};
        index = ir.IAdd(index, thread_id);
    }
    // offset = (inst_offen ? vgpr_offset : 0) + inst_offset
    IR::U32 offset = ir.Imm32(inst_offset);
    offset = ir.IAdd(offset, soffset);
    if (inst_info.voffset_enable) {
        const IR::U32 voffset = IR::GetBufferVOffsetArg(&inst);
        offset = ir.IAdd(offset, voffset);
    }
    const IR::U32 const_stride = ir.Imm32(stride);
    IR::U32 buffer_offset;
    if (buffer.swizzle_enable) {
        const IR::U32 const_index_stride = ir.Imm32(buffer.GetIndexStride());
        const IR::U32 const_element_size = ir.Imm32(buffer.GetElementSize());
        // index_msb = index / const_index_stride
        const IR::U32 index_msb{ir.IDiv(index, const_index_stride)};
        // index_lsb = index % const_index_stride
        const IR::U32 index_lsb{ir.IMod(index, const_index_stride)};
        // offset_msb = offset / const_element_size
        const IR::U32 offset_msb{ir.IDiv(offset, const_element_size)};
        // offset_lsb = offset % const_element_size
        const IR::U32 offset_lsb{ir.IMod(offset, const_element_size)};
        // buffer_offset =
        //     (index_msb * const_stride + offset_msb * const_element_size) * const_index_stride
        //     + index_lsb * const_element_size + offset_lsb
        const IR::U32 buffer_offset_msb = ir.IMul(
            ir.IAdd(ir.IMul(index_msb, const_stride), ir.IMul(offset_msb, const_element_size)),
            const_index_stride);
        const IR::U32 buffer_offset_lsb =
            ir.IAdd(ir.IMul(index_lsb, const_element_size), offset_lsb);
        buffer_offset = ir.IAdd(buffer_offset_msb, buffer_offset_lsb);
    } else {
        // buffer_offset = index * const_stride + offset
        buffer_offset = ir.IAdd(ir.IMul(index, const_stride), offset);
    }
    if (shift != 0) {
        buffer_offset = ir.ShiftRightLogical(buffer_offset, ir.Imm32(shift));
    }
    return buffer_offset;
}

void PatchBufferArgs(IR::Inst& inst, Info& info, const ResourceDiscovery& discovered,
                     std::vector<std::pair<IR::Inst*, IR::Value>>& pending_dynamic_reads) {
    const auto handle = inst.Arg(0);
    const auto buffer_res = info.buffers[handle.U32()];
    const auto buffer = buffer_res.GetSharp(info);

    // S_BUFFER_LOAD_DWORD reads guest memory using an S# buffer descriptor.
    // If the native resource-table walker could not flatten the descriptor,
    // binding the generated invalid V# as a Vulkan null buffer discards a
    // real guest memory operation. Preserve and execute the original read
    // through the existing shader-side GPU address-translation path.
    //
    // Only use this path for scalar reads with genuinely unflattened V#.
    // Normal descriptors keep their normal fast bound-buffer lowering.
    if (inst.GetOpcode() == IR::Opcode::ReadConstBuffer) {
        using Summary = SharpFetch<AmdGpu::Buffer>::Summary;
        if (buffer_res.sharp_fetch.summary != Summary::Invalid) {
            return;
        }

        const auto& sharp = discovered.sharps[0];
        // Use exactly the guest IR descriptor as issued by the scalar-memory
        // instruction, before the host-side sharp recognizer stripped any
        // lane reads or rewritten stride bits. Only the base-address field
        // participates in S_BUFFER_LOAD_DWORD guest address translation.
        IR::Inst* const handle = discovered.original_buffer_handle.TryInst();
        ASSERT_MSG(handle && handle->GetOpcode() == IR::Opcode::CompositeConstructU32x4,
                   "Scalar buffer read has unsupported source descriptor IR");
        IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};

        const IR::U32 base_lo{handle->Arg(0)};
        const IR::U32 base_hi = ir.BitwiseAnd(IR::U32{handle->Arg(1)}, ir.Imm32(0xffu));
        IR::Value gpu_addr = ir.CompositeConstruct(base_lo, base_hi);
        if (sharp.post_op == SharpFetchPostOp::OffsetByProgramBase) {
            // The existing host path adds the shader program base for inline
            // constant buffers. Match it with full 64-bit carry and 40-bit
            // guest address wraparound.
            const IR::U64 packed = ir.PackUint2x32(gpu_addr);
            const IR::U64 relative = ir.IAdd(packed, ir.Imm64(static_cast<u64>(info.pgm_base)));
            const IR::U64 wrapped = ir.BitwiseAnd(relative, ir.Imm64(u64{0xffffffffff}));
            gpu_addr = ir.UnpackUint2x32(wrapped);
        }

        // This is a genuine shader-side S_BUFFER_LOAD fallback. Its
        // descriptor or offset may depend on Phi/GetAttribute, so all
        // calculations stay as IR evaluated by the guest GPU invocation.
        //
        // GCN3 SMEM: stride=V#[dw1][29:16], NUM_RECORDS=V#[dw2].
        // Byte offset wraps to 32 bits BEFORE comparing bounds. A stride
        // of zero clamps at >= NUM_RECORDS; nonzero clamps only at >
        // stride * NUM_RECORDS, using the full 64-bit multiplication.
        const IR::U32 dw1{handle->Arg(1)};
        const IR::U32 stride = ir.BitwiseAnd(
            ir.ShiftRightLogical(dw1, ir.Imm32(16u)), ir.Imm32(0x3fffu));
        const IR::U32 num_records{handle->Arg(2)};
        const IR::U32 raw_bytes = ir.ShiftLeftLogical(
            IR::U32{inst.Arg(1)}, ir.Imm32(2u));
        const IR::U64 stride64{ir.UConvert(64, stride)};
        const IR::U64 records64{ir.UConvert(64, num_records)};
        const IR::U64 offset64{ir.UConvert(64, raw_bytes)};
        const IR::U64 stride_limit{ir.IMul(stride64, records64)};
        const IR::U1 zero_stride = ir.IEqual(stride, ir.Imm32(0u));
        const IR::U64 limit{ir.Select(zero_stride, records64, stride_limit)};
        const IR::U1 ge_zero = ir.IGreaterThanEqual(offset64, records64, false);
        const IR::U1 gt_stride = ir.IGreaterThan(offset64, stride_limit, false);
        const IR::U1 clamp{ir.Select(zero_stride, ge_zero, gt_stride)};
        const IR::U64 effective_bytes{ir.Select(clamp, limit, offset64)};

        // Mask the FINAL guest address to 40 bits and align to 4 bytes.
        // ReadConst's normal 48-bit page reader cannot provide this SMEM
        // guarantee if given the unbounded base + dword-index separately.
        // The existing BDA translation/fault path is reused unchanged.
        const IR::U64 base64 = ir.PackUint2x32(gpu_addr);
        const IR::U64 address = ir.BitwiseAnd(
            ir.IAdd(base64, effective_bytes), ir.Imm64(u64{0xfffffffffc}));
        const IR::U32 guest_read =
            ir.ReadConst(ir.UnpackUint2x32(address), ir.Imm32(0u));
        // Descriptor metadata captured in resource discovery is not a live
        // IR use-list: postpone producer removal until all shader-side loads
        // have registered real IR uses of those producers.
        pending_dynamic_reads.emplace_back(&inst, guest_read);
        LOG_INFO(Render_Recompiler,
                 "SBufferDynamic: shader={:#x} stage={} source=guest_gpu_memory "
                 "descriptor_dwords={} post_op={} gpu_smem_bounds=YES "
                 "offset_wrap=32 stride_product=64 address_bits=40",
                 info.pgm_hash, info.hw_stage, sharp.num_dwords, u32(sharp.post_op));
        return;
    }

    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};
    inst.SetArg(IR::LoadBufferArgs::Address,
                CalculateBufferAddress(ir, inst, info, buffer, buffer.stride));
}

void PatchImageSampleArgs(IR::Inst& inst, Info& info, const ImageResource& image_res,
                          const AmdGpu::Image& image) {
    const auto handle = inst.Arg(0);
    const auto& sampler_res = info.samplers[(handle.U32() >> 16) & 0xFFFF];
    const auto sampler = sampler_res.GetSharp(info);

    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};
    const auto inst_info = inst.Flags<IR::TextureInstInfo>();
    const auto view_type = image.GetViewType(image_res.is_array);

    IR::Inst* body1 = inst.Arg(2).Inst();
    IR::Inst* body2 = inst.Arg(3).Inst();
    IR::Inst* body3 = inst.Arg(4).Inst();
    IR::F32 body4 = IR::F32{inst.Arg(5)};
    const auto get_addr_reg = [&](u32 index) -> IR::F32 {
        if (index <= 3) {
            return IR::F32{body1->Arg(index)};
        }
        if (index >= 4 && index <= 7) {
            return IR::F32{body2->Arg(index - 4)};
        }
        if (index >= 8 && index <= 11) {
            return IR::F32{body3->Arg(index - 8)};
        }
        if (index == 12) {
            return body4;
        }
        UNREACHABLE();
    };
    u32 addr_reg = 0;

    // Load first address components as denoted in 8.2.4 VGPR Usage Sea Islands Series Instruction
    // Set Architecture
    const IR::Value offset = [&] -> IR::Value {
        if (!inst_info.has_offset) {
            return IR::U32{};
        }

        // The offsets are six-bit signed integers: X=[5:0], Y=[13:8], and Z=[21:16].
        IR::Value arg = get_addr_reg(addr_reg++);
        if (const IR::Inst* offset_inst = arg.TryInst()) {
            ASSERT(offset_inst->GetOpcode() == IR::Opcode::BitCastF32U32);
            arg = offset_inst->Arg(0);
        }

        const auto read = [&](u32 off) -> IR::U32 {
            if (arg.IsImmediate()) {
                const u32 imm =
                    arg.Type() == IR::Type::F32 ? std::bit_cast<u32>(arg.F32()) : arg.U32();
                const u16 comp = (imm >> off) & 0x3F;
                return ir.Imm32(s32(comp << 26) >> 26);
            }
            return ir.BitFieldExtract(IR::U32{arg}, ir.Imm32(off), ir.Imm32(6), true);
        };

        switch (view_type) {
        case AmdGpu::ImageType::Color1D:
        case AmdGpu::ImageType::Color1DArray:
            return read(0);
        case AmdGpu::ImageType::Color2D:
        case AmdGpu::ImageType::Color2DMsaa:
        case AmdGpu::ImageType::Color2DArray:
        case AmdGpu::ImageType::Cube:
            return ir.CompositeConstruct(read(0), read(8));
        case AmdGpu::ImageType::Color3D:
            return ir.CompositeConstruct(read(0), read(8), read(16));
        default:
            UNREACHABLE();
        }
    }();
    const IR::F32 bias = inst_info.has_bias ? get_addr_reg(addr_reg++) : IR::F32{};
    const IR::F32 dref = inst_info.is_depth ? get_addr_reg(addr_reg++) : IR::F32{};
    const auto [derivatives_dx, derivatives_dy] = [&] -> std::pair<IR::Value, IR::Value> {
        if (!inst_info.has_derivatives) {
            return {};
        }
        switch (view_type) {
        case AmdGpu::ImageType::Color1D:
        case AmdGpu::ImageType::Color1DArray:
            // du/dx, du/dy
            addr_reg = addr_reg + 2;
            return {get_addr_reg(addr_reg - 2), get_addr_reg(addr_reg - 1)};
        case AmdGpu::ImageType::Color2D:
        case AmdGpu::ImageType::Color2DMsaa:
        case AmdGpu::ImageType::Color2DArray:
        case AmdGpu::ImageType::Cube:
            // (du/dx, dv/dx), (du/dy, dv/dy)
            addr_reg = addr_reg + 4;
            return {ir.CompositeConstruct(get_addr_reg(addr_reg - 4), get_addr_reg(addr_reg - 3)),
                    ir.CompositeConstruct(get_addr_reg(addr_reg - 2), get_addr_reg(addr_reg - 1))};
        case AmdGpu::ImageType::Color3D:
            // (du/dx, dv/dx, dw/dx), (du/dy, dv/dy, dw/dy)
            addr_reg = addr_reg + 6;
            return {ir.CompositeConstruct(get_addr_reg(addr_reg - 6), get_addr_reg(addr_reg - 5),
                                          get_addr_reg(addr_reg - 4)),
                    ir.CompositeConstruct(get_addr_reg(addr_reg - 3), get_addr_reg(addr_reg - 2),
                                          get_addr_reg(addr_reg - 1))};
        default:
            UNREACHABLE();
        }
    }();

    const bool is_msaa = view_type == AmdGpu::ImageType::Color2DMsaa ||
                         view_type == AmdGpu::ImageType::Color2DMsaaArray;
    const bool unnormalized = sampler.force_unnormalized || inst_info.is_unnormalized;
    const bool needs_dimentions = (!is_msaa && unnormalized) || (is_msaa && !unnormalized);
    const auto dimensions =
        needs_dimentions ? ir.ImageQueryDimension(handle, ir.Imm32(0u), ir.Imm1(false), inst_info)
                         : IR::Value{};
    const auto get_coord = [&](u32 coord_idx, u32 dim_idx) -> IR::Value {
        const auto coord = get_addr_reg(coord_idx);
        if (is_msaa) {
            // For MSAA images preserve the unnormalized coord or manually unnormalize it
            if (unnormalized) {
                return ir.ConvertFToU(32, coord);
            } else {
                const auto dim =
                    ir.ConvertUToF(32, 32, IR::U32{ir.CompositeExtract(dimensions, dim_idx)});
                return ir.ConvertFToU(32, ir.FPMul(coord, dim));
            }
        }
        if (unnormalized) {
            // Normalize the coordinate for sampling, dividing by its corresponding dimension.
            const auto dim =
                ir.ConvertUToF(32, 32, IR::U32{ir.CompositeExtract(dimensions, dim_idx)});
            return ir.FPDiv(coord, dim);
        }
        return coord;
    };

    // Now we can load body components as noted in Table 8.9 Image Opcodes with Sampler
    const IR::Value coords = [&] -> IR::Value {
        switch (view_type) {
        case AmdGpu::ImageType::Color1D: // x
            addr_reg = addr_reg + 1;
            return get_coord(addr_reg - 1, 0);
        case AmdGpu::ImageType::Color1DArray: // x, slice
        case AmdGpu::ImageType::Color2D:      // x, y
        case AmdGpu::ImageType::Color2DMsaa:  // x, y
            addr_reg = addr_reg + 2;
            return ir.CompositeConstruct(get_coord(addr_reg - 2, 0), get_coord(addr_reg - 1, 1));
        case AmdGpu::ImageType::Color2DArray: // x, y, slice
        case AmdGpu::ImageType::Cube:         // x, y, face
        case AmdGpu::ImageType::Color3D:      // x, y, z
            addr_reg = addr_reg + 3;
            return ir.CompositeConstruct(get_coord(addr_reg - 3, 0), get_coord(addr_reg - 2, 1),
                                         get_coord(addr_reg - 1, 2));
        default:
            UNREACHABLE();
        }
    }();

    ASSERT(!inst_info.has_lod || !inst_info.has_lod_clamp);
    const bool explicit_lod = inst_info.has_lod || inst_info.force_level0;
    const IR::F32 lod = inst_info.has_lod        ? get_addr_reg(addr_reg++)
                        : inst_info.force_level0 ? ir.Imm32(0.0f)
                                                 : IR::F32{};
    const IR::F32 lod_clamp = inst_info.has_lod_clamp ? get_addr_reg(addr_reg++) : IR::F32{};

    auto texel = [&] -> IR::Value {
        if (is_msaa) {
            return ir.ImageRead(handle, coords, {}, ir.Imm32(0U), inst_info);
        }
        if (inst_info.is_gather) {
            if (inst_info.is_depth) {
                return ir.ImageGatherDref(handle, coords, offset, dref, inst_info);
            }
            return ir.ImageGather(handle, coords, offset, inst_info);
        }
        if (inst_info.has_derivatives) {
            if (image_res.is_guest_indexed) {
                // Explicit gradient is required for the compute shaders:
                // implicit LOD would not preserve the original PS4 opcode.
                return ir.ImageGradientIndexed(
                    handle, coords, derivatives_dx, derivatives_dy, offset,
                    IR::U32{inst.Arg(1)}, inst_info);
            }
            return ir.ImageGradient(handle, coords, derivatives_dx, derivatives_dy, offset,
                                    lod_clamp, inst_info);
        }
        if (inst_info.is_depth) {
            if (explicit_lod) {
                return ir.ImageSampleDrefExplicitLod(handle, coords, dref, lod, offset, inst_info);
            }
            return ir.ImageSampleDrefImplicitLod(handle, coords, dref, bias, offset, inst_info);
        }
        if (explicit_lod) {
            return ir.ImageSampleExplicitLod(handle, coords, lod, offset, inst_info);
        }
        if (image_res.is_guest_indexed) {
            return ir.ImageSampleImplicitLodIndexed(
                handle, coords, bias, offset, IR::U32{inst.Arg(1)}, inst_info);
        }
        return ir.ImageSampleImplicitLod(handle, coords, bias, offset, inst_info);
    }();

    auto converted = ApplyReadNumberConversionVec4(ir, texel, image.GetNumberConversion());
    if (sampler.force_degamma && image.GetNumberFmt() != AmdGpu::NumberFormat::Srgb) {
        converted = ApplyForceDegamma(ir, texel);
    }
    inst.ReplaceUsesWith(converted);
}

void PatchImageArgs(IR::Inst& inst, Info& info) {
    // Nothing to patch for dimension query.
    if (inst.GetOpcode() == IR::Opcode::ImageQueryDimensions) {
        return;
    }

    const auto image_handle = inst.Arg(0);
    const auto binding_index = image_handle.U32() & 0xFFFF;
    const auto& image_res = info.images[binding_index];
    auto image = image_res.GetSharp(info);

    // Sample instructions must be handled separately using address register data.
    if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw) {
        return PatchImageSampleArgs(inst, info, image_res, image);
    }

    IR::IREmitter ir{*inst.GetParent(), IR::Block::InstructionList::s_iterator_to(inst)};
    auto inst_info = inst.Flags<IR::TextureInstInfo>();
    const auto view_type = image.GetViewType(image_res.is_array);

    // Now that we know the image type, adjust texture coordinate vector.
    IR::Inst* body = inst.Arg(1).Inst();
    const auto [coords, arg] = [&] -> std::pair<IR::Value, IR::Value> {
        switch (view_type) {
        case AmdGpu::ImageType::Color1D: // x, [lod]
            return {body->Arg(0), body->Arg(1)};
        case AmdGpu::ImageType::Color1DArray: // x, slice, [lod]
        case AmdGpu::ImageType::Color2D:      // x, y, [lod]
            return {ir.CompositeConstruct(body->Arg(0), body->Arg(1)), body->Arg(2)};
        case AmdGpu::ImageType::Color2DMsaa: // x, y. (sample is passed on different argument)
        {
            auto skip_lod = false;
            if (inst_info.has_lod) {
                const auto mipid = body->Arg(2);
                if (mipid.IsImmediate() && mipid.U32() == 0) {
                    // if image_x_mip refers to a MSAA image, and mipid is 0, it is safe to be
                    // skipped and fragid is taken from the next arg
                    LOG_WARNING(Render_Recompiler, "Encountered a _mip instruction with MSAA "
                                                   "image, and mipid is 0, skipping LoD");
                    inst_info.has_lod.Assign(false);
                    skip_lod = true;
                } else {
                    UNREACHABLE_MSG(
                        "Encountered a _mip instruction with MSAA image, and mipid is non-zero");
                }
            }
            return {ir.CompositeConstruct(body->Arg(0), body->Arg(1)), body->Arg(skip_lod ? 3 : 2)};
        }
        case AmdGpu::ImageType::Color2DArray:     // x, y, slice, [lod]
        case AmdGpu::ImageType::Color2DMsaaArray: // x, y, slice. (sample is passed on different
                                                  // argument)
        case AmdGpu::ImageType::Cube:             // x, y, face, [lod]
        case AmdGpu::ImageType::Color3D:          // x, y, z, [lod]
            return {ir.CompositeConstruct(body->Arg(0), body->Arg(1), body->Arg(2)), body->Arg(3)};
        default:
            UNREACHABLE_MSG("Unknown image type {}", view_type);
        }
    }();

    const auto has_ms = view_type == AmdGpu::ImageType::Color2DMsaa ||
                        view_type == AmdGpu::ImageType::Color2DMsaaArray;
    ASSERT(!inst_info.has_lod || !has_ms);
    // If we are binding a single mip level as fallback, drop the argument
    const auto lod =
        (inst_info.has_lod && image_res.mip_fallback_mode != MipStorageFallbackMode::ConstantIndex)
            ? IR::U32{arg}
            : IR::U32{};
    const auto ms = has_ms ? IR::U32{arg} : IR::U32{};

    const auto is_storage = image_res.is_written;
    if (inst.GetOpcode() == IR::Opcode::ImageRead) {
        auto texel = ir.ImageRead(image_handle, coords, lod, ms, inst_info);
        if (is_storage) {
            // Storage image requires shader swizzle.
            texel = ApplySwizzle(ir, texel, image.DstSelect());
        }
        const auto converted =
            ApplyReadNumberConversionVec4(ir, texel, image.GetNumberConversion());
        inst.ReplaceUsesWith(converted);
    } else {
        inst.SetArg(1, coords);
        if (inst.GetOpcode() == IR::Opcode::ImageWrite) {
            inst.SetArg(2, lod);
            inst.SetArg(3, ms);

            auto texel = inst.Arg(4);
            if (is_storage) {
                // Storage image requires shader swizzle.
                texel = ApplySwizzle(ir, texel, image.DstSelect().Inverse());
            }
            const auto converted =
                ApplyWriteNumberConversionVec4(ir, texel, image.GetNumberConversion());
            inst.SetArg(4, converted);
        }
    }
}

// A compute program can have valid and invalid host-discoverable SRT
// variants of the SAME strided material record. The incomplete variants
// would otherwise be lowered as dummy descriptors alongside hundreds of
// live indexed Vulkan image slots. A mixed permutation has no sound
// resource model and has triggered GPU-driver failures in real workloads.
//
// Fail CLOSED per program: only enable the strided GPU-selected fastpath
// when the complete set of strided image sources is representable from the
// immutable per-program flattened user data. This is independent of any
// game or shader hash and never fabricates a missing guest V# descriptor.
// Shader variant discovery may legitimately fail to resolve a strided V#.
// Capture the exact reason once per compiled permutation without guessing
// texture IDs, dereferencing guest pointers or changing Vulkan resources.
static bool StridedSourceProofEnabled(const Shader::Info& info) {
    static const bool enabled=[] {
        const char* const flag=std::getenv("SHADPS4_STRIDED_SOURCE_PROOF");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    static const auto filter=VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_STRIDED_SOURCE_PROOF_HASHES"));
    return enabled && (!filter.enabled || (filter.valid && filter.Matches(info.pgm_hash)));
}

static bool AllStridedGuestImagesRepresentable(
    const Shader::Info& info, const ResourceDiscoveryList& resources,
    const Profile& profile, u32& sources, u32& invalid) {
    sources=0;
    invalid=0;
    if (info.hw_stage!=HwStage::Compute) return true;

    const bool probe=StridedSourceProofEnabled(info);
    for (const auto& usage : resources) {
        if (!usage.has_indexed_table || usage.indexed_record_stride_bytes==0u)
            continue;
        ++sources;
        const auto table_fetch=ConstructSharpFetch<AmdGpu::Buffer>(
            usage.indexed_table_sharp,info);
        AmdGpu::Buffer table{};
        const bool flattened =
            table_fetch.summary != SharpFetch<AmdGpu::Buffer>::Summary::Invalid;
        const bool in_bounds = table_fetch.ReadOffsetsInBounds(info.flattened_ud_buf.size());
        const bool fetched = table_fetch.FetchChecked(info.flattened_ud_buf.data(),
                                                      info.flattened_ud_buf.size(), &table);
        const bool valid=fetched && table.Valid();
        const bool has_base=valid && table.base_address!=0;
        const u32 entries=has_base
            ? CompleteGuestTsharpRecordEntries(
                static_cast<u32>(table.stride),
                static_cast<u32>(table.num_records),
                usage.indexed_record_stride_bytes,
                usage.indexed_descriptor_offset_bytes,
                profile.max_guest_indexed_image_entries)
            : 0u;
        const bool eligible=entries!=0u;
        if (!eligible) ++invalid;

        if (probe) {
            const auto reason = !flattened ? "UNFLATTENED_DESCRIPTOR_SOURCE"
                : !in_bounds ? "FLATTENED_SOURCE_OUT_OF_RANGE"
                : !valid ? "INVALID_BUFFER_DESCRIPTOR"
                : !has_base ? "ZERO_BASE_ADDRESS"
                : table.num_records==0u ? "ZERO_RECORD_COUNT"
                : !eligible ? "UNREPRESENTABLE_TABLE_LENGTH"
                : "PROVEN_COMPLETE";
            const auto& source=usage.indexed_table_sharp;
            const u32 missing=MissingSharpWordMask(source,table_fetch);
            // Raw values are descriptor words, never a guest-memory read.
            LOG_INFO(Render_Recompiler,
                     "GuestStridedSourceProof: shader={:#x} stage={} source={} "
                     "result={} reason={} fetched={} fetch_summary={} source_in_bounds={} flatbuf_words={} "
                     "descriptor_words={} missing_mask={:#x} load_mask={:#x} "
                     "flat_offsets={},{},{},{} "
                     "immediate_words={:#x},{:#x},{:#x},{:#x} "
                     "raw_base={:#x} raw_stride={} raw_records={} "
                     "record_stride={} image_offset={} sampler_offset={} "
                     "entries={} profile_limit={}",
                     info.pgm_hash,info.hw_stage,sources,
                     eligible,reason,fetched,static_cast<u32>(table_fetch.summary),
                     in_bounds, info.flattened_ud_buf.size(),
                     source.num_dwords,missing,static_cast<u32>(table_fetch.load_mask),
                     static_cast<u32>(table_fetch.offsets[0]),
                     static_cast<u32>(table_fetch.offsets[1]),
                     static_cast<u32>(table_fetch.offsets[2]),
                     static_cast<u32>(table_fetch.offsets[3]),
                     table_fetch.immediates[0],table_fetch.immediates[1],
                     table_fetch.immediates[2],table_fetch.immediates[3],
                     static_cast<u64>(table.base_address),
                     static_cast<u32>(table.stride),
                     static_cast<u32>(table.num_records),
                     usage.indexed_record_stride_bytes,
                     usage.indexed_descriptor_offset_bytes,
                     usage.indexed_sampler_offset_bytes,
                     entries,profile.max_guest_indexed_image_entries);
        }
    }
    if (probe) {
        LOG_INFO(Render_Recompiler,
                 "GuestStridedPermutationProof: shader={:#x} stage={} "
                 "sources={} missing={} complete={} action={}",
                 info.pgm_hash,info.hw_stage,sources,invalid,
                 CompleteStridedGuestShader(sources,invalid),
                 invalid==0u?"INDEXED_ELIGIBLE":"ORIGINAL_RESOURCE_FALLBACK");
    }
    return CompleteStridedGuestShader(sources,invalid);
}

void ResourcePatchingPass(Shader::Info& info, const ResourceDiscoveryList& resources,
                          const Profile& profile) {
    u32 indexed_sources{}, invalid_indexed_sources{};
    const bool complete=AllStridedGuestImagesRepresentable(
        info,resources,profile,indexed_sources,invalid_indexed_sources);
    if (!complete && indexed_sources) {
        LOG_WARNING(Render_Recompiler,
                    "GuestTsharpIncompleteProgram: shader={:#x} stage={} "
                    "strided_sources={} missing={} "
                    "action=keep_original_resource_path",
                    info.pgm_hash,info.hw_stage,indexed_sources,invalid_indexed_sources);
    }
    // Iterate over discovered resources and patch them after finding the sharp.
    // Pass 1: Track resource sharps
    Descriptors descriptors{info};
    for (const auto& usage : resources) {
        IR::Inst& inst = *usage.user;
        if (IsBufferInstruction(inst)) {
            PatchBufferSharp(usage, info, descriptors, profile);
        } else if (IsImageInstruction(inst)) {
            PatchImageSharp(usage, info, descriptors, profile, complete);
        }
    }

    // Pass 2: patch resource users, keeping old scalar descriptor producers
    // alive until all fallback read expressions have been constructed.
    std::vector<std::pair<IR::Inst*, IR::Value>> pending_dynamic_reads;
    for (const auto& usage : resources) {
        IR::Inst& inst = *usage.user;
        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info, usage, pending_dynamic_reads);
        } else if (IsImageInstruction(inst)) {
            PatchImageArgs(inst, info);
        } else if (IsDataRingInstruction(inst)) {
            PatchGlobalDataShareAccess(inst, info, descriptors, profile);
        }
    }
    // Every new ReadConst now owns IR use references to the original source
    // nodes. Replacing a producer updates all dependent GPU read expressions.
    for (const auto& [old_inst, guest_read] : pending_dynamic_reads) {
        old_inst->ReplaceUsesWithAndRemove(guest_read);
    }
}

} // namespace Shader::Optimization
