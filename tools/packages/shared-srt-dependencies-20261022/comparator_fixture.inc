struct PointerListCompare {
    bool operator()(const IR::Value& a, const IR::Value& b) const {
        if (a.IsImmediate() && b.IsImmediate()) {
            return a.U32() < b.U32();
        } else if (!a.IsImmediate() && !b.IsImmediate()) {
            auto a_inst = a.Inst();
            auto b_inst = b.Inst();
            auto a_block = a_inst->GetParent();
            auto b_block = b_inst->GetParent();
            if (a_block != b_block) {
                return a_block < b_block;
            }
            auto it = a_block->Instructions().iterator_to(*a_inst);
            auto end = a_block->Instructions().end();
            for (; it != end; ++it) {
                if (&*it == b_inst) {
                    return true;
                }
            }
            return false;
        } else {
            return a.IsImmediate();
        }
    }
};
