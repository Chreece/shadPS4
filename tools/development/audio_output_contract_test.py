#!/usr/bin/env python3
"""Positive and negative tests of the shared audio-output source contract."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

CHECKER = Path(__file__).with_name("audio_output_contract.py")
SPEC = importlib.util.spec_from_file_location("audio_output_contract", CHECKER)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

SDL = """#include "core/libraries/audio/audioout_health.h"
bool IsUsable() const noexcept { return intentionally_disabled || stream; }
intentionally_disabled = IsDisabledAudioDevice(device_name);
void Output() { log("SDL audio route:"); log("first non-silent PCM"); }
std::unique_ptr<PortBackend> SDLAudioOut::Open(PortOut& port) {
    auto backend = std::make_unique<SDLPortBackend>(port);
    if (!backend->IsUsable()) { return {}; }
    return backend;
}
"""
OPENAL = """#include "core/libraries/audio/audioout_health.h"
bool IsUsable() const noexcept { return intentionally_disabled || source; }
intentionally_disabled = IsDisabledAudioDevice(device_name);
void Output() { log("OpenAL audio route:"); log("first non-silent PCM"); }
void SetVolume() {
    if (intentionally_disabled || !device_context || !source) return;
    device_context->MakeCurrent(device_name);
}
u64 GetLastOutputTime() const {}
std::unique_ptr<PortBackend> OpenALAudioOut::Open(PortOut& port) {
    auto backend = std::make_unique<OpenALPortBackend>(port);
    if (!backend->IsUsable()) { return {}; }
    return backend;
}
"""
FIXTURE = {
    "src/core/libraries/audio/audioout_health.h":
        "IsDisabledAudioDevice AudioPeak( AudioPeakS16(",
    "src/core/libraries/audio/sdl_audio_out.cpp": SDL,
    "src/core/libraries/audio/openal_audio_out.cpp": OPENAL,
    "src/core/libraries/audio3d/audio3d.cpp": """
sceAudio3dInitialize(
if (const auto init_ret = sceAudioOutInit(); init_ret < 0) {
    state.reset();
    return init_ret;
}
sceAudio3dObjectReserve(
""",
}


def populate(root: Path) -> None:
    for name, contents in FIXTURE.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


class AudioOutputContractTest(unittest.TestCase):
    def check_mutation(self, name, old, new, fragment):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / name
            assert old in file.read_text()
            file.write_text(file.read_text().replace(old, new), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, fragment):
                checker.validate(root)

    def test_success_and_command_line(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            checker.validate(root)
            result = subprocess.run([sys.executable, str(CHECKER), str(root)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AUDIO_OUTPUT_BACKEND_CONTRACT=PASS", result.stdout)

    def test_broken_sdl_factory_fails(self):
        self.check_mutation("src/core/libraries/audio/sdl_audio_out.cpp",
                            "if (!backend->IsUsable())", "if (false)",
                            "must reject an unusable backend")

    def test_broken_openal_factory_fails(self):
        self.check_mutation("src/core/libraries/audio/openal_audio_out.cpp",
                            "if (!backend->IsUsable())", "if (false)",
                            "must reject an unusable backend")

    def test_muted_openal_gain_null_guard_fails(self):
        self.check_mutation("src/core/libraries/audio/openal_audio_out.cpp",
                            "if (intentionally_disabled || !device_context || !source)",
                            "if (source)",
                            "OpenAL muted ports must guard null device_context")

    def test_missing_disabled_output_setting_fails(self):
        self.check_mutation("src/core/libraries/audio/sdl_audio_out.cpp",
                            "intentionally_disabled = IsDisabledAudioDevice(",
                            "intentionally_disabled = get_device(",
                            "preserve explicit None")

    def test_missing_pcm_progress_fails(self):
        self.check_mutation("src/core/libraries/audio/openal_audio_out.cpp",
                            "first non-silent PCM", "some random diagnostic",
                            "sparse non-silent PCM")

    def test_partial_audio3d_init_rolls_back(self):
        self.check_mutation("src/core/libraries/audio3d/audio3d.cpp",
                            "state.reset();", "/* no rollback */",
                            "must roll back partial state")

    def test_missing_health_header_is_detected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            (root / "src/core/libraries/audio/audioout_health.h").unlink()
            with self.assertRaisesRegex(RuntimeError, "missing source"):
                checker.validate(root)

    def test_missing_signed_pcm_support_is_detected(self):
        self.check_mutation("src/core/libraries/audio/audioout_health.h",
                            "AudioPeakS16(", "NoS16Peak(",
                            "common PCM and intentional-mute")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AudioOutputContractTest))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"AUDIO_OUTPUT_CONTRACT_TESTS=PASS cases={result.testsRun}")
