#!/usr/bin/env python3
"""Compiler reproducer and packaging regressions; not a complete emulator build.

The C++ fixture models IR::TypedValue's overlapping-type constructor and the
emitter's U32U64 returns. It compiles the EXACT replacement body emitted by the
patcher, rather than testing a second copy of the corrected expression. The
real production translation units are built first by the prepared host script.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
# tests/shader_recompiler -> tests -> repository

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

apply = load("apply_resource_build_test", "scripts/apply-resource-access-ranges-20261021.py")
prepare = load("prepare_resource_build_test", "scripts/prepare-resource-host-20261021.py")
OLD_ANCHOR = ('            return ir.IAdd(ir.IMul(index, ir.Imm32(stride >> shift)),\n'
              '                           ir.Imm32(total_offset >> shift));')

CPP_PREFIX = r'''#include <cstdint>
#include <cstdio>
#include <random>
#include <type_traits>
using u32 = std::uint32_t;
namespace IR {
enum class Type { Void = 0, U32 = 1 << 9, U64 = 1 << 10 };
constexpr Type operator&(Type a, Type b) { return Type(int(a) & int(b)); }
constexpr Type operator|(Type a, Type b) { return Type(int(a) | int(b)); }
struct Value { u32 bits{}; };
// Same overlapping-type conversion constraint as ir/value.h. The payload
// and emitter are numeric fixtures, not the emulator's instruction graph.
template <Type type_>
class TypedValue : public Value {
public:
    TypedValue() = default;
    template <Type other_type>
        requires((other_type & type_) != Type::Void)
    TypedValue(const TypedValue<other_type>& value) : Value(value) {}
    explicit TypedValue(const Value& value) : Value(value) {}
};
using U32 = TypedValue<Type::U32>;
using U32U64 = TypedValue<Type::U32 | Type::U64>;
struct Emitter {
    U32 Imm32(u32 x) { return U32{Value{x}}; }
    U32U64 IAdd(const U32U64& a, const U32U64& b) {
        return U32U64{Value{u32(a.bits + b.bits)}};
    }
    U32U64 IMul(const U32U64& a, const U32U64& b) {
        return U32U64{Value{u32(a.bits * b.bits)}};
    }
    U32U64 BitwiseAnd(const U32U64& a, const U32U64& b) {
        return U32U64{Value{a.bits & b.bits}};
    }
};
static_assert(std::is_convertible_v<U32, U32U64>);
static_assert(std::is_convertible_v<U32U64, U32>);
}
namespace BufferAccess {
constexpr u32 ScaledAddressMask(u32 shift) { return shift < 32 ? UINT32_MAX >> shift : 0; }
}
IR::U32 Address(IR::Emitter& ir, IR::U32 index, u32 stride, u32 total_offset, u32 shift) {
'''
CPP_SUFFIX = r'''
}
int main() {
    IR::Emitter ir;
    std::mt19937 rng(0x42554646u);
    for (unsigned i = 0; i < 200000; ++i) {
        const u32 index = rng();
        const u32 shift = rng() % 4;
        const u32 mask = (1u << shift) - 1;
        const u32 stride = (rng() & 0x3fffu) & ~mask;
        const u32 offset = rng() & ~mask;
        const u32 expected = u32(index * stride + offset) >> shift;
        const auto actual = Address(ir, ir.Imm32(index), stride, offset, shift);
        if (actual.bits != expected) return 2;
    }
    std::puts("GENERATED_TYPED_ADDRESS=PASS randomized_cases=200000");
}
'''

HOST_FIXTURE = '''#!/usr/bin/env bash
set -Eeuo pipefail
cat > "$RUN/info/overlay-files.txt" <<'GOT_PINNED_OVERLAY_FILES'
src/shader_recompiler/resource.h
GOT_PINNED_OVERLAY_FILES
test "$(wc -l < "$RUN/info/overlay-files.txt")" -eq 110 || {
    exit 2;
}
STATUS=HOST_TESTS
git -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_bounds.cpp" > "$RUN/tmp/test.cpp"
echo "HOST_ONLY_TESTS=PASS" | tee -a "$RUN/host-tests.log"
STATUS=STAGING_CANDIDATE
# Source staging is represented by this fixture marker.
STATUS=CONFIGURING_OFFLINE
# CMake configuration is represented by this fixture marker.
STATUS=BUILDING_VK_RASTERIZER_OBJECT_FIRST
STATUS=BUILDING_JOBS_2
'''

def generated_host():
    data = HOST_FIXTURE.encode()
    with patch.object(prepare, "HOST_BLOB", prepare.blob(data)):
        return prepare.prepare(data)

class PackageTests(unittest.TestCase):
    def test_generated_body_has_explicit_u32_return(self):
        body = apply.transform("src/shader_recompiler/ir/passes/resource_patching_pass.cpp", OLD_ANCHOR)
        self.assertIn("if (shift == 0)", body)
        self.assertIn("return IR::U32{ir.BitwiseAnd(", body)
        self.assertNotIn("? shifted", body)

    def test_missing_or_duplicate_anchor_rejected(self):
        for source in ("", OLD_ANCHOR + OLD_ANCHOR):
            with self.assertRaises(ValueError):
                apply.transform("src/shader_recompiler/ir/passes/resource_patching_pass.cpp", source)

    def test_unknown_host_bytes_rejected(self):
        with self.assertRaises(ValueError):
            prepare.prepare(HOST_FIXTURE.encode())

    def test_bash_syntax_and_production_object_order(self):
        host = generated_host()
        subprocess.run(["bash", "-n"], input=host, text=True, check=True)
        self.assertLess(host.index("STATUS=CONFIGURING_OFFLINE"), host.index("STATUS=COMPILE_RESOURCE_CORRECTION_OBJECTS"))
        self.assertLess(host.index("STATUS=COMPILE_RESOURCE_CORRECTION_OBJECTS"), host.index("STATUS=BUILDING_VK_RASTERIZER_OBJECT_FIRST"))
        self.assertIn("src/shader_recompiler/ir/passes/resource_patching_pass.cpp.o", host)
        self.assertIn("src/shader_recompiler/recompiler.cpp.o", host)

    def manifest_check(self, correct):
        host = generated_host()
        begin = host.index('python3 - "$RUN/tmp/staged"')
        end = host.index("\nPYRESOURCECHECK\n", begin) + len("\nPYRESOURCECHECK\n")
        block = host[begin:end]
        with tempfile.TemporaryDirectory(prefix="resource-manifest-test-") as directory:
            run = Path(directory)
            (run / "tmp/staged").mkdir(parents=True)
            (run / "info").mkdir()
            data = b"test source\n"
            (run / "tmp/staged/a.cpp").write_bytes(data)
            expected = hashlib.sha256(data if correct else b"wrong").hexdigest()
            (run / "info/resource-correction.json").write_text(json.dumps({"files": {"a.cpp": {"sha256": expected}}}))
            env = dict(os.environ, RUN=directory)
            env.pop("PYTHONOPTIMIZE", None)
            result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + block], env=env, text=True, capture_output=True)
            log = (run / "host-tests.log").read_text()
            self.assertEqual(result.returncode == 0, correct)
            self.assertEqual("CORRECTED_SOURCE_MANIFEST=PASS" in log, correct)
            return result

    def test_manifest_success_recorded_in_verified_log(self):
        self.manifest_check(True)

    def test_manifest_failure_survives_tee(self):
        self.manifest_check(False)

    def object_check(self, success):
        host = generated_host()
        begin = host.index("STATUS=COMPILE_RESOURCE_CORRECTION_OBJECTS")
        end = host.index("STATUS=BUILDING_VK_RASTERIZER_OBJECT_FIRST", begin)
        with tempfile.TemporaryDirectory(prefix="resource-object-test-") as directory:
            run = Path(directory)
            (run / "info").mkdir()
            (run / "bin").mkdir()
            ninja = run / "bin/ninja"
            ninja.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\nexit ' + ('0' if success else '17') + '\n')
            ninja.chmod(0o755)
            env = dict(os.environ, RUN=directory, BUILD=str(run / "build"))
            env["PATH"] = str(run / "bin") + os.pathsep + env["PATH"]
            result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + host[begin:end] + '\necho NEXT_BUILD_STAGE_REACHED\n'], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0 if success else 2)
            self.assertEqual("NEXT_BUILD_STAGE_REACHED" in result.stdout, success)
            log = run / "host-tests.log"
            self.assertEqual(log.is_file() and "RESOURCE_CORRECTION_OBJECTS=PASS" in log.read_text(), success)
            output = (run / "info/resource-correction-objects.log").read_text()
            self.assertIn("resource_patching_pass.cpp.o", output)
            self.assertIn("recompiler.cpp.o", output)

    def test_production_compile_failure_stops_build(self):
        self.object_check(False)

    def test_production_compile_success_is_logged(self):
        self.object_check(True)


def compile_regression(compiler, output):
    body = apply.transform("src/shader_recompiler/ir/passes/resource_patching_pass.cpp", OLD_ANCHOR)
    output.mkdir(parents=True, exist_ok=True)
    old_body = body[:body.index('            // BitwiseAnd returns')] + '''            return shift == 0
                       ? shifted
                       : ir.BitwiseAnd(shifted, ir.Imm32(
                             BufferAccess::ScaledAddressMask(shift)));'''
    old = output / "before.cpp"
    new = output / "after.cpp"
    old.write_text(CPP_PREFIX + old_body + CPP_SUFFIX)
    new.write_text(CPP_PREFIX + body + CPP_SUFFIX)
    common = [compiler, "-std=c++23", "-Wall", "-Wextra", "-Werror", "-O2"]
    rejected = subprocess.run(common + ["-fsyntax-only", str(old)], text=True, capture_output=True)
    (output / "before.log").write_text(rejected.stdout + rejected.stderr)
    if rejected.returncode == 0 or "error:" not in rejected.stderr:
        raise RuntimeError("NEGATIVE_CONTROL_DID_NOT_REPRODUCE")
    executable = output / "after"
    compiled = subprocess.run(common + [str(new), "-o", str(executable)], text=True, capture_output=True)
    (output / "after-build.log").write_text(compiled.stdout + compiled.stderr)
    compiled.check_returncode()
    run = subprocess.run([str(executable)], text=True, capture_output=True, check=True)
    (output / "after-run.log").write_text(run.stdout + run.stderr)
    print(f"COMPILER_REGRESSION=PASS compiler={compiler} before=REJECTED after=COMPILED_AND_RAN")
    print(run.stdout, end="")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PackageTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    for compiler in options.compiler:
        path = shutil.which(compiler)
        if not path:
            raise SystemExit("COMPILER_NOT_FOUND=" + compiler)
        compile_regression(path, options.output / Path(compiler).name)
