// SPDX-License-Identifier: MIT
// Standalone RenderDoc control/replay helper. No emulator settings are written.
#include <renderdoc_replay.h>
#include <renderdoc_app.h>
#include <vulkan/vulkan.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <thread>
#include <dlfcn.h>

#ifdef PES_GPU_REPLAY_HELPER
REPLAY_PROGRAM_MARKER()
#endif

using json = nlohmann::json;
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
static std::string text(const rdcstr& s) { return std::string(s.c_str(), s.size()); }
static std::string id(ResourceId r) { static std::map<ResourceId, size_t> ids; return std::to_string(ids.emplace(r, ids.size()).first->second); }
static void save(const fs::path& p, const json& j) { std::ofstream(p) << j.dump(2) << '\n'; }
static void check(ResultDetails r) {
    if (r.code != ResultCode::Succeeded) throw std::runtime_error(r.internal_msg ? text(*r.internal_msg) : "RenderDoc result " + std::to_string(int(r.code)));
}
static void bytes(const fs::path& path, const bytebuf& data) {
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(data.data()), data.size());
    if (!out) throw std::runtime_error("Cannot write " + path.string());
}
template<class T> struct Release { void operator()(T* p) const { if(p) p->Shutdown(); } };

static void capture(uint32_t pid, const fs::path& output) {
    uint32_t ident = 0;
    for (unsigned tries = 0; tries < 32; ++tries) {
        ident = RENDERDOC_EnumerateRemoteTargets("localhost", ident);
        if (!ident) break;
        std::unique_ptr<ITargetControl, Release<ITargetControl>> control(
            RENDERDOC_CreateTargetControl("localhost", ident, "PES automatic capture", false));
        if (!control || control->GetPID() != pid) continue;
        if (!control->Connected()) throw std::runtime_error("RenderDoc target already controlled");
        control->TriggerCapture(1);
        const auto deadline = Clock::now() + std::chrono::seconds(45);
        while (control->Connected() && Clock::now() < deadline) {
            const auto msg = control->ReceiveMessage(nullptr);
            if (msg.type == TargetControlMessageType::NewCapture) {
                const auto& c = msg.newCapture;
                save(output, {{"pid", pid}, {"path", text(c.path)}, {"local", c.local},
                              {"frame", c.frameNumber}, {"bytes", c.byteSize}});
                std::cout << "PES_GPU_CAPTURE=PASS\n";
                return;
            }
        }
        throw std::runtime_error("GPU frame capture did not finish within 45 seconds");
    }
    throw std::runtime_error("No RenderDoc target matched the owned emulator PID");
}

static void flatten(const rdcarray<ActionDescription>& tree,
                    std::vector<const ActionDescription*>& actions, json& inventory) {
    for (const auto& a : tree) {
        inventory.push_back({{"event", a.eventId}, {"name", text(a.customName)},
                             {"flags", uint32_t(a.flags)}, {"indices", a.numIndices},
                             {"instances", a.numInstances}});
        if ((a.flags & (ActionFlags::Drawcall | ActionFlags::Dispatch)) != ActionFlags::NoFlags)
            actions.push_back(&a);
        flatten(a.children, actions, inventory);
    }
}

static json descriptor(const Descriptor& d) {
    return {{"resource", id(d.resource)}, {"type", int(d.type)}, {"offset", d.byteOffset},
            {"size", d.byteSize}, {"mip", d.firstMip}, {"slice", d.firstSlice},
            {"format", text(d.format.Name())}};
}

static void replay(const fs::path& capture_path, const fs::path& out, bool fixture) {
    fs::create_directories(out);
    std::unique_ptr<ICaptureFile, Release<ICaptureFile>> file(RENDERDOC_OpenCaptureFile());
    check(file->OpenFile(capture_path.c_str(), "rdc", nullptr));
    std::cerr << "PES_GPU_STAGE=open_replay\n";
    auto opened = file->OpenCapture(ReplayOptions(), nullptr);
    check(opened.first);
    std::unique_ptr<IReplayController, Release<IReplayController>> controller(opened.second);
    auto* c = controller.get();
    std::cerr << "PES_GPU_STAGE=enumerate_actions\n";
    json result = {{"actions", json::array()}, {"draws", json::array()},
                   {"textures", json::array()}, {"shaders", json::object()}};
    std::vector<const ActionDescription*> actions;
    flatten(c->GetRootActions(), actions, result["actions"]);
    std::cerr << "PES_GPU_STAGE=actions_ready\n";
    save(out / "frame.json", result);
    if (fixture) {
        uint32_t last = 0;
        for(const auto& a : result["actions"]) last = std::max(last, a["event"].get<uint32_t>());
        c->SetFrameEvent(last, true);
        bool matched = false;
        for(const auto& b : c->GetBuffers()) {
            if(b.length != 4096) continue;
            const auto data = c->GetBufferData(b.resourceId, 0, 4096);
            uint32_t first = 0;
            if(data.size() >= 4) std::memcpy(&first, data.data(), 4);
            if(first == 0x12345678 && data.size() == 4096) matched = true;
        }
        if(!matched) throw std::runtime_error("Captured GPU buffer did not replay its known fill");
        result["fixture_buffer_verified"] = true;
        save(out / "frame.json", result);
        std::cout << "PES_GPU_PREFLIGHT=PASS\n";
        return;
    }
    std::map<ResourceId, TextureDescription> textures;
    for(const auto& t : c->GetTextures()) {
        textures.emplace(t.resourceId, t);
        result["textures"].push_back({{"resource", id(t.resourceId)}, {"width", t.width},
            {"height", t.height}, {"depth", t.depth}, {"mips", t.mips},
            {"format", text(t.format.Name())}});
    }
    const size_t limit = std::min<size_t>(actions.size(), 96);
    result["total_draws_dispatches"] = actions.size();
    result["limited"] = actions.size() > limit;
    size_t image_count = 0, buffer_bytes = 0;
    std::set<std::string> saved_shaders;
    auto buffer = [&](ResourceId resource, uint64_t offset, uint64_t size, const std::string& name) {
        if(resource == ResourceId::Null() || !size || buffer_bytes >= 8 * 1024 * 1024) return;
        const auto data = c->GetBufferData(resource, offset, std::min<uint64_t>(size, 16384));
        bytes(out / (name + ".bin"), data);
        buffer_bytes += data.size();
    };
    auto texture = [&](const Descriptor& d, const std::string& name, bool output) {
        json j = descriptor(d);
        const auto it = textures.find(d.resource);
        if(it == textures.end()) return j;
        const auto& t = it->second;
        if(t.width > 4096 || t.height > 4096 || t.depth > 1 || image_count >= 96) return j;
        const Subresource sub(d.firstMip, d.firstSlice, 0);
        const auto component = d.format.compType == CompType::Typeless ? t.format.compType : d.format.compType;
        const auto minmax = c->GetMinMax(d.resource, sub, component);
        auto pixel = [&](const PixelValue& p) {
            json values = json::array();
            for(unsigned channel = 0; channel < 4; ++channel) {
                if(component == CompType::UInt) values.push_back(p.uintValue[channel]);
                else if(component == CompType::SInt) values.push_back(p.intValue[channel]);
                else values.push_back(p.floatValue[channel]);
            }
            return values;
        };
        j["component_type"] = int(component);
        j["min"] = pixel(minmax.first);
        j["max"] = pixel(minmax.second);
        if(output || image_count < 48) {
            TextureSave settings;
            settings.resourceId = d.resource;
            settings.mip = d.firstMip;
            settings.slice.sliceIndex = d.firstSlice;
            settings.destType = FileType::PNG;
            const auto path = out / (name + ".png");
            auto status = c->SaveTexture(settings, path.c_str());
            j["image_status"] = std::to_string(int(status.code));
            if(status.code == ResultCode::Succeeded) j["image"] = path.filename().string();
            ++image_count;
        }
        return j;
    };
    for(size_t i = 0; i < limit; ++i) {
        const auto& a = *actions[i];
        c->SetFrameEvent(a.eventId, true);
        const auto& vk = *c->GetVulkanPipelineState();
        const std::string prefix = "event-" + std::to_string(a.eventId);
        json j = {{"event", a.eventId}, {"indices", a.numIndices}, {"instances", a.numInstances},
                  {"outputs", json::array()}, {"resources", json::array()},
                  {"stages", json::object()}, {"viewports", json::array()}, {"blends", json::array()},
                  {"depth_test", vk.depthStencil.depthTestEnable}, {"depth_write", vk.depthStencil.depthWriteEnable},
                  {"depth_compare", int(vk.depthStencil.depthFunction)}, {"stencil_test", vk.depthStencil.stencilTestEnable},
                  {"discard", vk.rasterizer.rasterizerDiscardEnable}, {"cull", int(vk.rasterizer.cullMode)}};
        for(const auto& v : vk.viewportScissor.viewportScissors)
            j["viewports"].push_back({{"viewport", {v.vp.x,v.vp.y,v.vp.width,v.vp.height,v.vp.minDepth,v.vp.maxDepth}},
                                     {"scissor", {v.scissor.x,v.scissor.y,v.scissor.width,v.scissor.height}}});
        for(const auto& b : vk.colorBlend.blends)
            j["blends"].push_back({{"enabled", b.enabled}, {"write_mask", b.writeMask},
                {"src", int(b.colorBlend.source)}, {"dst", int(b.colorBlend.destination)}, {"op", int(b.colorBlend.operation)}});
        bytes(out / (prefix + "-push.bin"), vk.pushconsts);
        unsigned slot = 0;
        for(const auto& attachment : vk.currentPass.renderpass.colorAttachments) {
            if(attachment >= vk.currentPass.framebuffer.attachments.size()) continue;
            const auto& d = vk.currentPass.framebuffer.attachments[attachment];
            if(d.resource != ResourceId::Null()) j["outputs"].push_back(texture(d, prefix + "-output-" + std::to_string(slot), true));
            ++slot;
        }
        for(const auto* shader_state : {&vk.vertexShader, &vk.fragmentShader, &vk.computeShader}) {
            const auto stage = shader_state->stage;
            const auto* reflection = shader_state->reflection;
            if(!reflection) continue;
            const auto shader = shader_state->resourceId;
            const auto key = id(shader);
            j["stages"][std::to_string(int(stage))] = key;
            if(saved_shaders.insert(key).second) {
                bytes(out / ("shader-" + key + ".bin"), reflection->rawBytes);
                const auto pipeline = stage == ShaderStage::Compute ? vk.compute.pipelineResourceId : vk.graphics.pipelineResourceId;
                std::ofstream(out / ("shader-" + key + ".txt")) << c->DisassembleShader(pipeline, reflection, "").c_str();
                result["shaders"][key] = {{"stage", int(stage)}, {"encoding", int(reflection->encoding)}};
            }
            slot = 0;
            for(const auto& access : c->GetDescriptorAccess()) {
                if(access.stage != stage) continue;
                DescriptorRange range; range.offset = access.byteOffset; range.descriptorSize = access.byteSize; range.count = 1;
                const auto descriptors = c->GetDescriptors(access.descriptorStore, {range});
                if(descriptors.empty()) continue;
                const auto& d = descriptors[0];
                auto name = prefix + "-stage-" + std::to_string(int(stage)) + "-read-" + std::to_string(slot++);
                j["resources"].push_back(texture(d, name, false));
                if(!textures.count(d.resource)) buffer(d.resource, d.byteOffset, d.byteSize, name);
            }
        }
        if((a.flags & ActionFlags::Drawcall) != ActionFlags::NoFlags) {
            const auto m = c->GetPostVSData(0, 0, MeshDataStage::VSOut);
            j["post_vs"] = {{"buffer", id(m.vertexResourceId)}, {"offset", m.vertexByteOffset},
                             {"stride", m.vertexByteStride}, {"count", m.numIndices}};
            buffer(m.vertexResourceId, m.vertexByteOffset, uint64_t(m.vertexByteStride) * std::min(m.numIndices, 256u), prefix + "-post-vs");
            unsigned n = 0;
            for(const auto& b : vk.vertexInput.vertexBuffers) buffer(b.resourceId, b.byteOffset, b.byteSize, prefix + "-vertex-" + std::to_string(n++));
        }
        result["draws"].push_back(j);
        save(out / "frame.json", result);
    }
    if(actions.empty()) throw std::runtime_error("Captured frame contained no draw or dispatch");
    result["complete"] = true;
    save(out / "frame.json", result);
    std::cout << "PES_GPU_REPLAY=PASS\n";
}

static void fixture(const fs::path& prefix, const fs::path& tools) {
    auto get = reinterpret_cast<pRENDERDOC_GetAPI>(dlsym(RTLD_DEFAULT, "RENDERDOC_GetAPI"));
    RENDERDOC_API_1_6_0* api = nullptr;
    if(!get || !get(eRENDERDOC_API_Version_1_6_0, reinterpret_cast<void**>(&api))) throw std::runtime_error("RenderDoc API missing");
    api->SetCaptureFilePathTemplate(prefix.c_str());
    api->MaskOverlayBits(0, 0);
    auto ok = [](VkResult r) { if(r != VK_SUCCESS) throw std::runtime_error("Vulkan fixture failed: " + std::to_string(r)); };
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ci.pApplicationInfo = &app;
    VkInstance instance; ok(vkCreateInstance(&ci, nullptr, &instance));
    uint32_t count = 0; ok(vkEnumeratePhysicalDevices(instance, &count, nullptr));
    if(!count) throw std::runtime_error("No Vulkan fixture device");
    std::vector<VkPhysicalDevice> devices(count); ok(vkEnumeratePhysicalDevices(instance, &count, devices.data()));
    VkPhysicalDevice physical = devices.front();
    for(auto candidate : devices) {
        VkPhysicalDeviceProperties properties; vkGetPhysicalDeviceProperties(candidate, &properties);
        if(properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) { physical = candidate; break; }
    }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, nullptr);
    std::vector<VkQueueFamilyProperties> queues(count); vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, queues.data());
    uint32_t family = 0; while(family < count && !(queues[family].queueFlags & VK_QUEUE_GRAPHICS_BIT)) ++family;
    if(family == count) throw std::runtime_error("No graphics queue for fixture");
    float priority = 1;
    VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; qi.queueFamilyIndex = family; qi.queueCount = 1; qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; di.queueCreateInfoCount = 1; di.pQueueCreateInfos = &qi;
    VkDevice device; ok(vkCreateDevice(physical, &di, nullptr, &device)); VkQueue queue; vkGetDeviceQueue(device, family, 0, &queue);
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO}; bi.size = 4096; bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    VkBuffer buffer; ok(vkCreateBuffer(device, &bi, nullptr, &buffer)); VkMemoryRequirements req; vkGetBufferMemoryRequirements(device, buffer, &req);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO}; ai.allocationSize = req.size; ai.memoryTypeIndex = __builtin_ctz(req.memoryTypeBits);
    VkDeviceMemory memory; ok(vkAllocateMemory(device, &ai, nullptr, &memory)); ok(vkBindBufferMemory(device, buffer, memory, 0));
    VkCommandPoolCreateInfo pi{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO}; pi.queueFamilyIndex = family;
    VkCommandPool pool; ok(vkCreateCommandPool(device, &pi, nullptr, &pool));
    VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO}; cai.commandPool = pool; cai.commandBufferCount = 1;
    VkCommandBuffer cmd; ok(vkAllocateCommandBuffers(device, &cai, &cmd));
    VkImageCreateInfo image_info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    image_info.imageType = VK_IMAGE_TYPE_2D; image_info.format = VK_FORMAT_R8G8B8A8_UNORM;
    image_info.extent = {64, 64, 1}; image_info.mipLevels = 1; image_info.arrayLayers = 1;
    image_info.samples = VK_SAMPLE_COUNT_1_BIT;
    image_info.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    VkImage image; ok(vkCreateImage(device, &image_info, nullptr, &image));
    vkGetImageMemoryRequirements(device, image, &req);
    ai.allocationSize = req.size; ai.memoryTypeIndex = __builtin_ctz(req.memoryTypeBits);
    VkDeviceMemory image_memory; ok(vkAllocateMemory(device, &ai, nullptr, &image_memory));
    ok(vkBindImageMemory(device, image, image_memory, 0));
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = image_info.format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkImageView view; ok(vkCreateImageView(device, &vi, nullptr, &view));
    VkAttachmentDescription attachment{}; attachment.format = image_info.format;
    attachment.samples = VK_SAMPLE_COUNT_1_BIT; attachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
    attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE; attachment.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    VkAttachmentReference ref{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription sub{}; sub.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    sub.colorAttachmentCount = 1; sub.pColorAttachments = &ref;
    VkRenderPassCreateInfo ri{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
    ri.attachmentCount = 1; ri.pAttachments = &attachment; ri.subpassCount = 1; ri.pSubpasses = &sub;
    VkRenderPass renderpass; ok(vkCreateRenderPass(device, &ri, nullptr, &renderpass));
    VkFramebufferCreateInfo fi{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};
    fi.renderPass = renderpass; fi.attachmentCount = 1; fi.pAttachments = &view;
    fi.width = 64; fi.height = 64; fi.layers = 1;
    VkFramebuffer framebuffer; ok(vkCreateFramebuffer(device, &fi, nullptr, &framebuffer));
    auto shader = [&](const char* name) {
        std::ifstream stream(tools / name, std::ios::binary | std::ios::ate);
        const auto size = stream.tellg();
        if(size <= 0 || size % 4) throw std::runtime_error("Fixture shader missing");
        std::vector<uint32_t> data(size_t(size) / 4); stream.seekg(0);
        stream.read(reinterpret_cast<char*>(data.data()), size);
        VkShaderModuleCreateInfo info{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        info.codeSize = size_t(size); info.pCode = data.data();
        VkShaderModule module; ok(vkCreateShaderModule(device, &info, nullptr, &module)); return module;
    };
    VkShaderModule vs = shader("fixture.vert.spv"), ps = shader("fixture.frag.spv");
    VkPipelineShaderStageCreateInfo stages[2]{};
    stages[0].sType = stages[1].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    stages[0].module = vs; stages[1].module = ps; stages[0].pName = stages[1].pName = "main";
    stages[0].stage = VK_SHADER_STAGE_VERTEX_BIT; stages[1].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
    VkPipelineLayoutCreateInfo li{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    VkPipelineLayout layout; ok(vkCreatePipelineLayout(device, &li, nullptr, &layout));
    VkPipelineVertexInputStateCreateInfo vertex{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    VkPipelineInputAssemblyStateCreateInfo assembly{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
    assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    VkViewport viewport{0, 0, 64, 64, 0, 1}; VkRect2D scissor{{0, 0}, {64, 64}};
    VkPipelineViewportStateCreateInfo vp{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
    vp.viewportCount = 1; vp.pViewports = &viewport; vp.scissorCount = 1; vp.pScissors = &scissor;
    VkPipelineRasterizationStateCreateInfo raster{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
    raster.polygonMode = VK_POLYGON_MODE_FILL; raster.lineWidth = 1;
    VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
    ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    VkPipelineColorBlendAttachmentState blend{}; blend.colorWriteMask = 15;
    VkPipelineColorBlendStateCreateInfo bs{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
    bs.attachmentCount = 1; bs.pAttachments = &blend;
    VkGraphicsPipelineCreateInfo gi{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
    gi.stageCount = 2; gi.pStages = stages; gi.pVertexInputState = &vertex;
    gi.pInputAssemblyState = &assembly; gi.pViewportState = &vp; gi.pRasterizationState = &raster;
    gi.pMultisampleState = &ms; gi.pColorBlendState = &bs; gi.layout = layout; gi.renderPass = renderpass;
    VkPipeline pipeline; ok(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &gi, nullptr, &pipeline));
    if(std::getenv("PES_GPU_FIXTURE_WAIT")) std::this_thread::sleep_for(std::chrono::seconds(5));
    api->StartFrameCapture(nullptr, nullptr);
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; ok(vkBeginCommandBuffer(cmd, &begin));
    vkCmdFillBuffer(cmd, buffer, 0, 4096, 0x12345678);
    VkClearValue clear{}; VkRenderPassBeginInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
    rp.renderPass = renderpass; rp.framebuffer = framebuffer; rp.renderArea = scissor;
    rp.clearValueCount = 1; rp.pClearValues = &clear;
    vkCmdBeginRenderPass(cmd, &rp, VK_SUBPASS_CONTENTS_INLINE);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline); vkCmdDraw(cmd, 3, 1, 0, 0);
    vkCmdEndRenderPass(cmd); ok(vkEndCommandBuffer(cmd));
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO}; si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
    ok(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE)); ok(vkQueueWaitIdle(queue));
    if(!api->EndFrameCapture(nullptr, nullptr) || api->GetNumCaptures() != 1) throw std::runtime_error("Fixture capture missing");
    if(std::getenv("PES_GPU_FIXTURE_WAIT")) std::this_thread::sleep_for(std::chrono::seconds(1));
    vkDestroyPipeline(device, pipeline, nullptr); vkDestroyPipelineLayout(device, layout, nullptr);
    vkDestroyShaderModule(device, vs, nullptr); vkDestroyShaderModule(device, ps, nullptr);
    vkDestroyFramebuffer(device, framebuffer, nullptr); vkDestroyRenderPass(device, renderpass, nullptr);
    vkDestroyImageView(device, view, nullptr); vkDestroyImage(device, image, nullptr); vkFreeMemory(device, image_memory, nullptr);
    vkDestroyCommandPool(device, pool, nullptr); vkDestroyBuffer(device, buffer, nullptr); vkFreeMemory(device, memory, nullptr);
    vkDestroyDevice(device, nullptr); vkDestroyInstance(instance, nullptr);
}

int main(int argc, char** argv) {
    try {
        if(argc >= 3) RENDERDOC_SetDebugLogFile((std::string(argv[argc-1]) + ".renderdoc.log").c_str());
        if(argc == 3 && std::string(argv[1]) == "fixture") { fixture(fs::absolute(argv[2]), fs::absolute(argv[0]).parent_path()); return 0; }
        RENDERDOC_InitialiseReplay(GlobalEnvironment(), {});
        if(argc == 3 && std::string(argv[1]) == "options") {
            CaptureOptions options;
            RENDERDOC_GetDefaultCaptureOptions(&options);
            options.softMemoryLimit = 256;
            save(argv[2], {{"encoded", text(options.EncodeAsString())}, {"soft_memory_limit_mib", 256}});
        }
        else if(argc == 4 && std::string(argv[1]) == "capture") capture(std::stoul(argv[2]), argv[3]);
        else if(argc == 4 && (std::string(argv[1]) == "replay" || std::string(argv[1]) == "verify")) replay(argv[2], argv[3], std::string(argv[1]) == "verify");
        else throw std::runtime_error("Expected fixture PREFIX, capture PID JSON, or replay/verify RDC DIRECTORY");
        RENDERDOC_ShutdownReplay();
        return 0;
    } catch(const std::exception& e) { std::cerr << "PES_GPU_ERROR=" << e.what() << '\n'; return 1; }
}
