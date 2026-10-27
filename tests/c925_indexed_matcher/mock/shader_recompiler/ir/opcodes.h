#pragma once
#include <cstdint>
namespace IR {
enum class Opcode { IAdd32, ISub32, ShiftRightLogical32, ShiftLeftLogical32, IMul32, GetAttribute, Phi };
enum class Type { U32 };
}
