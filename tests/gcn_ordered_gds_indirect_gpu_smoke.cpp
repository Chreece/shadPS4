// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Real AMD GPU writes the 12-byte Vulkan dispatch-indirect command, transfers
// it to host, and uses exactly the production GDS command decoder and plan.
// NO guest shader, emulator launch, window, game or Vulkan spinlock.
#include <vulkan/vulkan.h>
#include "shader_recompiler/frontend/ordered_gds_device_plan.h"
#include <array>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
using namespace Shader::Gcn::OrderedGds;

static void Check(VkResult code, const char* where) {
    if (code != VK_SUCCESS)
        throw std::runtime_error(std::string(where) + " vk=" + std::to_string(code));
}
struct Storage {
    VkBuffer buffer{VK_NULL_HANDLE};
    VkDeviceMemory memory{VK_NULL_HANDLE};
    VkMemoryPropertyFlags flags{};
    void* ptr{};
};
int main() {
    VkInstance instance{VK_NULL_HANDLE};
    VkDevice device{VK_NULL_HANDLE};
    VkCommandPool pool{VK_NULL_HANDLE};
    VkFence fence{VK_NULL_HANDLE};
    Storage gpu{}, readback{};
    auto close = [&] {
        if (device) {
            if (fence) vkDestroyFence(device, fence, nullptr);
            if (pool) vkDestroyCommandPool(device, pool, nullptr);
            for (auto* r : {&gpu, &readback}) {
                if (r->ptr) vkUnmapMemory(device, r->memory);
                if (r->buffer) vkDestroyBuffer(device, r->buffer, nullptr);
                if (r->memory) vkFreeMemory(device, r->memory, nullptr);
            }
            vkDestroyDevice(device, nullptr);
        }
        if (instance) vkDestroyInstance(instance, nullptr);
    };
    try {
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
        app.pApplicationName = "shadPS4 ordered GDS 12-byte GPU indirect readback";
        app.apiVersion = VK_API_VERSION_1_2;
        VkInstanceCreateInfo ic{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
        ic.pApplicationInfo = &app;
        Check(vkCreateInstance(&ic, nullptr, &instance), "instance");
        std::uint32_t count{};
        Check(vkEnumeratePhysicalDevices(instance, &count, nullptr), "gpu count");
        if (!count) throw std::runtime_error("NO_VULKAN_GPU");
        std::vector<VkPhysicalDevice> devices(count);
        Check(vkEnumeratePhysicalDevices(instance, &count, devices.data()), "gpu list");
        VkPhysicalDevice physical{VK_NULL_HANDLE};
        std::uint32_t queue_family{};
        for (const auto candidate : devices) {
            VkPhysicalDeviceProperties prop{};
            vkGetPhysicalDeviceProperties(candidate, &prop);
            if (prop.vendorID != 0x1002 ||
                prop.deviceType != VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) continue;
            std::uint32_t n{};
            vkGetPhysicalDeviceQueueFamilyProperties(candidate, &n, nullptr);
            std::vector<VkQueueFamilyProperties> fam(n);
            vkGetPhysicalDeviceQueueFamilyProperties(candidate, &n, fam.data());
            for (std::uint32_t i=0; i<n; ++i) {
                if (fam[i].queueCount &&
                    (fam[i].queueFlags & VK_QUEUE_TRANSFER_BIT) &&
                    (fam[i].queueFlags & VK_QUEUE_COMPUTE_BIT)) {
                    physical=candidate;
                    queue_family=i;
                    break;
                }
            }
            if (physical) break;
        }
        if (!physical) throw std::runtime_error("NO_DISCRETE_AMD_GPU_QUEUE");
        const float priority=1.f;
        VkDeviceQueueCreateInfo qc{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
        qc.queueFamilyIndex=queue_family;
        qc.queueCount=1;
        qc.pQueuePriorities=&priority;
        VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
        dc.queueCreateInfoCount=1;
        dc.pQueueCreateInfos=&qc;
        Check(vkCreateDevice(physical, &dc, nullptr, &device), "device");
        VkQueue queue{};
        vkGetDeviceQueue(device,queue_family,0,&queue);
        VkPhysicalDeviceMemoryProperties mem{};
        vkGetPhysicalDeviceMemoryProperties(physical,&mem);
        constexpr VkDeviceSize bytes = sizeof(GpuIndirectDispatchCommand);
        const auto make = [&](Storage& s,VkBufferUsageFlags usage,bool host){
            VkBufferCreateInfo bc{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
            bc.size=bytes;
            bc.usage=usage;
            bc.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
            Check(vkCreateBuffer(device,&bc,nullptr,&s.buffer),"buffer");
            VkMemoryRequirements req{};
            vkGetBufferMemoryRequirements(device,s.buffer,&req);
            int chosen=-1;
            for(std::uint32_t i=0;i<mem.memoryTypeCount;++i) {
                if(!(req.memoryTypeBits & (1u<<i))) continue;
                const auto flags=mem.memoryTypes[i].propertyFlags;
                const auto need=host?VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT:
                                     VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
                if(!(flags & need)) continue;
                if(chosen<0 || (host && (flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))) {
                    chosen=int(i);
                    if(!host || (flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) break;
                }
            }
            if(chosen<0) throw std::runtime_error("NO_GPU_MEMORY_TYPE");
            s.flags=mem.memoryTypes[chosen].propertyFlags;
            VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
            ma.allocationSize=req.size;
            ma.memoryTypeIndex=std::uint32_t(chosen);
            Check(vkAllocateMemory(device,&ma,nullptr,&s.memory),"allocate");
            Check(vkBindBufferMemory(device,s.buffer,s.memory,0),"bind");
            if(host) Check(vkMapMemory(device,s.memory,0,VK_WHOLE_SIZE,0,&s.ptr),"map");
        };
        make(gpu,VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT |
                 VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                 VK_BUFFER_USAGE_TRANSFER_DST_BIT,false);
        make(readback,VK_BUFFER_USAGE_TRANSFER_DST_BIT,true);
        VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        pc.queueFamilyIndex=queue_family;
        Check(vkCreateCommandPool(device,&pc,nullptr,&pool),"pool");
        VkCommandBuffer cmd{};
        VkCommandBufferAllocateInfo ac{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        ac.commandPool=pool;
        ac.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        ac.commandBufferCount=1;
        Check(vkAllocateCommandBuffers(device,&ac,&cmd),"command");
        VkCommandBufferBeginInfo bc{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        bc.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        Check(vkBeginCommandBuffer(cmd,&bc),"begin");
        // Three distinct numbers written by GPU commands, not copied from CPU
        // registers or a fabricated CPU-side dispatch-extent structure.
        vkCmdFillBuffer(cmd,gpu.buffer,0,4,2u);
        vkCmdFillBuffer(cmd,gpu.buffer,4,4,3u);
        vkCmdFillBuffer(cmd,gpu.buffer,8,4,2u);
        VkBufferMemoryBarrier b{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
        b.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;
        b.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;
        b.srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;
        b.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;
        b.buffer=gpu.buffer;
        b.offset=0;
        b.size=bytes;
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,1,&b,0,nullptr);
        VkBufferCopy copy{0,0,bytes};
        vkCmdCopyBuffer(cmd,gpu.buffer,readback.buffer,1,&copy);
        b.buffer=readback.buffer;
        b.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;
        b.dstAccessMask=VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_HOST_BIT,0,0,nullptr,1,&b,0,nullptr);
        Check(vkEndCommandBuffer(cmd),"end");
        VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        Check(vkCreateFence(device,&fc,nullptr,&fence),"fence");
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount=1;
        si.pCommandBuffers=&cmd;
        Check(vkQueueSubmit(queue,1,&si,fence),"submit");
        const auto waited=vkWaitForFences(device,1,&fence,VK_TRUE,10'000'000'000ULL);
        if (waited==VK_TIMEOUT) {
            // Do not destroy in-flight GPU resources or invoke device idle.
            std::cerr<<"GPU_INDIRECT_FENCE_TIMEOUT\n";
            std::cerr.flush();
            std::_Exit(124);
        }
        Check(waited,"wait");
        if(!(readback.flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
            VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
            range.memory=readback.memory;
            range.offset=0;
            range.size=VK_WHOLE_SIZE;
            Check(vkInvalidateMappedMemoryRanges(device,1,&range),"invalidate");
        }
        std::array<std::byte,sizeof(GpuIndirectDispatchCommand)> raw{};
        std::memcpy(raw.data(),readback.ptr,bytes);
        const auto actual=DecodeGpuIndirectDispatchCommand(raw);
        const auto verified=GcnOrderedGdsDevicePlan::Require(actual,{64,1,1},9,4096);
        if(actual.x!=2 || actual.y!=3 || actual.z!=2 ||
           verified.workgroups!=12 || verified.waves!=12 ||
           !verified.lds_layout || verified.lds_layout->workgroups!=12)
            throw std::runtime_error("GPU_INDIRECT_GROUPS_OR_WAVE_BUDGET_MISMATCH");
        std::cout<<"GDS_REAL_AMD_GPU_INDIRECT_READBACK=PASS "
                 <<"gpu_written_groups=2,3,2 verified_wave64_slots=12 "
                 <<"LDS_owner_groups=12 no_guest_shader=YES\n";
        close();
        return 0;
    } catch(const std::exception& e) {
        std::cerr<<"GDS_AMD_GPU_INDIRECT_READBACK=FAIL "<<e.what()<<'\n';
        close();
        return 2;
    }
}
