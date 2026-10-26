#!/usr/bin/env bash
# Mandatory source gate for GoT/game builds: loading image, gamepad quit and timeout.
set -Eeuo pipefail
SOURCE="$HOME/projects/shadps4/GhostOfTsushima/worktrees/got-proven-20261026"
if (( $# > 0 )); then SOURCE="$1"; fi
fail() { echo "FRONTEND_CONTRACT_FAIL: $*" >&2; exit 2; }
[[ -d "$SOURCE/src" ]] || fail "source tree absent: $SOURCE"
test_source() {
  local relative="$1" needle="$2"
  [[ -f "$SOURCE/$relative" ]] || fail "missing $relative"
  grep -Fq -- "$needle" "$SOURCE/$relative" ||
    fail "$relative missing required code: $needle"
}
test_source src/emulator.cpp '/app0/sce_sys/pic1.png'
test_source src/video_core/renderer_vulkan/vk_presenter.cpp 'IsSplashVisible()'
test_source src/video_core/renderer_vulkan/vk_presenter.cpp 'GetSplashData()'
test_source src/core/devtools/quit_dialog.h 'SDL_GAMEPAD_BUTTON_GUIDE'
test_source src/core/devtools/quit_dialog.h 'SDL_GAMEPAD_BUTTON_SOUTH'
test_source src/core/devtools/quit_dialog.h 'SDL_GAMEPAD_BUTTON_EAST'
test_source src/core/devtools/layer.cpp 'HOST_QUIT action=confirmed'
test_source src/core/devtools/layer.cpp 'SDL_EVENT_QUIT'
test_source src/sdl_window.cpp 'Overlay::ProcessQuitEvent(event)'
test_source src/sdl_window.cpp 'SDL_EVENT_QUIT'
command -v timeout >/dev/null || fail "GNU timeout missing"
echo "FRONTEND_LOADING_SPLASH_SOURCE=PASS"
echo "FRONTEND_GAMEPAD_EXIT_SOURCE=PASS"
echo "GAME_RUNTIME_LIMIT_REQUIRED=118s_SIGTERM_2s_SIGKILL"
echo "NOTE=manual_real_gamepad_acceptance_still_required"
