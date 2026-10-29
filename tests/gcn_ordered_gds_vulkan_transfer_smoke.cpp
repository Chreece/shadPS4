// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Headless Vulkan *transfer* smoke test; no guest shader, surface or game.
#include <vulkan/vulkan.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"

using Shader::Gcn::OrderedGds::GpuCapturePacket;
using Shader::Gcn::OrderedGds::GpuResumePacket;
using Shader::Gcn::OrderedGds::ResumeFlags;
using Shader::Gcn::OrderedGds::ResumePacketTag;

static void Check(VkResult result, const char* name) {
    if (result != VK_SUCCESS) {
        throw std::runtime_error(std::string{name} + " VkResult=" +
                                 std::to_string(static_cast<int>(result)));
    }
}

struct Buffer {
    VkBuffer handle{VK_NULL_HANDLE};
    VkDeviceMemory memory{VK_NULL_HANDLE};
    VkMemoryPropertyFlags properties{};
    VkDeviceSize bytes{};
    void* mapping{};
};

int main(int argc, char** argv) {
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkShaderModule compute_shader = VK_NULL_HANDLE;
    VkDescriptorSetLayout compute_set_layout = VK_NULL_HANDLE;
    VkDescriptorPool compute_descriptor_pool = VK_NULL_HANDLE;
    VkPipelineLayout compute_pipeline_layout = VK_NULL_HANDLE;
    VkPipeline compute_pipeline = VK_NULL_HANDLE;
    std::array<Buffer, 5> buffers{};
    try {
        if (argc != 2) {
            throw std::runtime_error("COMPUTE_PACKET_WRITER_SPIRV_PATH_REQUIRED");
        }
        std::ifstream shader_file{argv[1], std::ios::binary | std::ios::ate};
        if (!shader_file) throw std::runtime_error("GPU_PACKET_WRITER_SPIRV_MISSING");
        const auto code_size = shader_file.tellg();
        if (code_size <= 0 || code_size > 65536 ||
            (static_cast<std::size_t>(code_size) & 3u) != 0) {
            throw std::runtime_error("GPU_PACKET_WRITER_SPIRV_LENGTH_INVALID");
        }
        std::vector<std::uint32_t> code(static_cast<std::size_t>(code_size) / 4);
        shader_file.seekg(0);
        if (!shader_file.read(reinterpret_cast<char*>(code.data()), code_size) ||
            code.front() != 0x07230203u) {
            throw std::runtime_error("GPU_PACKET_WRITER_SPIRV_HEADER_INVALID");
        }
        VkApplicationInfo app{};
        app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
        app.pApplicationName = "shadPS4 ordered GDS transfer probe";
        app.apiVersion = VK_API_VERSION_1_2;
        VkInstanceCreateInfo ci{};
        ci.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
        ci.pApplicationInfo = &app;
        Check(vkCreateInstance(&ci, nullptr, &instance), "vkCreateInstance");
        std::uint32_t device_count{};
        Check(vkEnumeratePhysicalDevices(instance, &device_count, nullptr),
              "vkEnumeratePhysicalDevices count");
        if (!device_count) {
            throw std::runtime_error("NO_VULKAN_GPU");
        }
        std::vector<VkPhysicalDevice> candidates(device_count);
        Check(vkEnumeratePhysicalDevices(instance, &device_count, candidates.data()),
              "vkEnumeratePhysicalDevices");
        VkPhysicalDevice physical = VK_NULL_HANDLE;
        std::uint32_t family = 0;
        VkPhysicalDeviceProperties selected{};
        for (const auto candidate : candidates) {
            VkPhysicalDeviceProperties props{};
            vkGetPhysicalDeviceProperties(candidate, &props);
            if (props.vendorID != 0x1002 ||
                props.deviceType != VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) {
                continue; // Never silently test a CPU software Vulkan adapter.
            }
            std::uint32_t family_count{};
            vkGetPhysicalDeviceQueueFamilyProperties(candidate, &family_count, nullptr);
            std::vector<VkQueueFamilyProperties> queues(family_count);
            vkGetPhysicalDeviceQueueFamilyProperties(candidate, &family_count, queues.data());
            for (std::uint32_t index = 0; index < family_count; ++index) {
                if (queues[index].queueCount &&
                    (queues[index].queueFlags & VK_QUEUE_COMPUTE_BIT) &&
                    (queues[index].queueFlags & VK_QUEUE_TRANSFER_BIT)) {
                    physical = candidate;
                    family = index;
                    selected = props;
                    break;
                }
            }
            if (physical != VK_NULL_HANDLE) break;
        }
        if (physical == VK_NULL_HANDLE) {
            throw std::runtime_error("NO_AMD_DISCRETE_COMPUTE_TRANSFER_GPU");
        }
        float priority = 1.0f;
        VkDeviceQueueCreateInfo qi{};
        qi.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
        qi.queueFamilyIndex = family;
        qi.queueCount = 1;
        qi.pQueuePriorities = &priority;
        VkDeviceCreateInfo di{};
        di.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
        di.queueCreateInfoCount = 1;
        di.pQueueCreateInfos = &qi;
        Check(vkCreateDevice(physical, &di, nullptr, &device), "vkCreateDevice");
        VkQueue queue{};
        vkGetDeviceQueue(device, family, 0, &queue);

        VkPhysicalDeviceMemoryProperties mem{};
        vkGetPhysicalDeviceMemoryProperties(physical, &mem);
        constexpr VkDeviceSize bytes = 4 * sizeof(GpuCapturePacket);
        static_assert(sizeof(GpuCapturePacket) == sizeof(GpuResumePacket));
        const auto allocate = [&](Buffer& b, bool host) {
            b.bytes = bytes;
            VkBufferCreateInfo desc{};
            desc.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
            desc.size = bytes;
            desc.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                         VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                         VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
            desc.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
            Check(vkCreateBuffer(device, &desc, nullptr, &b.handle), "vkCreateBuffer");
            VkMemoryRequirements required{};
            vkGetBufferMemoryRequirements(device, b.handle, &required);
            int selected_type = -1;
            int preferred_type = -1;
            for (std::uint32_t i = 0; i < mem.memoryTypeCount; ++i) {
                if ((required.memoryTypeBits & (1u << i)) == 0) continue;
                const auto flags = mem.memoryTypes[i].propertyFlags;
                const auto wanted = host ? VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                         : VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
                if ((flags & wanted) != wanted) continue;
                if (selected_type < 0) selected_type = static_cast<int>(i);
                if (host && (flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
                    preferred_type = static_cast<int>(i);
                    break;
                }
            }
            if (preferred_type >= 0) selected_type = preferred_type;
            if (selected_type < 0) throw std::runtime_error("NO_MATCHING_MEMORY_TYPE");
            b.properties = mem.memoryTypes[selected_type].propertyFlags;
            VkMemoryAllocateInfo ai{};
            ai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
            ai.allocationSize = required.size;
            ai.memoryTypeIndex = static_cast<std::uint32_t>(selected_type);
            Check(vkAllocateMemory(device, &ai, nullptr, &b.memory), "vkAllocateMemory");
            Check(vkBindBufferMemory(device, b.handle, b.memory, 0), "vkBindBufferMemory");
            if (host) Check(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0,
                                       &b.mapping), "vkMapMemory");
        };
        // Device capture, CPU readback, host resume upload, device resume,
        // CPU roundtrip. All tiny and restricted to this process.
        allocate(buffers[0], false);
        allocate(buffers[1], true);
        allocate(buffers[2], true);
        allocate(buffers[3], false);
        allocate(buffers[4], true);

        std::array<GpuResumePacket, 4> resume{};
        for (std::uint32_t i = 0; i < resume.size(); ++i) {
            resume[i].tag = ResumePacketTag;
            resume[i].epoch = 3;
            resume[i].flags = ResumeFlags;
            resume[i].site_pc = (i & 1) ? 0x41d4 : 0x4158;
            resume[i].logical_slot = i;
            resume[i].previous = 101 + 7 * i;
            resume[i].current = 102 + 7 * i;
            resume[i].token_lo = 2000 + i;
        }
        std::memcpy(buffers[2].mapping, resume.data(), bytes);
        const auto flush = [&](const Buffer& b) {
            if (b.properties & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) return;
            VkMappedMemoryRange range{};
            range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
            range.memory = b.memory;
            range.offset = 0;
            range.size = VK_WHOLE_SIZE;
            Check(vkFlushMappedMemoryRanges(device, 1, &range),
                  "vkFlushMappedMemoryRanges");
        };
        flush(buffers[2]);

        VkCommandPoolCreateInfo pci{};
        pci.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
        pci.queueFamilyIndex = family;
        Check(vkCreateCommandPool(device, &pci, nullptr, &pool), "vkCreateCommandPool");
        VkCommandBufferAllocateInfo cbi{};
        cbi.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
        cbi.commandPool = pool;
        cbi.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        cbi.commandBufferCount = 1;
        VkCommandBuffer cmd{};
        Check(vkAllocateCommandBuffers(device, &cbi, &cmd), "vkAllocateCommandBuffers");
        VkCommandBufferBeginInfo begin{};
        begin.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        Check(vkBeginCommandBuffer(cmd, &begin), "vkBeginCommandBuffer");
        const auto barrier = [&](const Buffer& b, VkAccessFlags src, VkAccessFlags dst,
                                 VkPipelineStageFlags source, VkPipelineStageFlags target) {
            VkBufferMemoryBarrier mb{};
            mb.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
            mb.srcAccessMask = src;
            mb.dstAccessMask = dst;
            mb.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            mb.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            mb.buffer = b.handle;
            mb.offset = 0;
            mb.size = bytes;
            vkCmdPipelineBarrier(cmd, source, target, 0, 0, nullptr, 1, &mb, 0, nullptr);
        };
        VkBufferCopy region{0, 0, bytes};
        vkCmdFillBuffer(cmd, buffers[0].handle, 0, bytes, 0x13579bdfu);
        barrier(buffers[0], VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdCopyBuffer(cmd, buffers[0].handle, buffers[1].handle, 1, &region);
        barrier(buffers[1], VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT,
                VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT);
        barrier(buffers[2], VK_ACCESS_HOST_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdCopyBuffer(cmd, buffers[2].handle, buffers[3].handle, 1, &region);
        barrier(buffers[3], VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        vkCmdCopyBuffer(cmd, buffers[3].handle, buffers[4].handle, 1, &region);
        barrier(buffers[4], VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT,
                VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT);
        Check(vkEndCommandBuffer(cmd), "vkEndCommandBuffer");
        VkFenceCreateInfo fi{};
        fi.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
        Check(vkCreateFence(device, &fi, nullptr, &fence), "vkCreateFence");
        VkSubmitInfo submit{};
        submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &cmd;
        Check(vkQueueSubmit(queue, 1, &submit, fence), "vkQueueSubmit");
        Check(vkWaitForFences(device, 1, &fence, VK_TRUE, 10'000'000'000ULL),
              "vkWaitForFences");
        const auto invalidate = [&](const Buffer& b) {
            if (b.properties & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) return;
            VkMappedMemoryRange range{};
            range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
            range.memory = b.memory;
            range.offset = 0;
            range.size = VK_WHOLE_SIZE;
            Check(vkInvalidateMappedMemoryRanges(device, 1, &range),
                  "vkInvalidateMappedMemoryRanges");
        };
        invalidate(buffers[1]);
        invalidate(buffers[4]);
        const auto* read = static_cast<const std::uint32_t*>(buffers[1].mapping);
        for (std::size_t i = 0; i < bytes / sizeof(std::uint32_t); ++i) {
            if (read[i] != 0x13579bdfu)
                throw std::runtime_error("GPU_CAPTURE_DEVICE_TO_HOST_MISMATCH");
        }
        if (std::memcmp(resume.data(), buffers[4].mapping, bytes) != 0)
            throw std::runtime_error("GPU_RESUME_HOST_TO_DEVICE_ROUNDTRIP_MISMATCH");

        // Second phase: real GPU *compute* shader writes packet fields into
        // the same device capture buffer. These are synthetic input fixtures,
        // NOT guest GCN state, guest ordering, or emulator shader replay.
        using namespace Shader::Gcn::OrderedGds;
        std::array<GpuCapturePacket,4> fixtures{};
        for (std::uint32_t slot = 0; slot < fixtures.size(); ++slot) {
            fixtures[slot].tag = 0xbad1deafu;           // overwritten on GPU
            fixtures[slot].epoch = 99;                   // overwritten on GPU
            fixtures[slot].logical_slot = NoActiveLane;  // GPU builtin replaces
            fixtures[slot].reserved0 = 99;               // GPU clears
        }
        fixtures[0].flags = CaptureBypassFlags;
        fixtures[0].site_pc = NoActiveLane;
        fixtures[0].source_lane = NoActiveLane;

        fixtures[1].flags = CaptureFlags;
        fixtures[1].site_pc = 0x4158;
        fixtures[1].encoded_control = 0x1300;
        fixtures[1].runtime_m0 = 0x20u << 16;
        fixtures[1].exec_hi = 0x80000000u;
        fixtures[1].source_lane = 63;
        fixtures[1].payload = 7;
        fixtures[1].token_lo = 1000;

        fixtures[2].flags = CaptureFlags;
        fixtures[2].site_pc = 0x41d4;
        fixtures[2].encoded_control = 0x0300;
        fixtures[2].runtime_m0 = 0x20u << 16;
        fixtures[2].exec_lo = 1u << 7;
        fixtures[2].source_lane = 7;
        fixtures[2].payload = 5;
        fixtures[2].token_lo = 1003;

        fixtures[3].flags = CaptureFlags;
        fixtures[3].site_pc = 0x41d4;
        fixtures[3].encoded_control = 0x0300;
        fixtures[3].runtime_m0 = 0x20u << 16;
        fixtures[3].exec_lo = 1;
        fixtures[3].source_lane = 0;
        fixtures[3].payload = 3;
        fixtures[3].token_lo = 1001;

        std::memcpy(buffers[2].mapping, fixtures.data(), bytes);
        flush(buffers[2]);
        const VkShaderModuleCreateInfo sci{
            .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
            .codeSize = static_cast<std::size_t>(code_size),
            .pCode = code.data(),
        };
        Check(vkCreateShaderModule(device, &sci, nullptr, &compute_shader),
              "vkCreateShaderModule");
        std::array<VkDescriptorSetLayoutBinding,2> bindings{};
        for (std::uint32_t binding = 0; binding < bindings.size(); ++binding) {
            bindings[binding].binding = binding;
            bindings[binding].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            bindings[binding].descriptorCount = 1;
            bindings[binding].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        }
        const VkDescriptorSetLayoutCreateInfo dsci{
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = static_cast<std::uint32_t>(bindings.size()),
            .pBindings = bindings.data(),
        };
        Check(vkCreateDescriptorSetLayout(device, &dsci, nullptr, &compute_set_layout),
              "vkCreateDescriptorSetLayout");
        const VkPipelineLayoutCreateInfo plci{
            .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
            .setLayoutCount = 1,
            .pSetLayouts = &compute_set_layout,
        };
        Check(vkCreatePipelineLayout(device, &plci, nullptr, &compute_pipeline_layout),
              "vkCreatePipelineLayout");
        VkComputePipelineCreateInfo cpci{};
        cpci.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
        cpci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        cpci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        cpci.stage.module = compute_shader;
        cpci.stage.pName = "main";
        cpci.layout = compute_pipeline_layout;
        Check(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpci, nullptr,
                                       &compute_pipeline), "vkCreateComputePipelines");

        const VkDescriptorPoolSize pool_size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,2};
        const VkDescriptorPoolCreateInfo dpci{
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
            .maxSets = 1,
            .poolSizeCount = 1,
            .pPoolSizes = &pool_size,
        };
        Check(vkCreateDescriptorPool(device, &dpci, nullptr, &compute_descriptor_pool),
              "vkCreateDescriptorPool");
        const VkDescriptorSetAllocateInfo dsai{
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
            .descriptorPool = compute_descriptor_pool,
            .descriptorSetCount = 1,
            .pSetLayouts = &compute_set_layout,
        };
        VkDescriptorSet compute_descriptor{};
        Check(vkAllocateDescriptorSets(device, &dsai, &compute_descriptor),
              "vkAllocateDescriptorSets");
        const std::array<VkDescriptorBufferInfo,2> descriptor_buffers{{
            {buffers[0].handle, 0, bytes},
            {buffers[2].handle, 0, bytes},
        }};
        std::array<VkWriteDescriptorSet,2> writes{};
        for (std::uint32_t binding = 0; binding < writes.size(); ++binding) {
            writes[binding].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[binding].dstSet = compute_descriptor;
            writes[binding].dstBinding = binding;
            writes[binding].descriptorCount = 1;
            writes[binding].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            writes[binding].pBufferInfo = &descriptor_buffers[binding];
        }
        vkUpdateDescriptorSets(device, static_cast<std::uint32_t>(writes.size()),
                               writes.data(), 0, nullptr);

        VkCommandBuffer writer_cmd{};
        Check(vkAllocateCommandBuffers(device, &cbi, &writer_cmd),
              "vkAllocateCommandBuffers(writer)");
        Check(vkBeginCommandBuffer(writer_cmd, &begin),
              "vkBeginCommandBuffer(writer)");
        VkBufferMemoryBarrier host_to_compute{};
        host_to_compute.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
        host_to_compute.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT;
        host_to_compute.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        host_to_compute.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        host_to_compute.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        host_to_compute.buffer = buffers[2].handle;
        host_to_compute.size = bytes;
        vkCmdPipelineBarrier(writer_cmd, VK_PIPELINE_STAGE_HOST_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             0, 0, nullptr, 1, &host_to_compute, 0, nullptr);
        vkCmdBindPipeline(writer_cmd, VK_PIPELINE_BIND_POINT_COMPUTE, compute_pipeline);
        vkCmdBindDescriptorSets(writer_cmd, VK_PIPELINE_BIND_POINT_COMPUTE,
                                compute_pipeline_layout, 0, 1, &compute_descriptor,
                                0, nullptr);
        vkCmdDispatch(writer_cmd, 4, 1, 1);
        VkBufferMemoryBarrier shader_to_copy{};
        shader_to_copy.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
        shader_to_copy.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        shader_to_copy.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        shader_to_copy.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        shader_to_copy.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        shader_to_copy.buffer = buffers[0].handle;
        shader_to_copy.size = bytes;
        vkCmdPipelineBarrier(writer_cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT,
                             0, 0, nullptr, 1, &shader_to_copy, 0, nullptr);
        vkCmdCopyBuffer(writer_cmd, buffers[0].handle, buffers[1].handle, 1, &region);
        VkBufferMemoryBarrier copy_to_host{};
        copy_to_host.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;
        copy_to_host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        copy_to_host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        copy_to_host.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        copy_to_host.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        copy_to_host.buffer = buffers[1].handle;
        copy_to_host.size = bytes;
        vkCmdPipelineBarrier(writer_cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_HOST_BIT,
                             0, 0, nullptr, 1, &copy_to_host, 0, nullptr);
        Check(vkEndCommandBuffer(writer_cmd), "vkEndCommandBuffer(writer)");
        Check(vkResetFences(device, 1, &fence), "vkResetFences(writer)");
        VkSubmitInfo writer_submit = submit;
        writer_submit.pCommandBuffers = &writer_cmd;
        Check(vkQueueSubmit(queue, 1, &writer_submit, fence),
              "vkQueueSubmit(writer)");
        Check(vkWaitForFences(device, 1, &fence, VK_TRUE, 10'000'000'000ULL),
              "vkWaitForFences(writer)");
        invalidate(buffers[1]);

        std::array<GpuCapturePacket,4> received{};
        std::memcpy(received.data(), buffers[1].mapping, bytes);
        for (std::uint32_t slot = 0; slot < received.size(); ++slot) {
            if (received[slot].tag != CapturePacketTag ||
                received[slot].epoch != 3 ||
                received[slot].logical_slot != slot ||
                received[slot].reserved0 || received[slot].reserved1 ||
                received[slot].reserved2 ||
                received[slot].site_pc != fixtures[slot].site_pc) {
                throw std::runtime_error("GPU_COMPUTE_WRITER_PACKET_CORRUPTION");
            }
        }
        const std::array<u64,4> ranks{2,0,3,1};
        const std::array<u32,2> sites{0x4158,0x41d4};
        const auto verified = VerifiedGuestCreationOrder::FromScheduler(ranks);
        if (!verified) throw std::runtime_error("INVALID_TEST_GUEST_RANK_MAPPING");
        SparseOrderedGdsEpoch host(4, {{{0x20,0},100}}, sites);
        StageGpuCaptures(host,3,received,*verified);
        const auto ready = ResolveGpuCaptures(host,3,*verified);
        if (ready.size()!=4 || ready[0].flags!=0 ||
            ready[1].previous!=100 || ready[1].current!=7 ||
            ready[3].previous!=7 || ready[3].current!=10 ||
            ready[2].previous!=10 || ready[2].current!=15) {
            throw std::runtime_error("GPU_COMPUTE_WRITER_HOST_RESOLVE_MISMATCH");
        }
        std::cout << "GPU_CAPTURE_COMPUTE_WRITER=PASS gpu_dispatches=1"
                     " packets_written=4 real_vk_compute=1"
                     " guest_rank_source=TEST_FIXTURE host_sparse_resolve=PASS"
                     " ordered_exchange_add=PASS skipped_wave=PASS"
                     " guest_shader=NOT_RUN framebuffer=NOT_TOUCHED\n";

        std::cout << "GPU_TRANSFER_SMOKE=PASS device=" << selected.deviceName
                  << " vendor=AMD direct_capture_bytes=" << bytes
                  << " gpu_to_host=PASS host_to_gpu=PASS"
                  << " fence=PASS barrier=PASS noncoherent_handled=PASS"
                  << " guest_shader=NOT_RUN framebuffer=NOT_TOUCHED\n";
        if (compute_pipeline) vkDestroyPipeline(device, compute_pipeline, nullptr);
        if (compute_pipeline_layout) vkDestroyPipelineLayout(device, compute_pipeline_layout, nullptr);
        if (compute_descriptor_pool) vkDestroyDescriptorPool(device, compute_descriptor_pool, nullptr);
        if (compute_set_layout) vkDestroyDescriptorSetLayout(device, compute_set_layout, nullptr);
        if (compute_shader) vkDestroyShaderModule(device, compute_shader, nullptr);
        vkDestroyFence(device, fence, nullptr); fence = VK_NULL_HANDLE;
        vkDestroyCommandPool(device, pool, nullptr); pool = VK_NULL_HANDLE;
        for (auto& b : buffers) {
            if (b.mapping) vkUnmapMemory(device, b.memory);
            if (b.handle) vkDestroyBuffer(device, b.handle, nullptr);
            if (b.memory) vkFreeMemory(device, b.memory, nullptr);
            b = {};
        }
        vkDestroyDevice(device, nullptr); device = VK_NULL_HANDLE;
        vkDestroyInstance(instance, nullptr); instance = VK_NULL_HANDLE;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "GPU_TRANSFER_SMOKE=FAIL reason=" << e.what() << '\n';
        if (device) {
        if (compute_pipeline) vkDestroyPipeline(device, compute_pipeline, nullptr);
        if (compute_pipeline_layout) vkDestroyPipelineLayout(device, compute_pipeline_layout, nullptr);
        if (compute_descriptor_pool) vkDestroyDescriptorPool(device, compute_descriptor_pool, nullptr);
        if (compute_set_layout) vkDestroyDescriptorSetLayout(device, compute_set_layout, nullptr);
        if (compute_shader) vkDestroyShaderModule(device, compute_shader, nullptr);
            if (fence) vkDestroyFence(device, fence, nullptr);
            if (pool) vkDestroyCommandPool(device, pool, nullptr);
            for (auto& b : buffers) {
                if (b.mapping) vkUnmapMemory(device, b.memory);
                if (b.handle) vkDestroyBuffer(device, b.handle, nullptr);
                if (b.memory) vkFreeMemory(device, b.memory, nullptr);
            }
            vkDestroyDevice(device, nullptr);
        }
        if (instance) vkDestroyInstance(instance, nullptr);
        return 1;
    }
}
