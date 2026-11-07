// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

namespace Shader {
struct Pools;
namespace IR {
struct Program;
}
namespace Gcn {
class CFG;
}
namespace Optimization {
void OrderedGdsContinuationPass(IR::Program& program, Gcn::CFG& cfg, Pools& pools);
}
} // namespace Shader
