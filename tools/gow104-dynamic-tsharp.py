#!/usr/bin/env python3
"""GoW104: generic runtime-indexed sampled T# descriptor tables.

Builds on the retained GoW103/98/97/96/91 chain.  Dynamic sampled image
descriptors of the form ReadConst(ptr, element*stride + constant + dword)
are represented as Vulkan descriptor arrays and indexed by the shader.  The
CPU SRT walker therefore does not need to invent Phi/GetAttribute values.

No game/shader IDs, no fake selector values, no global readbacks.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, types
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
LOGS=Path.home()/"projects/shadps4/logs"

GOW103_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "c4465be12f280af226d3f14416a8a21be5c68b31/tools/gow103-srt-arithmetic.py")
GOW103_BLOB="b8f3da25728baff77b7ad3063eebd01ba9f3ab4d"
CORE_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "265542f4a2986ca79e85e3d54957d9e07c64b876/tools/gow104-dynamic-tsharp-core.patch")
CORE_BLOB="8f4fb9bd63edba38df34caacc84eb62199cb6825"

RESOURCE_H="src/shader_recompiler/resource.h"
INFO_H="src/shader_recompiler/info.h"
PATCH_CPP="src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
RASTER_CPP="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RASTER_H="src/video_core/renderer_vulkan/vk_rasterizer.h"
OPCODES_H="src/shader_recompiler/ir/opcodes.h"
SERIAL_CPP="src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"

CORE_FILES=(
 "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
 "src/shader_recompiler/backend/spirv/emit_spirv_image.cpp",
 "src/shader_recompiler/backend/spirv/emit_spirv_instructions.h",
 "src/shader_recompiler/backend/spirv/spirv_emit_context.cpp",
 "src/shader_recompiler/backend/spirv/spirv_emit_context.h",
 "src/shader_recompiler/ir/ir_emitter.cpp",
 "src/shader_recompiler/ir/ir_emitter.h",
 "src/shader_recompiler/ir/opcodes.inc",
 PATCH_CPP,
 "src/video_core/renderer_vulkan/vk_instance.cpp",
)
MANUAL_FILES=(RESOURCE_H,INFO_H,RASTER_CPP,RASTER_H,SERIAL_CPP)
# opcodes.inc changes Opcode values; force every dependent linked TU to recompile through opcodes.h.
DYNAMIC_FILES=CORE_FILES+MANUAL_FILES+(OPCODES_H,)


def git_blob(data:bytes)->str:
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def fetch(url:str,path:Path,blob:str):
    if path.is_file() and not path.is_symlink() and git_blob(path.read_bytes())==blob:
        return path
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked helper: "+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=".gow104-",dir=path.parent);os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                        "--max-time","120",url,"-o",name],check=True,timeout=380)
        data=Path(name).read_bytes()
        if git_blob(data)!=blob:
            raise RuntimeError("Pinned download fingerprint mismatch: "+path.name)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    return path


def load_gow103():
    p=fetch(GOW103_URL,ROOT/"scripts/gow103-pinned-for-gow104.py",GOW103_BLOB)
    m=types.ModuleType("gow104_gow103");m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__)
    return m


def one(text:str,old:str,new:str,label:str)->str:
    if text.count(old)!=1:
        raise RuntimeError(label+" source shape missing/ambiguous")
    return text.replace(old,new,1)


def source_for(src:Path,overrides:dict,rel:str)->Path:
    key=(src/rel).resolve()
    p=Path(overrides.get(key,key))
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("Missing/unsafe retained source: "+rel)
    return p


def adapt_resource_h(data:bytes)->bytes:
    text=data.decode()
    if "SharpTableList" in text or "NUM_IMAGE_ARRAY_DESCRIPTORS" in text:
        raise RuntimeError("Dynamic T# resource ABI already present")

    text=one(text,
      "static constexpr u32 NUM_FMASKS = 8;\n",
      "static constexpr u32 NUM_FMASKS = 8;\n"
      "static constexpr u32 MAX_TSHARP_ARRAY_SIZE = 64;\n"
      "static constexpr u32 NUM_IMAGE_ARRAY_DESCRIPTORS = 16 * (MAX_TSHARP_ARRAY_SIZE + 1);\n",
      "resource descriptor budget")

    text=one(text,
      "    constexpr bool Fetch(const u32* flatbuf, T* out) const {\n"
      "        if (summary == Summary::SingleLoad) [[likely]] {\n"
      "            std::memcpy(out, flatbuf + offsets[0], num_dwords * sizeof(u32));\n",
      "    constexpr bool Fetch(const u32* flatbuf, T* out, u32 element_off_dw = 0) const {\n"
      "        if (summary == Summary::SingleLoad) [[likely]] {\n"
      "            std::memcpy(out, flatbuf + offsets[0] + element_off_dw, num_dwords * sizeof(u32));\n",
      "SharpFetch contiguous element offset")
    text=one(text,
      "            out_dw[i] = (mask & 1) ? flatbuf[offsets[i]] : immediates[i];\n",
      "            out_dw[i] = (mask & 1) ? flatbuf[offsets[i] + element_off_dw] : immediates[i];\n",
      "SharpFetch scattered element offset")

    anchor="};\n\nenum class SharpFetchPostOp : u8 {\n"
    table="""};

struct SharpTable {
    SharpLocation ptr_lo;
    SharpLocation ptr_hi;
    SharpLocation flatbuf_off;
    u16 num_elements;
    u32 offset_dw;
    u32 stride_dw;

    u32 NumDwords() const noexcept {
        return num_elements * stride_dw;
    }
};
using SharpTableList = boost::container::static_vector<SharpTable, 4>;

enum class SharpFetchPostOp : u8 {
"""
    text=one(text,anchor,table,"SharpTable insertion")

    text=one(text,
      "    SharpFetchPostOp post_op{};\n\n"
      "    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {\n"
      "        AmdGpu::Image image{};\n"
      "        if (!Fetch(info.flattened_ud_buf.data(), &image)) {\n"
      "            return AmdGpu::Image::Null(is_depth);\n"
      "        }\n",
      "    SharpFetchPostOp post_op{};\n"
      "    u16 array_size{};\n"
      "    u16 array_stride_dw{};\n\n"
      "    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {\n"
      "        if (array_size == 0) {\n"
      "            return GetElementSharp(info, 0);\n"
      "        }\n"
      "        for (u32 i = 0; i < array_size; ++i) {\n"
      "            const AmdGpu::Image image = GetElementSharp(info, i);\n"
      "            if (image) {\n"
      "                return image;\n"
      "            }\n"
      "        }\n"
      "        return AmdGpu::Image::Null(is_depth);\n"
      "    }\n\n"
      "    constexpr AmdGpu::Image GetElementSharp(const auto& info, u32 element) const noexcept {\n"
      "        AmdGpu::Image image{};\n"
      "        if (!Fetch(info.flattened_ud_buf.data(), &image, element * array_stride_dw)) {\n"
      "            return AmdGpu::Image::Null(is_depth);\n"
      "        }\n",
      "ImageResource table-aware GetSharp")

    text=one(text,
      "    constexpr bool Fetch(const u32* flatbuf, AmdGpu::Image* out) const {\n"
      "        if (!is_r128) {\n"
      "            // Fetch full 8 byte T#\n"
      "            return sharp_fetch.Fetch(flatbuf, out);\n"
      "        }\n"
      "        // Fetch r128 T# and fix pitch\n"
      "        if (!sharp_fetch.Fetch<4>(flatbuf, out)) {\n",
      "    constexpr bool Fetch(const u32* flatbuf, AmdGpu::Image* out, u32 element_off_dw = 0) const {\n"
      "        if (!is_r128) {\n"
      "            // Fetch full 8 byte T#\n"
      "            return sharp_fetch.Fetch(flatbuf, out, element_off_dw);\n"
      "        }\n"
      "        // Fetch r128 T# and fix pitch\n"
      "        if (!sharp_fetch.Fetch<4>(flatbuf, out, element_off_dw)) {\n",
      "ImageResource element Fetch")

    text=one(text,
      "    u32 NumBindings(const auto& info) const {\n"
      "        const AmdGpu::Image tsharp = GetSharp(info);\n"
      "        return (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex)\n"
      "                   ? (tsharp.last_level - tsharp.base_level + 1)\n"
      "                   : 1;\n"
      "    }\n",
      "    u32 NumBindings(const auto& info) const {\n"
      "        if (array_size != 0) {\n"
      "            return array_size + 1u;\n"
      "        }\n"
      "        const AmdGpu::Image tsharp = GetSharp(info);\n"
      "        return (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex &&\n"
      "                tsharp.last_level >= tsharp.base_level)\n"
      "                   ? (u32(tsharp.last_level) - u32(tsharp.base_level) + 1u)\n"
      "                   : 1u;\n"
      "    }\n",
      "ImageResource binding count")

    for token in ("SharpTableList","array_stride_dw","GetElementSharp","NUM_IMAGE_ARRAY_DESCRIPTORS"):
        if token not in text: raise RuntimeError("resource.h verification failed: "+token)
    return text.encode()


def adapt_info_h(data:bytes)->bytes:
    text=data.decode()
    if "SharpTableList sharp_tables" in text:
        raise RuntimeError("Dynamic T# info ABI already present")
    text=one(text,
      "    FMaskResourceList fmasks;\n",
      "    FMaskResourceList fmasks;\n    SharpTableList sharp_tables;\n",
      "InfoPersistent sharp tables")
    text=one(text,
      "        bnd.unified += buffers.size() + images.size() + samplers.size();\n",
      "        bnd.unified += buffers.size() + images.size() + samplers.size();\n"
      "        for (const auto& image : images) {\n"
      "            bnd.unified += image.array_size;\n"
      "        }\n",
      "Info descriptor count")
    text=one(text,
      "        if (srt_info.walker_func) {\n"
      "            srt_info.walker_func(user_data.data(), flattened_ud_buf.data());\n"
      "        }\n"
      "    }\n\n"
      "    void ReadTessConstantBuffer",
      "        if (srt_info.walker_func) {\n"
      "            srt_info.walker_func(user_data.data(), flattened_ud_buf.data());\n"
      "        }\n"
      "        if (!sharp_tables.empty()) {\n"
      "            RefreshSharpTables();\n"
      "        }\n"
      "    }\n\n"
      "    void RefreshSharpTables();\n\n"
      "    void ReadTessConstantBuffer",
      "Info table refresh")
    text=one(text,
      "DECLARE_ENUM_FLAG_OPERATORS(Info::ReadConstType);\n\n} // namespace Shader\n",
      "DECLARE_ENUM_FLAG_OPERATORS(Info::ReadConstType);\n\n"
      "u32 DynamicTsharpArraySize();\n\n"
      "} // namespace Shader\n",
      "dynamic T# size declaration")
    return text.encode()


def post_adapt_resource_patching(data:bytes)->bytes:
    text=data.decode()
    if "SHADPS4_DYNAMIC_TSHARP_ARRAY_SIZE" in text:
        raise RuntimeError("Dynamic T# runtime helpers already present")
    text=one(text,
      "#include <limits>\n",
      "#include <algorithm>\n#include <cstdlib>\n#include <cstring>\n#include <limits>\n"
      "#include \"core/memory.h\"\n",
      "dynamic T# helper includes")
    helper=r'''namespace Shader {

void Info::RefreshSharpTables() {
    constexpr VAddr AddressMask = 0xFFFFFFFFFFFFULL;
    auto* memory = Core::Memory::Instance();
    for (const SharpTable& table : sharp_tables) {
        u32* dst = flattened_ud_buf.data() + table.flatbuf_off;
        const u64 size = u64{table.NumDwords()} * sizeof(u32);
        const VAddr ptr =
            (VAddr{flattened_ud_buf[table.ptr_lo]} |
             (VAddr{flattened_ud_buf[table.ptr_hi]} << 32)) &
            AddressMask;
        const VAddr src = ptr + u64{table.offset_dw} * sizeof(u32);
        if (ptr == 0) {
            std::memset(dst, 0, size);
        } else {
            memory->CopySparseMemory(src, reinterpret_cast<u8*>(dst), size);
        }
    }
}

u32 DynamicTsharpArraySize() {
    static const u32 size = [] {
        const char* value = std::getenv("SHADPS4_DYNAMIC_TSHARP_ARRAY_SIZE");
        if (!value || !*value) {
            return 0u;
        }
        char* end = nullptr;
        const unsigned long parsed = std::strtoul(value, &end, 10);
        if (end == value || *end != '\0') {
            return 0u;
        }
        return std::min<u32>(static_cast<u32>(parsed), MAX_TSHARP_ARRAY_SIZE);
    }();
    return size;
}

} // namespace Shader

'''
    text=one(text,
      "namespace Shader::Optimization {\n",
      helper+"namespace Shader::Optimization {\n",
      "dynamic T# runtime helpers")
    text=one(text,
      "    const u32 array_size = info.hw_stage == HwStage::Compute ? DynamicTsharpArraySize() : 0;\n",
      "    const bool dynamic_tsharp_stage =\n"
      "        info.hw_stage == HwStage::Compute || info.hw_stage == HwStage::Fragment;\n"
      "    const u32 array_size = dynamic_tsharp_stage ? DynamicTsharpArraySize() : 0;\n",
      "dynamic T# shader stages")
    if "MatchSharpTable" not in text or "ImageArrayHandle" not in text:
        raise RuntimeError("core dynamic T# resource patch did not apply")
    if re.search(r'0x[0-9a-fA-F]{8,}',text[text.find("MatchSharpTable"):text.find("PatchBufferSharp")]):
        # Existing source may contain literals elsewhere; this bounded region must not
        # contain a program hash special case.
        raise RuntimeError("shader-specific dynamic T# match detected")
    return text.encode()


def adapt_raster_h(data:bytes)->bytes:
    text=data.decode()
    if "MaxImageDescriptors" in text:
        raise RuntimeError("Dynamic descriptor capacity already present")
    text=one(text,
      "    std::array<ImageBinding, Shader::NUM_IMAGES> image_bindings;\n",
      "    static constexpr u32 MaxImageDescriptors =\n"
      "        Shader::NUM_IMAGES + Shader::NUM_IMAGE_ARRAY_DESCRIPTORS;\n"
      "    static constexpr u32 MaxImageInfos = MaxImageDescriptors + Shader::NUM_SAMPLERS;\n"
      "    std::array<ImageBinding, MaxImageDescriptors> image_bindings;\n",
      "rasterizer image binding capacity")
    text=one(text,
      "    boost::container::static_vector<vk::DescriptorImageInfo, Shader::NUM_IMAGES> image_infos;\n",
      "    boost::container::static_vector<vk::DescriptorImageInfo, MaxImageInfos> image_infos;\n",
      "rasterizer descriptor info capacity")
    return text.encode()


def adapt_raster_cpp(data:bytes)->bytes:
    text=data.decode()
    if "image_desc.array_size != 0" in text:
        raise RuntimeError("Dynamic T# raster binding already present")

    text=one(text,
      "    boost::container::small_vector<u32, 8> image_descriptor_array_sizes;\n",
      "    boost::container::small_vector<u32, 8> image_descriptor_array_sizes;\n"
      "    boost::container::small_vector<bool, 8> image_descriptor_array_storage;\n",
      "descriptor type tracking")

    # Never let an old descriptor description survive a null slot.
    text=text.replace("image_bindings[num_images++].image_id = {};",
                      "image_bindings[num_images++] = {};")
    if text.count("image_bindings[num_images++] = {};") < 2:
        raise RuntimeError("null image slot reset coverage incomplete")

    anchor="""        const auto tsharp = image_desc.GetSharp(stage);
        const auto data_fmt = tsharp.GetDataFmt();
"""
    block="""        const auto tsharp = image_desc.GetSharp(stage);
        if (image_desc.array_size != 0) {
            const u32 num_bindings = image_desc.NumBindings(stage);
            const auto view_type = tsharp.GetViewType(image_desc.is_array);
            const bool integer_type = AmdGpu::IsInteger(tsharp.GetNumberFmt());
            const auto number_conversion = tsharp.GetNumberConversion();

            for (u32 i = 0; i < num_bindings; ++i) {
                auto& slot = image_bindings[num_images++];
                slot = {};
                if (i >= image_desc.array_size) {
                    continue;
                }

                const AmdGpu::Image element = image_desc.GetElementSharp(stage, i);
                const auto data_fmt = element.GetDataFmt();
                const auto num_fmt = element.GetNumberFmt();
                const bool compatible =
                    element && element.GetViewType(image_desc.is_array) == view_type &&
                    AmdGpu::IsInteger(num_fmt) == integer_type &&
                    element.GetNumberConversion() == number_conversion &&
                    data_fmt != AmdGpu::DataFormat::FormatInvalid &&
                    magic_enum::enum_contains(data_fmt) && magic_enum::enum_contains(num_fmt) &&
                    memory->IsValidGpuMapping(element.Address(), 0);
                if (!compatible) {
                    continue;
                }

                auto& [image_id, desc] = slot;
                std::construct_at(&desc, element, image_desc);
                image_id = texture_cache.FindImage(desc);
                auto* image = &texture_cache.GetImage(image_id);
                if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                    image_id = depth_image_id;
                    image = &texture_cache.GetImage(image_id);
                }
                if (image->binding.is_bound) {
                    image->binding.force_general |= image_desc.is_written;
                }
                image->binding.is_bound = 1u;
            }

            image_descriptor_array_sizes.push_back(num_bindings);
            image_descriptor_array_storage.push_back(false);
            continue;
        }
        const auto data_fmt = tsharp.GetDataFmt();
"""
    text=one(text,anchor,block,"dynamic descriptor array binding")

    # Every ordinary image contributes one logical descriptor write.
    text=one(text,
      "        image_descriptor_array_sizes.push_back(num_bindings);\n"
      "    }\n\n"
      "    // Second pass",
      "        image_descriptor_array_sizes.push_back(num_bindings);\n"
      "        image_descriptor_array_storage.push_back(image_desc.is_written);\n"
      "    }\n\n"
      "    // Second pass",
      "ordinary descriptor type tracking")

    # The two early-null branches also contribute a logical descriptor write.
    text=text.replace(
      "            image_descriptor_array_sizes.push_back(1);\n            continue;\n",
      "            image_descriptor_array_sizes.push_back(1);\n"
      "            image_descriptor_array_storage.push_back(image_desc.is_written);\n"
      "            continue;\n")
    if text.count("image_descriptor_array_storage.push_back(image_desc.is_written);") < 3:
        raise RuntimeError("descriptor type bookkeeping incomplete")

    old="""    u32 image_info_idx = first_image_idx;
    u32 image_binding_idx = 0;
    for (u32 array_size : image_descriptor_array_sizes) {
        const auto& [_, desc] = image_bindings[image_binding_idx];
        const bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;
"""
    new="""    u32 image_info_idx = first_image_idx;
    u32 image_binding_idx = 0;
    u32 logical_image_idx = 0;
    for (u32 array_size : image_descriptor_array_sizes) {
        const bool is_storage = image_descriptor_array_storage[logical_image_idx++];
"""
    text=one(text,old,new,"descriptor write type source")

    verify=(
      "image_desc.array_size != 0",
      "GetElementSharp(stage, i)",
      "MaxImageDescriptors",
      "image_descriptor_array_storage",
    )
    for token in verify:
        if token not in text and token!="MaxImageDescriptors":
            raise RuntimeError("rasterizer verification failed: "+token)
    return text.encode()


def adapt_serialization(data:bytes)->bytes:
    text=data.decode()
    def bump(name,s):
        m=re.search(rf"(static constexpr u32 {name} = )(\d+)(u;)",s)
        if not m: raise RuntimeError("Missing serialization version: "+name)
        return s[:m.start()]+m.group(1)+str(int(m.group(2))+1)+m.group(3)+s[m.end():]
    text=bump("ShaderBinaryVersion",text)
    text=bump("ShaderMetaVersion",text)
    return text.encode()


def install_runtime(base):
    # Retain GoW96's no-readback / SRT-root runtime contract and add the generic
    # descriptor-table size.  No controller input is generated.
    old_core=base.core_module
    def core(recover=True):
        m=old_core(recover)
        real_popen=m.subprocess.Popen
        def popen(args,**kwargs):
            if args and Path(str(args[0])).name=="shadps4":
                env=dict(kwargs.get("env") or os.environ)
                env["SHADPS4_DYNAMIC_TSHARP_ARRAY_SIZE"]="64"
                kwargs["env"]=env
            return real_popen(args,**kwargs)
        m.subprocess=types.SimpleNamespace(**dict(vars(m.subprocess),Popen=popen))
        return m
    base.core_module=core


def build_candidate(gow103,gow98,gow97,gow96,gow91,base,phase90,phase89,helper,work,logs):
    src,build,prior,pins,overrides=gow91.prior_state(base,phase90,phase89)

    # Retain the proven chain.
    buf_path=source_for(src,overrides,gow91.CPP);buf_data=buf_path.read_bytes()
    buf_after,_,buf_proof=gow91.adapt_source(buf_data)
    res_path=source_for(src,overrides,gow96.RESOURCE);res_data=res_path.read_bytes()
    res_after,_,phi_proof=gow96.adapt_resource(res_data,gow96.collaborator_source())
    flat_path=source_for(src,overrides,gow97.FLAT);flat_data=flat_path.read_bytes()
    flat_after,_,flat_proof=gow97.adapt_flatten(flat_data)

    originals={}
    for rel in DYNAMIC_FILES:
        p=source_for(src,overrides,rel)
        originals[rel]=p.read_bytes()

    overlay=work/"overlay"
    planned={gow91.CPP:buf_after,gow96.RESOURCE:res_after,gow97.FLAT:flat_after}
    for rel,raw in originals.items():
        planned[rel]=raw
    for rel,raw in planned.items():
        p=overlay/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)

    core=fetch(CORE_URL,ROOT/"scripts/gow104-dynamic-tsharp-core.patch",CORE_BLOB)
    helper.run(["git","apply","--check",core],logs/"dynamic-core-check.log",cwd=overlay,timeout=30)
    helper.run(["git","apply",core],logs/"dynamic-core-apply.log",cwd=overlay,timeout=30)

    # Manual ABI/runtime pieces adapted to this retained shadPS4 baseline.
    (overlay/RESOURCE_H).write_bytes(adapt_resource_h((overlay/RESOURCE_H).read_bytes()))
    (overlay/INFO_H).write_bytes(adapt_info_h((overlay/INFO_H).read_bytes()))
    (overlay/PATCH_CPP).write_bytes(post_adapt_resource_patching((overlay/PATCH_CPP).read_bytes()))
    (overlay/RASTER_H).write_bytes(adapt_raster_h((overlay/RASTER_H).read_bytes()))
    (overlay/RASTER_CPP).write_bytes(adapt_raster_cpp((overlay/RASTER_CPP).read_bytes()))
    (overlay/SERIAL_CPP).write_bytes(adapt_serialization((overlay/SERIAL_CPP).read_bytes()))

    # No title/program special cases may enter the dynamic-resource implementation.
    scan="\n".join((overlay/x).read_text(errors="replace") for x in
                    (RESOURCE_H,INFO_H,PATCH_CPP,RASTER_H,RASTER_CPP))
    if "CUSA" in scan or re.search(r"pgm_hash\s*==|pgm_hash\s*!=",scan):
        raise RuntimeError("Game/shader-specific dynamic descriptor logic detected")

    changed=tuple(dict.fromkeys((gow91.CPP,gow96.RESOURCE,gow97.FLAT)+DYNAMIC_FILES))
    binary,stats,mapping=phase89.chained_build(
        helper,build,src,overlay,work,logs,changed,
        prior,overrides,
        [overlay,gow91.BASE90/"overlay",phase90.BASE89/"overlay",
         phase89.BASE88/"overlay",base.BASE87/"overlay"])

    if any(gow91.sha(p)!=h for p,h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for rel,raw in originals.items():
        if source_for(src,overrides,rel).read_bytes()!=raw:
            raise RuntimeError("Original retained source changed: "+rel)
    for p,raw in ((buf_path,buf_data),(res_path,res_data),(flat_path,flat_data)):
        if p.read_bytes()!=raw:
            raise RuntimeError("Original retained chain source changed: "+str(p))

    runtime=build/"cpu-id-runtime"
    if not runtime.is_dir(): raise RuntimeError("CPU-ID runtime missing")
    (work/"cpu-id-runtime").symlink_to(runtime.resolve(),target_is_directory=True)
    helper.run(["ldd",binary],logs/"libraries.log",timeout=20)
    if "not found" in (logs/"libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
      gow104_dynamic_tsharp={
        "runtime_indexed_sampled_tables":True,
        "array_size":64,
        "stages":["compute","fragment"],
        "global_readbacks":False,
        "game_specific":False,
      },
      gow103_srt=flat_proof,
      gow96_phi_buffer_fix=phi_proof,
      phase91_buffer_fix=buf_proof,
      replacement_objects={str(k):str(v) for k,v in mapping.items()},
    )
    (work/"manifest.json").write_text(json.dumps(stats,indent=2)+"\n")
    (logs/"build-proof.json").write_text(json.dumps(stats,indent=2)+"\n")
    return binary,stats


def self_test(gow103,gow98,gow97,gow96,gow91):
    phase90=gow91.get_helper();phase89=phase90.get_helper();base=gow91.get_base(phase89)
    src,_,_,_,overrides=gow91.prior_state(base,phase90,phase89)

    # Proven chain transformations.
    gow91.adapt_source(source_for(src,overrides,gow91.CPP).read_bytes())
    gow96.adapt_resource(source_for(src,overrides,gow96.RESOURCE).read_bytes(),
                         gow96.collaborator_source())
    gow97.adapt_flatten(source_for(src,overrides,gow97.FLAT).read_bytes())

    # Manual dynamic pieces must transform the exact retained files without ambiguity.
    adapt_resource_h(source_for(src,overrides,RESOURCE_H).read_bytes())
    adapt_info_h(source_for(src,overrides,INFO_H).read_bytes())
    adapt_raster_h(source_for(src,overrides,RASTER_H).read_bytes())
    adapt_raster_cpp(source_for(src,overrides,RASTER_CPP).read_bytes())
    adapt_serialization(source_for(src,overrides,SERIAL_CPP).read_bytes())

    # Core patch context check against a temporary exact-source overlay.
    with tempfile.TemporaryDirectory(prefix="gow104-selftest-") as td:
        root=Path(td)
        for rel in CORE_FILES:
            p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(source_for(src,overrides,rel).read_bytes())
        core=fetch(CORE_URL,ROOT/"scripts/gow104-dynamic-tsharp-core.patch",CORE_BLOB)
        subprocess.run(["git","apply","--check",core],cwd=root,check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        subprocess.run(["git","apply",core],cwd=root,check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        post_adapt_resource_patching((root/PATCH_CPP).read_bytes())

    print("GOW104_SELF_TEST=PASS",flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--build-only",action="store_true")
    ap.add_argument("--restore",action="store_true")
    args=ap.parse_args()

    gow103=load_gow103()
    gow98=gow103.load_gow98()
    gow97=gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    gow103.install_fixes(gow98,gow97)
    gow96=gow97.load_gow96();gow91=gow96.load_gow91()

    if args.self_test:
        self_test(gow103,gow98,gow97,gow96,gow91);return 0

    phase90=gow91.get_helper();phase89=phase90.get_helper();base=gow91.get_base(phase89)
    gow91.install(base,phase90,phase89)
    base.build_candidate=lambda helper,work,logs:build_candidate(
        gow103,gow98,gow97,gow96,gow91,base,phase90,phase89,helper,work,logs)
    gow96.install_runtime(base)
    install_runtime(base)

    old=sys.argv[:]
    try:
        sys.argv=["gow104"]+(["--build-only"] if args.build_only else [])+(["--restore"] if args.restore else [])
        return base.main()
    finally:
        sys.argv=old


if __name__=="__main__":
    raise SystemExit(main())
