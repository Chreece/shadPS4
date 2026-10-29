// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Headless Vulkan *transfer* smoke test; no guest shader, surface or game.
#include <vulkan/vulkan.h>
#include <array>
#include <cstdint>
#include <cstring>
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

int main() {
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    std::array<Buffer, 5> buffers{};
    try {
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

        std::cout << "GPU_TRANSFER_SMOKE=PASS device=" << selected.deviceName
                  << " vendor=AMD direct_capture_bytes=" << bytes
                  << " gpu_to_host=PASS host_to_gpu=PASS"
                  << " fence=PASS barrier=PASS noncoherent_handled=PASS"
                  << " guest_shader=NOT_RUN framebuffer=NOT_TOUCHED\n";
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
