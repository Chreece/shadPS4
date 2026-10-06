// SPDX-License-Identifier: GPL-2.0-or-later
// Diagnostic only: bounded draw submission/state trace. No rendering overrides.
#pragma once
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include "common/path_util.h"
#include "core/debug_state.h"
#include "video_core/renderdoc.h"
#include "video_core/amdgpu/regs.h"
#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"

namespace Vulkan::UiTrace {
struct State {
    std::filesystem::path root;
    std::FILE* output{};
    std::string token;
    std::chrono::steady_clock::time_point next_poll{}, deadline{};
    u32 first_frame{}, last_frame{}, frame_changes{}, draws{};
    ~State() { if (output) std::fclose(output); }
    void End(const char* reason) {
        if (!output) return;
        std::fprintf(output, "CAPTURE_END reason=%s draws=%u frame_changes=%u first=%u last=%u\n",
                     reason, draws, frame_changes, first_frame, last_frame);
        std::fclose(output);
        output = nullptr;
        VideoCore::RequestScreenshot(VideoCore::ScreenshotRequest::GameOnly);
        std::fprintf(stderr, "UI_DRAW_TRACE_COMPLETE token=%s draws=%u reason=%s\n",
                     token.c_str(), draws, reason);
        std::fflush(stderr);
    }
    bool Accept(u32 frame) {
        const auto now = std::chrono::steady_clock::now();
        if (output) {
            if (frame != last_frame) {
                last_frame = frame;
                ++frame_changes;
            }
            if (frame_changes >= 3 || draws >= 12000 || now >= deadline) {
                End(frame_changes >= 3 ? "three-frame-boundaries" :
                    draws >= 12000 ? "draw-limit" : "time-limit");
                return false;
            }
            return true;
        }
        if (now < next_poll) return false;
        next_poll = now + std::chrono::milliseconds(250);
        if (root.empty()) {
            root = Common::FS::GetUserPath(Common::FS::PathType::UserDir) / "ui-draw-trace";
        }
        std::ifstream request(root / "request");
        if (!request) return false;
        std::string value;
        // Bound the read even if somebody places an arbitrary file here.
        char buffer[81]{};
        request.getline(buffer, sizeof(buffer));
        if (request.fail()) return false;
        value = buffer;
        const auto safe = [](unsigned char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                   (c >= '0' && c <= '9') || c == '-' || c == '_';
        };
        if (value.empty() || !std::all_of(value.begin(), value.end(), safe)) return false;
        // Exclusive creation avoids overwriting an earlier capture.
        output = std::fopen((root / (value + ".trace")).string().c_str(), "wx");
        if (!output) return false;
        token = value;
        std::error_code ec;
        std::filesystem::remove(root / "request", ec);
        first_frame = last_frame = frame;
        draws = frame_changes = 0;
        deadline = now + std::chrono::seconds(5);
        std::fprintf(output, "CAPTURE_BEGIN token=%s first_frame=%u schema=1\n", token.c_str(), frame);
        std::fflush(output);
        VideoCore::RequestScreenshot(VideoCore::ScreenshotRequest::GameOnly);
        return true;
    }
};
inline State& Get() { static State state; return state; }

template<class T>
inline void Raw(std::FILE* f, const char* name, const T& value) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&value);
    std::fprintf(f, " %s_bytes=", name);
    for (size_t i = 0; i < sizeof(T); ++i) std::fprintf(f, "%02x", unsigned(bytes[i]));
}

class Draw {
    State* s{};
    const char* result = "incomplete";
public:
    Draw(const AmdGpu::Regs& regs, bool indirect, bool indexed) {
        auto& state = Get();
        const u32 frame = DebugState.GetFrameNum();
        if (!state.Accept(frame)) return;
        s = &state;
        ++s->draws;
        const auto& vp = regs.viewports[0];
        std::fprintf(s->output, "DRAW id=%u frame=%u indirect=%u indexed=%u prim=%u count=%u instances=%u "
                     "target=%08x shader_mask=%08x xy_transformed=%u clip_disabled=%u "
                     "screen=%d,%d,%d,%d viewport0=%g,%g,%g,%g\n",
                     s->draws, frame, unsigned(indirect), unsigned(indexed), unsigned(regs.primitive_type),
                     unsigned(regs.num_indices), unsigned(regs.num_instances.NumInstances()),
                     unsigned(regs.color_target_mask.raw), unsigned(regs.color_shader_mask.raw),
                     unsigned(regs.viewport_control.xy_transformed), unsigned(regs.IsClipDisabled()),
                     int(regs.screen_scissor.top_left_x), int(regs.screen_scissor.top_left_y),
                     int(regs.screen_scissor.bottom_right_x), int(regs.screen_scissor.bottom_right_y),
                     double(vp.xscale), double(vp.xoffset), double(vp.yscale), double(vp.yoffset));
        std::fprintf(s->output, "REGS");
        Raw(s->output, "color_control", regs.color_control);
        Raw(s->output, "depth", regs.depth_control);
        Raw(s->output, "stencil", regs.stencil_control);
        Raw(s->output, "stencil_front", regs.stencil_ref_front);
        Raw(s->output, "stencil_back", regs.stencil_ref_back);
        Raw(s->output, "polygon", regs.polygon_control);
        Raw(s->output, "clipper", regs.clipper_control);
        std::fprintf(s->output, "\n");
        for (u32 i = 0; i < 8; ++i) {
            const auto& cb = regs.color_buffers[i];
            if (cb.Address() == 0) continue;
            std::fprintf(s->output, "CB i=%u addr=%llx format=%u pitch=%u height=%u mask=%u",
                         i, static_cast<unsigned long long>(cb.Address()), unsigned(cb.info.format),
                         unsigned(cb.Pitch()), unsigned(cb.Height()), unsigned(regs.color_target_mask.GetMask(i)));
            Raw(s->output, "blend", regs.blend_control[i]);
            std::fprintf(s->output, "\n");
        }
        std::fprintf(s->output, "DEPTH addr=%llx stencil_addr=%llx valid=%u stencil_valid=%u\n",
                     static_cast<unsigned long long>(regs.depth_buffer.DepthAddress()),
                     static_cast<unsigned long long>(regs.depth_buffer.StencilAddress()),
                     unsigned(regs.depth_buffer.DepthValid()), unsigned(regs.depth_buffer.StencilValid()));
    }
    ~Draw() { if (s) std::fprintf(s->output, "DRAW_END result=%s\n", result); }
    void Result(const char* value) { result = value; }
    void Pipeline(const GraphicsPipeline& pipeline) {
        if (!s) return;
        const auto& key = pipeline.GetGraphicsKey();
        const auto& fetch = pipeline.GetFetchShader();
        std::fprintf(s->output, "PIPE_EXTRA samples=%u depth_clip=%u depth_clamp=%u fetch_empty=%u vertex_sgpr=%d instance_sgpr=%d\n",
                     unsigned(key.num_samples), unsigned(key.depth_clip_enable), unsigned(key.depth_clamp_enable),
                     unsigned(fetch.Empty()), int(fetch.vertex_offset_sgpr), int(fetch.instance_offset_sgpr));
        for (u32 i = 0; i < 8; ++i) {
            std::fprintf(s->output, "PIPE_CB i=%u", i);
            Raw(s->output, "write_mask", key.write_masks[i]);
            Raw(s->output, "blend", key.blend_controls[i]);
            std::fprintf(s->output, "\n");
        }
        std::fprintf(s->output, "PIPE mrt=%u attachments=%u stages=", unsigned(key.mrt_mask),
                     unsigned(key.num_color_attachments));
        for (auto hash : key.stage_hashes) std::fprintf(s->output, "%016llx,", static_cast<unsigned long long>(hash));
        std::fprintf(s->output, "\n");
    }
    void Offsets(u32 vertex, u32 instance) {
        if (s) std::fprintf(s->output, "DIRECT vertex_offset=%u instance_offset=%u\n", vertex, instance);
    }
    void Indirect(VAddr address, u32 offset, u32 stride, u32 count, VAddr count_address) {
        if (s) std::fprintf(s->output, "INDIRECT addr=%llx offset=%u stride=%u max_count=%u count_addr=%llx\n",
                            static_cast<unsigned long long>(address), offset, stride, count,
                            static_cast<unsigned long long>(count_address));
    }
};

template<class Viewports, class Scissors>
inline void Dynamic(const Viewports& viewports, const Scissors& scissors) {
    auto* f = Get().output;
    if (!f) return;
    for (const auto& v : viewports) {
        std::fprintf(f, "VK_VIEWPORT x=%g y=%g width=%g height=%g zmin=%g zmax=%g\n",
                     double(v.x), double(v.y), double(v.width), double(v.height), double(v.minDepth), double(v.maxDepth));
    }
    for (const auto& r : scissors) {
        std::fprintf(f, "VK_SCISSOR x=%d y=%d width=%u height=%u\n", int(r.offset.x), int(r.offset.y),
                     unsigned(r.extent.width), unsigned(r.extent.height));
    }
}
} // namespace Vulkan::UiTrace
