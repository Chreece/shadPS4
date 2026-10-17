#!/usr/bin/env python3
"""Fail-closed checks for general audio-output init and PCM route integration."""
from pathlib import Path
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("AUDIO_OUTPUT_CONTRACT_FAIL: " + message)


def validate(root: Path) -> None:
    def read(relative: str) -> str:
        path = root / relative
        require(path.is_file(), "missing source " + relative)
        return path.read_text(encoding="utf-8")

    health = read("src/core/libraries/audio/audioout_health.h")
    require("IsDisabledAudioDevice" in health and "AudioPeak(" in health and
            "AudioPeakS16(" in health,
            "common PCM and intentional-mute functions must be present")

    for backend, classname, open_function, pcm in (
            ("sdl_audio_out.cpp", "SDLPortBackend", "SDLAudioOut::Open",
             "SDL audio route:"),
            ("openal_audio_out.cpp", "OpenALPortBackend", "OpenALAudioOut::Open",
             "OpenAL audio route:")):
        source = read("src/core/libraries/audio/" + backend)
        require('#include "core/libraries/audio/audioout_health.h"' in source,
                backend + " missing common audio health checks")
        require("intentionally_disabled = IsDisabledAudioDevice(" in source and
                "return intentionally_disabled ||" in source,
                backend + " must preserve explicit None/disabled ports")
        require("bool IsUsable() const noexcept" in source,
                backend + " missing real host stream/source readiness contract")
        factory = source.partition("std::unique_ptr<PortBackend> " + open_function)[2]
        require("if (!backend->IsUsable())" in factory and
                "return {};" in factory,
                backend + " must reject an unusable backend instead of silent success")
        require(pcm in source and "first non-silent PCM" in source,
                backend + " missing sparse non-silent PCM progress diagnostics")
        require("IsUsable()" in factory and ("make_unique<" + classname) in factory,
                backend + " factory must validate newly created port instance")

    audio3d = read("src/core/libraries/audio3d/audio3d.cpp")
    init = audio3d.partition("sceAudio3dInitialize(")[2].partition(
        "sceAudio3dObjectReserve(")[0]
    require(bool(init) and
            "state.reset();" in init.partition("init_ret < 0")[2].partition("return init_ret;")[0],
            "Audio3D must roll back partial state if AudioOut initialization fails")


def main() -> int:
    require(len(sys.argv) == 2, "usage: audio_output_contract.py <source-tree>")
    validate(Path(sys.argv[1]))
    print("AUDIO_OUTPUT_BACKEND_CONTRACT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
