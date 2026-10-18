#!/usr/bin/env python3
"""Fail closed if Linux UFFD read-first changes guest/GPU coherence semantics."""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path

OLD_BLOB = "8574e55d2500f662ce0cdfc93df6ca47a60e29c5"
NEW_BLOB = "7186c176b15f4d6f8cab0a03d2ca7189e64e456f"

def blob(data: bytes) -> str:
    # Git-compatible SHA1 blob verification matches the immutable commit.
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def require(ok: bool, tag: str) -> None:
    if not ok:
        raise SystemExit(f"READFIRST_CONTRACT_FAIL={tag}")

def main() -> None:
    require(len(sys.argv) == 3, "TWO_SOURCE_PATHS_REQUIRED")
    old_bytes, new_bytes = (Path(p).read_bytes() for p in sys.argv[1:])
    require(blob(old_bytes) == OLD_BLOB, "ORIGINAL_BLOB_CHANGED")
    require(blob(new_bytes) == NEW_BLOB, "NEW_SOURCE_BLOB_CHANGED")
    old, new = old_bytes.decode(), new_bytes.decode()
    future = "            // The kernel can return multiple independent CPU-origin page"
    require(old.count(future) == new.count(future) == 1, "FAULT_DISPATCH_ANCHOR")
    require(old.split(future, 1)[1] == new.split(future, 1)[1],
            "FAULT_DISPATCH_WAKEUPS_ORDER_CHANGED")
    flag_anchor = "    // Owned exclusively by UffdHandler"
    new_flag = "    // O_NONBLOCK lets us read queued UFFD events"
    require(old.count(flag_anchor) == 1 and new.count(new_flag) == 1, "FLAG_ANCHOR")
    require(old.split(flag_anchor, 1)[0] == new.split(new_flag, 1)[0],
            "ORIGINAL_UPSTREAM_MEMORY_CODE_CHANGED")
    wake = "    void WakeFault(VAddr addr, u32 tid)"
    handler = "    void UffdHandler(std::stop_token token)"
    for token in (wake, handler):
        require(old.count(token) == new.count(token) == 1, f"MISSING_{token}")
    require(old.split(wake, 1)[1].split(handler, 1)[0] ==
            new.split(wake, 1)[1].split(handler, 1)[0],
            "UFFD_WAKE_RANGE_OR_GPU_COHERENCE_CHANGED")
    require("SYS_userfaultfd, O_CLOEXEC | O_NONBLOCK" in old,
            "O_NONBLOCK_CONTRACT_MISSING")
    require(new.count("SHADPS4_UFFD_READ_FIRST") == 1, "ENV_FLAG_NOT_SINGLE")
    require(r"value[1] == '\0';" in new, "ENV_FLAG_NOT_EXACT_OPTIN")
    require(new.count("if (!read_first_enabled) {") == 1,
            "ORIGINAL_POLL_PATH_NOT_GUARDED")
    require(new.count("if (read_first_enabled) {") == 1,
            "READ_FIRST_EAGAIN_POLL_GUARD")
    require(new.count("const int pollres = poll(&descriptor, 1, 200);") == 2,
            "BOUNDED_POLL_PRESENT_FOR_BOTH_MODES")
    require(new.count("const ssize_t bytes = read(uffd, messages.data(), requested);") == 1,
            "READ_ABI_CHANGED")
    require(new.count("if (errno == EAGAIN || errno == EWOULDBLOCK)") == 1,
            "EAGAIN_PATH_UNEXPECTED")
    require(new.count("if (errno == EINTR)") >= 1, "EINTR_HANDLING_MISSING")
    require("read_first={} poll_calls={} read_eagain={}" in new,
            "POLL_SYSCALL_INSTRUMENTATION_MISSING")
    require("std::vector<CpuFault> cpu_bundle" in new and
            "rasterizer->InvalidateMemoryOnGpuThread(addr, 1," in new and
            "WakeFault(fault.addr, fault.tid);" in new,
            "GPU_CALLBACK_OR_PAGE_COHERENCY_CHANGED")
    print("UFFD_READFIRST_SOURCE_BLOB_CONTRACT=PASS")
    print("UFFD_PAGE_INVALIDATE_WAKEUP_ORDER_IDENTICAL=PASS")
    print("UFFD_ORIGINAL_POLL_BEHAVIOR_DEFAULT=PASS")
    print("UFFD_NONBLOCK_READFIRST_OPTIN_AND_BOUNDED_WAIT=PASS")
    print("UFFD_BACKEND_GUEST_AUDIO_RENDERER_UNCHANGED=PASS")

if __name__ == "__main__":
    main()
