// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Included from resource_discover_pass.cpp inside Shader::Optimization.

static bool IsImagePhiEntryValue(const IR::Value& value) {
    return value.IsImmediate() || value.Inst()->GetParent()->ImmPredecessors().empty();
}

static bool CanRebuildImageSharpValue(const IR::Value& value, u32 depth) {
    if (IsImagePhiEntryValue(value)) {
        return true;
    }
    const IR::Inst* const inst = value.Inst();
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer:
    case IR::Opcode::CompositeConstructU32x2:
        break;
    default:
        return false;
    }
    if (depth == 0) {
        return false;
    }
    for (size_t i = 0; i < inst->NumArgs(); ++i) {
        if (!CanRebuildImageSharpValue(inst->Arg(i), depth - 1)) {
            return false;
        }
    }
    return true;
}

static IR::Value RebuildImageSharpValue(
    IR::Block& block, IR::Block::iterator insert_point, const IR::Value& value,
    std::unordered_map<const IR::Inst*, IR::Value>& rebuilt) {
    if (IsImagePhiEntryValue(value)) {
        return value;
    }
    const IR::Inst* const inst = value.Inst();
    if (const auto it = rebuilt.find(inst); it != rebuilt.end()) {
        return it->second;
    }
    boost::container::small_vector<IR::Value, 4> args;
    for (size_t i = 0; i < inst->NumArgs(); ++i) {
        args.push_back(RebuildImageSharpValue(block, insert_point, inst->Arg(i), rebuilt));
    }
    IR::Inst* const copy = &*block.PrependNewInst(insert_point, *inst);
    for (size_t i = 0; i < args.size(); ++i) {
        copy->SetArg(i, args[i]);
    }
    return rebuilt.emplace(inst, IR::Value{copy}).first->second;
}

static IR::Value SelectImagePhiResult(IR::IREmitter& ir, const IR::U1& condition,
                                      const IR::Value& true_value,
                                      const IR::Value& false_value) {
    size_t num_components = 0;
    switch (true_value.Type()) {
    case IR::Type::U32x2:
    case IR::Type::F32x2:
        num_components = 2;
        break;
    case IR::Type::U32x3:
    case IR::Type::F32x3:
        num_components = 3;
        break;
    case IR::Type::U32x4:
    case IR::Type::F32x4:
        num_components = 4;
        break;
    default:
        return ir.Select(condition, true_value, false_value);
    }
    boost::container::small_vector<IR::Value, 4> components;
    for (size_t i = 0; i < num_components; ++i) {
        components.push_back(ir.Select(condition, ir.CompositeExtract(true_value, i),
                                       ir.CompositeExtract(false_value, i)));
    }
    return ir.CompositeConstruct(std::span<const IR::Value>{components.data(), components.size()});
}

static bool SplitImageSampleBySharpPhi(IR::Block& block, IR::Inst& inst,
                                       std::size_t& remaining_budget) {
    if (inst.GetOpcode() != IR::Opcode::ImageSampleRaw || remaining_budget == 0) {
        return false;
    }
    const IR::Inst* const handle = inst.Arg(0).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::ImageHandle) {
        return false;
    }
    const IR::Inst* const low = handle->Arg(0).TryInst();
    const IR::Inst* const high = handle->Arg(1).TryInst();
    const IR::Inst* const sampler = inst.Arg(1).TryInst();
    if (!low || !high || !sampler || low->NumArgs() != 4 || high->NumArgs() != 4 ||
        sampler->NumArgs() != 4) {
        return false;
    }

    const std::array<const IR::Inst*, 3> pieces{low, high, sampler};
    std::array<IR::Value, 12> words{};
    std::array<bool, 12> comes_from_phi{};
    // A material can select an eight-dword T# or four-dword S# through a
    // control-flow Phi in the *ReadConstBuffer dword offset*, rather than a
    // Phi directly in the descriptor word. Such Phis cannot be evaluated by
    // the host SRT walker. Finite constant-index alternatives can instead
    // be bound separately and selected on the GPU after each image sample.
    std::array<bool, 12> indexed_read_phi{};
    std::array<const IR::Inst*, 12> indexed_phi_producers{};
    std::array<u32, 12> indexed_read_addends{};
    const IR::Inst* first_phi{};
    for (std::size_t k = 0; k < words.size(); ++k) {
        const IR::Value value = pieces[k / 4]->Arg(k % 4);
        words[k] = value;
        if (value.IsImmediate()) {
            if (value.Type() != IR::Type::U32) return false;
            continue;
        }
        const IR::Inst* const producer = value.TryInst();
        if (!producer) return false;
        const IR::Inst* phi = producer;
        if (producer->GetOpcode() == IR::Opcode::ReadConstBuffer) {
            const auto index = AnalyzeIndexedSharpWord(producer->Arg(1));
            if (index.valid && index.selector) {
                const auto* candidate = static_cast<const IR::Inst*>(index.selector);
                if (candidate->GetOpcode() == IR::Opcode::Phi) {
                    phi = candidate;
                    indexed_read_phi[k] = true;
                    indexed_phi_producers[k] = candidate;
                    indexed_read_addends[k] = index.addend;
                }
            }
        }
        if (!indexed_read_phi[k]) {
            if (IsSharpSource(producer)) continue;
            if (producer->GetOpcode() != IR::Opcode::Phi) return false;
        }
        if (!first_phi) {
            first_phi = phi;
        } else if (phi->GetParent() != first_phi->GetParent() ||
                   phi->NumArgs() != first_phi->NumArgs()) {
            return false;
        }
        comes_from_phi[k] = true;
    }
    if (!first_phi) return false;
    const std::size_t branches = first_phi->NumArgs();
    if (!CanSplitSharpImagePhi(branches, true, true, remaining_budget)) return false;

    std::array<IR::Block*, 3> predecessors{};
    for (std::size_t side = 0; side < branches; ++side) {
        IR::Block* const predecessor = first_phi->PhiBlock(side);
        if (!predecessor) return false;
        for (std::size_t j = 0; j < side; ++j) {
            if (predecessors[j] == predecessor) return false;
        }
        predecessors[side] = predecessor;
    }

    std::array<std::array<IR::Value, 12>, 3> selected{};
    for (std::size_t side = 0; side < branches; ++side) {
        for (std::size_t word = 0; word < words.size(); ++word) {
            if (!comes_from_phi[word]) {
                selected[side][word] = words[word];
                continue;
            }
            const IR::Inst* const phi = indexed_read_phi[word]
                ? indexed_phi_producers[word] : words[word].TryInst();
            std::size_t arg = 0;
            while (arg < phi->NumArgs() && phi->PhiBlock(arg) != predecessors[side]) ++arg;
            if (arg == phi->NumArgs()) return false;
            const IR::Value value = phi->Arg(arg);
            if (indexed_read_phi[word]) {
                // Only genuine finite, immediate dword offsets are safely
                // representable by distinct static SRT sharp bindings.
                // Loops/attributes/indirect GPU selectors remain dynamic.
                if (!value.IsImmediate() || value.Type() != IR::Type::U32) return false;
                selected[side][word] = IR::Value{value.U32() + indexed_read_addends[word]};
            } else {
                if (value.IsImmediate()) {
                    if (value.Type() != IR::Type::U32) return false;
                } else {
                    const IR::Inst* const incoming = value.TryInst();
                    if (!incoming || !IsSharpSource(incoming) ||
                        !CanRebuildImageSharpValue(value, 4)) {
                        return false;
                    }
                }
                selected[side][word] = value;
            }
        }
    }

    IR::Block* const phi_block = first_phi->GetParent();
    IR::Inst* const selector =
        &*phi_block->PrependNewInst(phi_block->begin(), IR::Opcode::Phi);
    selector->SetFlags(IR::Type::U32);
    for (std::size_t side = 0; side < branches; ++side) {
        selector->AddPhiOperand(predecessors[side], IR::Value{static_cast<u32>(side)});
    }

    const auto insert_point = IR::Block::InstructionList::s_iterator_to(inst);
    std::unordered_map<const IR::Inst*, IR::Value> rebuilt;
    boost::container::small_vector<IR::Value, 3> sampled;
    for (std::size_t side = 0; side < branches; ++side) {
        std::array<IR::Inst*, 3> copies{};
        for (std::size_t group = 0; group < 3; ++group) {
            IR::Inst* const clone = &*block.PrependNewInst(insert_point, *pieces[group]);
            for (std::size_t word = 0; word < 4; ++word) {
                const std::size_t idx = group * 4 + word;
                IR::Value value = selected[side][idx];
                if (indexed_read_phi[idx]) {
                    // Rebuild only the constant-buffer load with its exact
                    // branch-selected offset. The GPU still controls which
                    // sampled texel contributes to the final result.
                    IR::Inst* const read = &*block.PrependNewInst(
                        insert_point, *words[idx].TryInst());
                    read->SetArg(1, value);
                    value = IR::Value{read};
                } else if (comes_from_phi[idx]) {
                    value = RebuildImageSharpValue(block, insert_point, value, rebuilt);
                }
                clone->SetArg(word, value);
            }
            copies[group] = clone;
        }
        IR::Inst* const image_handle = &*block.PrependNewInst(insert_point, *handle);
        image_handle->SetArg(0, IR::Value{copies[0]});
        image_handle->SetArg(1, IR::Value{copies[1]});
        IR::Inst* const sample = &*block.PrependNewInst(insert_point, inst);
        sample->SetArg(0, IR::Value{image_handle});
        sample->SetArg(1, IR::Value{copies[2]});
        sampled.push_back(IR::Value{sample});
    }

    IR::IREmitter ir{block, insert_point};
    IR::Value output = sampled.back();
    for (std::size_t side = branches - 1; side-- > 0;) {
        const IR::U1 choose =
            ir.IEqual(IR::U32{selector}, ir.Imm32(static_cast<u32>(side)));
        output = SelectImagePhiResult(ir, choose, sampled[side], output);
    }
    inst.ReplaceUsesWithAndRemove(output);
    remaining_budget -= branches - 1;
    const size_t indexed_words = std::ranges::count(indexed_read_phi, true);
    if (indexed_words != 0) {
        LOG_INFO(Render_Recompiler,
                 "GPU-indexed Phi sharp selection lowered to {} static branches for {} descriptor words",
                 branches, indexed_words);
    }
    return true;
}
