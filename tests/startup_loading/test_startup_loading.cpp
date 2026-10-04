// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string_view>
#include <thread>
#include <vector>

#include "imgui/startup_loading.h"

void assert_fail_debug_msg(const char* message) {
    std::fprintf(stderr, "%s\n", message);
    std::abort();
}

static void Check(bool condition, const char* message) {
    if (!condition) {
        assert_fail_debug_msg(message);
    }
}

using Core::Startup::Progress;
using Core::Startup::Stage;

static void Lifecycle() {
    Progress progress;
    const auto start = Progress::Clock::time_point{};
    Check(!progress.IsActive(), "startup must be opt-in for game launch");
    Check(!progress.Presented(true, true), "an unrelated presenter must not complete startup");
    progress.Begin(start);
    Check(progress.IsActive(), "startup must remain visible before video-out opens");
    progress.SetStage(Stage::FirstFrame);
    Check(!progress.Presented(false, true), "host blank frames must not finish startup");
    Check(!progress.Presented(true, false), "failed presentation must not finish startup");
    Check(progress.IsActive(), "keep drawing while the game has no displayed frame");
    Check(progress.ElapsedMs(start + std::chrono::seconds{20}) == 20000,
          "elapsed time must not depend on a game's frame rate");
    Check(progress.Presented(true, true), "a displayed game buffer completes startup");
    Check(!progress.Presented(true, true), "completion must be reported only once");
    progress.SetStage(Stage::Modules);
    Check(!progress.IsActive(), "late loader updates must not cover gameplay");
    Check(!progress.Presented(false, true), "later black transitions must not rearm startup");

    for (int attempt = 0; attempt < 100; ++attempt) {
        progress.Begin(start);
        std::jthread loader([&] {
            for (int i = 0; i < 100; ++i) {
                progress.SetStage(Stage::Modules);
            }
        });
        progress.Presented(true, true);
        loader.join();
        Check(!progress.IsActive(), "completion must win over concurrent loader updates");
    }
}

static std::vector<ImVec2> Draw(Stage stage, ImVec2 size, const char* title, int elapsed_ms) {
    auto& io = ImGui::GetIO();
    io.DisplaySize = size;
    ImGui::NewFrame();
    ImGui::SetNextWindowPos({0, 0});
    ImGui::SetNextWindowSize(size);
    ImGui::Begin("Display", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
                     ImGuiWindowFlags_NoBackground | ImGuiWindowFlags_NoSavedSettings);
    auto* draw = ImGui::GetWindowDrawList();
    const int before = draw->VtxBuffer.Size;
    ImGui::DrawStartupLoading({0, 0}, size, title, stage, elapsed_ms);
    std::vector<ImVec2> result;
    for (int i = before; i < draw->VtxBuffer.Size; ++i) {
        const auto pos = draw->VtxBuffer[i].pos;
        Check(std::isfinite(pos.x) && std::isfinite(pos.y), "UI geometry must be finite");
        Check(pos.x >= 0 && pos.y >= 0 && pos.x <= size.x && pos.y <= size.y,
              "loading panel must fit the game display");
        result.push_back(pos);
    }
    ImGui::End();
    ImGui::Render();
    Check(!io.WantCaptureKeyboard && !io.WantCaptureMouse,
          "loading indicator must not capture game or quit-dialog input");
    return result;
}

static void Ui() {
    ImGui::CreateContext();
    auto& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.DeltaTime = 1.0f / 60.0f;
    io.Fonts->AddFontDefault();
    unsigned char* pixels;
    int width, height;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &width, &height);
    Check(Draw(Stage::Inactive, {1280, 720}, "Game", 0).empty(), "inactive UI must be absent");
    const auto first = Draw(Stage::FirstFrame, {1280, 720}, "The Last Guardian", 10000);
    const auto second = Draw(Stage::FirstFrame, {1280, 720}, "The Last Guardian", 10000);
    Check(!first.empty() && first.size() == second.size(), "indicator must draw while waiting");
    bool moved = false;
    for (size_t i = 0; i < first.size(); ++i) {
        moved |= first[i].x != second[i].x || first[i].y != second[i].y;
    }
    Check(moved, "indicator must animate without a new game frame");
    Draw(Stage::Executable, {640, 360}, "", 2000);
    Draw(Stage::Modules, {3840, 2160}, "A very long game title with multiple words and subtitles",
         65000);
    Check(Draw(Stage::Complete, {1280, 720}, "Game", 20000).empty(),
          "completed startup must leave no overlay");
    ImGui::DestroyContext();
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view{argv[1]} == "lifecycle") {
        Lifecycle();
    } else {
        Ui();
    }
}
