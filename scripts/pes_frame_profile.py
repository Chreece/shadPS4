# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded frame-path observation for the pinned PES diagnostic, not a game fix."""

APIS = (
    "sceGnmSubmitCommandBuffers", "sceGnmSubmitCommandBuffersForWorkload",
    "sceGnmSubmitAndFlipCommandBuffers", "sceGnmSubmitAndFlipCommandBuffersForWorkload",
    "sceGnmSubmitDone", "sceGnmDingDong", "sceGnmDingDongForWorkload",
    "sceVideoOutSubmitFlip", "sceVideoOutSubmitEopFlip",
    "sceVideoOutGetFlipStatus", "sceVideoOutGetVblankStatus",
    "VideoOutDriver::Flip", "VideoOutDriver::DrawBlankFrame",
    "VideoOutDriver::DrawLastFrame", "VideoOutDriver::SubmitFlipInternal",
    "Rasterizer::Draw", "Rasterizer::DrawIndirect", "Rasterizer::DispatchDirect",
    "Rasterizer::DispatchIndirect", "Rasterizer::FilterDraw",
    "Rasterizer::PrepareRenderState", "Rasterizer::ResetBindings",
    "scePlayGoGetInstallSpeed", "scePlayGoGetLanguageMask", "scePlayGoGetToDoList",
    "scePlayGoGetProgress", "scePlayGoGetLocus", "sceKernelStat",
)
OPTIONAL_APIS = ("Rasterizer::FilterDraw", "Rasterizer::PrepareRenderState", "Rasterizer::ResetBindings")

SUPPORT = r'''
frame_returns = {}


class Return(gdb.Breakpoint):
    def __init__(self, context):
        # Entry breakpoints stop at the first instruction, before the prologue.
        # A tail jump retains this same return slot; FinishBreakpoint's frame
        # lifetime inference can drop the outer call in stripped optimized code.
        stack = register("rsp")
        address = number(memory(stack, 8), 0)
        key = (context["thread"], stack, context["api"])
        previous = frame_returns.get(key)
        if previous is not None:
            # A new invocation supersedes an unmatched earlier one (e.g. unwind).
            result["apis"][context["api"]]["out_of_scope"] += 1
            if previous.is_valid():
                previous.delete()
        super().__init__("*" + hex(address), internal=True)
        self.thread = context["thread"]
        self.silent = True
        self.expected_rsp = stack + 8
        self.context = context
        self.key = key
        context["return_probe"] = self
        frame_returns[key] = self

    def stop(self):
        # Another recursive invocation or thread at this address is not this return.
        if (gdb.selected_thread().global_num != self.context["thread"] or
                register("rsp") != self.expected_rsp):
            return False
        pending.append(("return", self.context))
        return True


def consume_return(context):
    # Delete outside Breakpoint.stop(), as required by the GDB Python API.
    probe = context.pop("return_probe")
    frame_returns.pop(probe.key, None)
    if probe.is_valid():
        probe.delete()


def capture_args(api):
    args = [register(r) for r in ("rdi", "rsi", "rdx", "rcx", "r8", "r9")]
    if api in ("sceGnmSubmitAndFlipCommandBuffers", "sceGnmSubmitAndFlipCommandBuffersForWorkload"):
        # PS4_SYSV_ABI: six integer/pointer registers, then slots above return RIP.
        count = 4 if api.endswith("ForWorkload") else 3
        stack = memory(register("rsp") + 8, count * 8)
        args += [number(stack, i * 8) for i in range(count)]
    return args


def return_kind(api):
    if api in ("Rasterizer::FilterDraw", "PES::InitStage"):
        return "bool"
    if api in ("PES::InitListener", "PES::GameTick", "PES::DispatcherTick"):
        return "void"
    return "void" if api.startswith(("VideoOutDriver::", "sceGnmDingDong", "Rasterizer::")) else "error_code"


def signed32(value):
    value &= 0xffffffff
    return value - (1 << 32) if value & (1 << 31) else value


def fields_before(api, args):
    if api in ("PES::GameTick", "PES::DispatcherTick"):
        return {"object": hex(args[0]), "object_bytes": memory(args[0], 128).hex(),
                "delta_seconds": str(gdb.parse_and_eval("$xmm0.v2_double[0]")),
                "caller": hex(number(memory(register("rsp"), 8), 0))}
    if api == "PES::InitListener":
        return dict(listener_fields(args[0]),
                    event_type=number(memory(args[2], 4), 0, 4),
                    event_address=hex(args[2]))
    if api == "PES::InitStage":
        return {"stage_state": number(memory(0x5e7f984, 4), 0, 4)}
    if api.startswith("Rasterizer::"):
        if api in ("Rasterizer::Draw", "Rasterizer::DrawIndirect"):
            return {"indexed": bool(args[1] & 0xff)}
        if api == "Rasterizer::ResetBindings":
            return {"is_compute": bool(args[1] & 0xff)}
        return {}
    if api.startswith("scePlayGo"):
        values = {"handle": signed32(args[0]), "caller": hex(number(memory(register("rsp"), 8), 0))}
        if api in ("scePlayGoGetProgress", "scePlayGoGetLocus"):
            count = args[2] & 0xffffffff
            data = memory(args[1], min(count, 8) * 2) if count and args[1] else b""
            values.update(chunk_count=count, chunks=[number(data, i, 2) for i in range(0, len(data), 2)])
        return values
    if api == "sceKernelStat":
        path = bytearray()
        for offset in range(512):
            value = memory(args[0] + offset, 1)
            if value == b"\0":
                break
            path.extend(value)
        return {"path": path.decode("utf-8", errors="replace"), "path_truncated": len(path) == 512,
                "caller": hex(number(memory(register("rsp"), 8), 0))}
    if api.startswith("sceGnmSubmit") and api != "sceGnmSubmitDone":
        shift = int(api.endswith("ForWorkload"))
        count = args[shift] & 0xffffffff
        values = {"buffer_count": count}
        if count and args[shift + 1] and args[shift + 2]:
            length = min(count, 4)
            addresses = memory(args[shift + 1], length * 8)
            sizes = memory(args[shift + 2], length * 4)
            values["dcb_addresses"] = [hex(number(addresses, i * 8)) for i in range(length)]
            values["dcb_bytes"] = [number(sizes, i * 4, 4) for i in range(length)]
        if api.startswith("sceGnmSubmitAndFlip"):
            values.update(handle=signed32(args[shift + 5]),
                          buffer_index=signed32(args[shift + 6]),
                          flip_mode=signed32(args[shift + 7]), flip_arg=hex(args[shift + 8]))
        return values
    if api.startswith("sceGnmDingDong"):
        return {"queue": args[0] & 0xffffffff, "offset_dw": args[1] & 0xffffffff}
    if api in ("sceVideoOutSubmitFlip", "sceVideoOutSubmitEopFlip"):
        return {"handle": signed32(args[0]), "buffer_index": signed32(args[1]),
                "flip_mode": signed32(args[2]), "flip_arg": hex(args[3])}
    if api == "VideoOutDriver::SubmitFlipInternal":
        return {"buffer_index": signed32(args[2]), "flip_arg": hex(args[3]),
                "eop": bool(args[4] & 0xff)}
    if api == "VideoOutDriver::Flip":
        # Request in driver.h: frame*, port*, s64 flip_arg, s32 index, bool eop.
        request = memory(args[1], 32)
        return {"frame": hex(number(request, 0)), "flip_arg": hex(number(request, 16)),
                "buffer_index": signed32(number(request, 24, 4)), "eop": bool(request[28])}
    return {}


def fields_after(api, args, rc):
    if api in ("PES::GameTick", "PES::DispatcherTick"):
        return {"object": hex(args[0]), "object_bytes": memory(args[0], 128).hex()}
    if api == "PES::InitListener":
        return listener_fields(args[0])
    if api == "PES::InitStage":
        return {"complete": bool(rc),
                "stage_state": number(memory(0x5e7f984, 4), 0, 4)}
    if api == "Rasterizer::FilterDraw":
        return {"draw_allowed": bool(rc)}
    if rc != 0:
        return {}
    if api == "scePlayGoGetInstallSpeed":
        return {"install_speed": signed32(number(memory(args[1], 4), 0, 4))}
    if api == "scePlayGoGetLanguageMask":
        return {"language_mask": hex(number(memory(args[1], 8), 0))}
    if api == "scePlayGoGetToDoList":
        return {"todo_entries": number(memory(args[3], 4), 0, 4)}
    if api == "scePlayGoGetProgress":
        data = memory(args[3], 16)
        return {"installed_bytes": number(data, 0), "total_bytes": number(data, 8)}
    if api == "scePlayGoGetLocus":
        count = min(args[2] & 0xffffffff, 8)
        return {"loci": list(memory(args[3], count))}
    if api == "sceVideoOutGetFlipStatus":
        data = memory(args[1], 64)
        return {"flip_count": number(data, 0), "flip_arg": hex(number(data, 24)),
                "gc_queue_num": signed32(number(data, 48, 4)),
                "flip_pending_num": signed32(number(data, 52, 4)),
                "current_buffer": signed32(number(data, 56, 4))}
    if api == "sceVideoOutGetVblankStatus":
        return {"vblank_count": number(memory(args[1], 40), 0)}
    return {}


def completion_snapshot(pc, key):
    # These addresses/signature come from the three supplied guest disassemblies.
    # Only interpret RBX after its key assignment, while inside that exact function.
    if not 0x2e81919 <= pc <= 0x2e819ae:
        return {"status": "outside_known_completion_check"}
    signature = bytes.fromhex("48 8b 48 08 48 8b 09 48 3b 08")
    if memory(0x2e81997, len(signature)) != signature:
        return {"status": "guest_signature_mismatch"}
    table = number(memory(0x5fff258, 8), 0)
    slot = table + 24 * (key & 1023)
    entry = memory(slot, 24)
    expected, pointer = number(entry, 0), number(entry, 8)
    observed = number(memory(pointer, 8), 0) if pointer else None
    return {"status": "sampled", "key": hex(key), "slot": hex(slot),
            "expected": hex(expected), "pointer": hex(pointer),
            "observed": hex(observed) if observed is not None else None,
            "slot_matches_key": expected == key, "equal_at_sample": observed == expected}


def request_native_screenshots():
    requests = []
    for kind in ("game_only", "with_overlays"):
        symbol = "VideoCore::screenshot_" + kind + "_count"
        address = int(gdb.parse_and_eval("(void*)&'" + symbol + "'"))
        value = number(memory(address, 4), 0, 4)
        if value != 0:
            raise RuntimeError("Screenshot already pending: " + symbol)
        requests.append((kind, address))
    for kind, address in requests:
        gdb.selected_inferior().write_memory(address, (1).to_bytes(4, "little"))
        result.setdefault("screenshot_requests", []).append({"kind": kind, "address": hex(address)})


def guest_wait_snapshot(phase):
    started_at = time.monotonic()
    original = gdb.selected_thread()
    snapshot = {"phase": phase, "threads": [], "memory": [], "code": [], "errors": []}
    deadline = started_at + 1.0
    regions = []
    consumed = 0
    saved = set()
    code_saved = set()
    for line in (proc / "maps").read_text().splitlines():
        fields = line.split()
        low, high = (int(value, 16) for value in fields[0].split("-"))
        if "r" in fields[1] and low < (1 << 40):
            regions.append((low, min(high, 1 << 40), fields[1]))

    def region_at(address, executable=False):
        return next((r for r in regions if r[0] <= address < r[1] and
                     (not executable or "x" in r[2])), None)

    def read_window(address, length, kind):
        nonlocal consumed
        region = region_at(address)
        if region is None or time.monotonic() >= deadline:
            return b""
        length = min(length, region[1] - address, 32768 - consumed)
        key = (address, length)
        if length <= 0 or key in saved:
            return b""
        consumed += length
        saved.add(key)
        try:
            data = memory(address, length)
            snapshot["memory"].append({"address": hex(address), "kind": kind,
                                       "bytes": data.hex()})
            return data
        except Exception as error:
            snapshot["errors"].append("read " + hex(address) + ": " + str(error))
            return b""

    def code_window(address):
        region = region_at(address, executable=True)
        if region is None or address in code_saved or len(code_saved) >= 12:
            return
        if time.monotonic() >= deadline:
            return
        code_saved.add(address)
        start = max(region[0], address - 256)
        read_window(start, 768, "code around " + hex(address))
        try:
            instructions = gdb.selected_frame().architecture().disassemble(
                address, min(address + 192, region[1]))
            snapshot["code"].append({"pc": hex(address), "instructions": instructions})
        except Exception as error:
            snapshot["errors"].append("code " + hex(address) + ": " + str(error))

    try:
        threads = sorted(gdb.selected_inferior().threads(),
                         key=lambda t: (t.name != "Game:Main", t.global_num))
        for thread in threads:
            name = thread.name or ""
            if name.startswith(("shadPS4", "SDL", "Pulse", "WSI ")):
                continue
            if time.monotonic() >= deadline or len(snapshot["threads"]) >= 96:
                snapshot["limited"] = True
                break
            item = {"thread": thread.global_num, "ptid": list(thread.ptid), "name": name,
                    "registers": {}, "frames": []}
            snapshot["threads"].append(item)
            try:
                thread.switch()
                for reg in ("rip", "rsp", "rbp", "rax", "rbx", "rcx", "rdx", "rsi", "rdi",
                            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags"):
                    item["registers"][reg] = hex(register(reg))
                stack_address = register("rsp")
                stack = read_window(stack_address, 512 if name == "Game:Main" else 128,
                                    name + " stack")
                frame = gdb.newest_frame()
                for _ in range(24 if name == "Game:Main" else 12):
                    if frame is None or time.monotonic() >= deadline:
                        break
                    address = frame.pc()
                    item["frames"].append({"pc": hex(address), "symbol": frame.name()})
                    code_window(address)
                    frame = frame.older()
                if name == "Game:Main":
                    pointers = [int(value, 16) for value in item["registers"].values()]
                    pointers += [number(stack, offset) for offset in range(0, len(stack) - 7, 8)]
                    for address in pointers[:64]:
                        if region_at(address, executable=True):
                            code_window(address)
                        else:
                            data = read_window(address, 128, "main register/stack pointer")
                            for offset in range(0, min(len(data), 32), 8):
                                nested = number(data, offset)
                                if not region_at(nested, executable=True):
                                    read_window(nested, 64, "main nested pointer")
            except Exception as error:
                item["error"] = str(error)
    finally:
        if original is not None and original.is_valid():
            original.switch()
        snapshot["read_bytes"] = consumed
        snapshot["seconds"] = round(time.monotonic() - started_at, 4)
        result.setdefault("guest_wait_samples", []).append(snapshot)


def listener_fields(address):
    data = memory(address, 0x48)
    return {"object": hex(address), "vtable": hex(number(data, 0)),
            "substate": number(data, 0x30, 4), "cancelled": bool(data[0x34]),
            "complete": bool(data[0x35]), "notify": bool(data[0x36]),
            "pending_object": hex(number(data, 0x38)),
            "pending_status": number(data, 0x40, 4)}


def arm_startup_listener_probes(sample):
    if not sample.get("listener", {}).get("code_verified"):
        return
    for api, address in (("PES::InitStage", 0x6ce3a0), ("PES::InitListener", 0x6ce910)):
        entries.append(Entry(address, api))
        result["apis"][api] = {"calls": 0, "returns": 0, "errors": {},
                                "valid_frames": 0, "out_of_scope": 0, "capped": False}
        result["resolved_symbols"][api] = hex(address)


def dispatcher_snapshot(phase, stage_sample):
    import re
    started = time.monotonic()
    deadline = started + 0.75
    sample = {"phase": phase, "status": "unverified_image", "roots": [],
              "memory": [], "code": [], "errors": [], "read_bytes": 0}
    result.setdefault("dispatcher_samples", []).append(sample)
    if not stage_sample.get("listener", {}).get("code_verified"):
        return
    regions = []
    for line in (proc / "maps").read_text().splitlines():
        fields = line.split()
        low, high = (int(value, 16) for value in fields[0].split("-"))
        if "r" in fields[1] and low < (1 << 40):
            regions.append((low, min(high, 1 << 40), fields[1]))
    saved, objects, methods = {}, [], []
    seen_objects, seen_methods = set(), set()

    def region_at(address, executable=False):
        return next((r for r in regions if r[0] <= address < r[1] and
                     (not executable or "x" in r[2])), None)

    def read(address, size, kind):
        region = region_at(address)
        if region is None or time.monotonic() >= deadline:
            return b""
        size = min(size, region[1] - address, 524288 - sample["read_bytes"])
        if size <= 0:
            return b""
        key = (address, size)
        if key not in saved:
            data = memory(address, size)
            saved[key] = data
            sample["read_bytes"] += len(data)
            sample["memory"].append({"address": hex(address), "kind": kind,
                                      "bytes": data.hex()})
        return saved[key]

    def enqueue_object(address, depth):
        # Follow aligned data pointers with bounded depth and node count.
        if (address & 7 or address < 0x4b70000 or not region_at(address) or
                region_at(address, True) or address in seen_objects or depth > 4):
            return
        if len(seen_objects) >= 160:
            sample["object_limit_reached"] = True
            return
        seen_objects.add(address)
        objects.append((address, depth))

    def enqueue_method(address, depth):
        if address in seen_methods or not region_at(address, True):
            return
        if len(seen_methods) >= 32:
            sample["code_limit_reached"] = True
            return
        seen_methods.add(address)
        methods.append((address, depth))

    try:
        for api, global_address, expected_vtable, slot in (
                ("PES::GameTick", 0x5e7f8d8, 0x4d8b140, 0x40),
                ("PES::DispatcherTick", 0x5e7f8c0, 0x4d8aed8, 0x38)):
            pointer = read(global_address, 8, api + " global")
            if len(pointer) != 8:
                raise RuntimeError(api + " global unreadable")
            address = number(pointer, 0)
            data = read(address, 512, api + " object")
            if len(data) < 128 or number(data, 0) != expected_vtable:
                raise RuntimeError(api + " vtable mismatch")
            table = read(expected_vtable, 192, api + " vtable")
            target = number(table, slot) if len(table) >= slot + 8 else 0
            if not region_at(target, True):
                raise RuntimeError(api + " method is not executable")
            sample["roots"].append({"api": api, "object": hex(address),
                                    "vtable": hex(expected_vtable), "slot": slot,
                                    "target": hex(target)})
            enqueue_method(target, 0)
            enqueue_object(address, 0)
            # The game context owns the event dispatcher at +0x10.
            if api == "PES::GameTick":
                dispatcher = number(data, 0x10)
                state = read(dispatcher, 512, "game event dispatcher")
                enqueue_object(dispatcher, 0)
                if len(state) >= 8:
                    vtable = read(number(state, 0), 192, "event dispatcher vtable")
                    for offset in range(0, len(vtable) - 7, 8):
                        enqueue_method(number(vtable, offset), 1)
        # Preserve this compact engine area for offline inspection of registration,
        # queue processing and event filtering without another capture for each callee.
        for address, size, label in ((0x2770000, 0x4000, "event engine code"),
                                     (0x6cbf00, 0x1200, "dispatcher call sites"),
                                     (0x1e6fae0, 0x840, "game mode gates")):
            if region_at(address, True):
                read(address, size, label)
        # Capture the root listener lists before following incidental code references.
        while objects and time.monotonic() < deadline:
            address, depth = objects.pop(0)
            data = read(address, 512, "dispatcher object graph")
            for offset in range(0, len(data) - 7, 8):
                enqueue_object(number(data, offset), depth + 1)
        while methods and time.monotonic() < deadline:
            address, depth = methods.pop(0)
            data = read(address, 4096, "dispatcher method")
            if len(data) < 16:
                continue
            instructions = gdb.selected_inferior().architecture().disassemble(
                address, address + min(2048, len(data) - 15))
            sample["code"].append({"pc": hex(address), "instructions": instructions})
            for instruction in instructions:
                asm = instruction["asm"]
                direct = re.match(r"(?:call\w*|jmp\w*)\s+(0x[0-9a-fA-F]+)", asm)
                if direct and depth < 2:
                    enqueue_method(int(direct[1], 16), depth + 1)
                reference = re.search(r"#\s*(0x[0-9a-fA-F]+)", asm)
                if reference:
                    target = int(reference[1], 16)
                    values = read(target, 256, "dispatcher referenced data")
                    if not region_at(target, True):
                        enqueue_object(target, 1)
                        for offset in range(0, min(len(values), 64) - 7, 8):
                            enqueue_object(number(values, offset), 1)
        while objects and time.monotonic() < deadline:
            address, depth = objects.pop(0)
            data = read(address, 512, "dispatcher object graph")
            for offset in range(0, len(data) - 7, 8):
                enqueue_object(number(data, offset), depth + 1)
        sample["status"] = "sampled"
        sample["limited"] = bool(methods or objects or sample.get("object_limit_reached") or
                                 sample.get("code_limit_reached"))
    except Exception as error:
        sample["status"] = "failed"
        sample["errors"].append(str(error))
    finally:
        sample["seconds"] = round(time.monotonic() - started, 4)


def arm_dispatcher_probes(sample):
    if sample.get("status") != "sampled":
        return
    for root in sample["roots"]:
        api, address = root["api"], int(root["target"], 16)
        entries.append(Entry(address, api))
        result["apis"][api] = {"calls": 0, "returns": 0, "errors": {},
                               "valid_frames": 0, "out_of_scope": 0, "capped": False}
        result["resolved_symbols"][api] = hex(address)


def startup_stage_snapshot(phase):
    import re
    started = time.monotonic()
    deadline = started + 0.5
    sample = {"phase": phase, "status": "signature_mismatch", "memory": [],
              "code": [], "errors": [], "read_bytes": 0}
    regions = []
    saved = {}
    queued = set()
    queue = []
    for line in (proc / "maps").read_text().splitlines():
        fields = line.split()
        low, high = (int(value, 16) for value in fields[0].split("-"))
        if "r" in fields[1] and low < (1 << 40):
            regions.append((low, min(high, 1 << 40), fields[1]))

    def region_at(address, executable=False):
        return next((r for r in regions if r[0] <= address < r[1] and
                     (not executable or "x" in r[2])), None)

    def read(address, size, kind):
        region = region_at(address)
        if region is None or time.monotonic() >= deadline:
            return b""
        size = min(size, region[1] - address, 131072 - sample["read_bytes"])
        if size <= 0:
            return b""
        key = (address, size)
        if key in saved:
            return saved[key]
        try:
            data = memory(address, size)
            saved[key] = data
            sample["read_bytes"] += size
            sample["memory"].append({"address": hex(address), "kind": kind,
                                      "bytes": data.hex()})
            return data
        except Exception as error:
            sample["errors"].append(hex(address) + ": " + str(error))
            return b""

    def enqueue(address, depth, kind, priority=False):
        if address not in queued and region_at(address, True) and len(queued) >= 64:
            sample["code_limit_reached"] = True
        if address not in queued and region_at(address, True) and len(queued) < 64:
            queued.add(address)
            if priority:
                queue.insert(0, (address, depth, kind))
            else:
                queue.append((address, depth, kind))

    try:
        signatures = ((0x6c8d3b, "8b05fb687b0583f80f0f848b000000"),
                      (0x6c8d73, "489848c1e00542ff543810"))
        if any(read(address, len(bytes.fromhex(expected)), "signature") !=
               bytes.fromhex(expected) for address, expected in signatures):
            return
        value = read(0x5e7f63c, 4, "startup stage index")
        table = read(0x4c113d0, 15 * 32, "startup callback table")
        if len(value) != 4 or len(table) != 15 * 32:
            sample["status"] = "unreadable_stage"
            return
        stage = number(value, 0, 4)
        sample.update(status="sampled" if stage <= 15 else "invalid_stage",
                      stage_index=stage, stage_count=15, callbacks=[])
        for index in range(15):
            entry = [number(table, index * 32 + offset) for offset in range(0, 32, 8)]
            sample["callbacks"].append({"index": index, "words": [hex(v) for v in entry],
                                         "poll_callback": hex(entry[2])})
        signature = bytes.fromhex("554889e54157415641554154534883ec48")
        poll_signature = bytes.fromhex("554889e541574156534883ec18")
        if (read(0x6ce910, len(signature), "listener signature") == signature and
                read(0x6ce3a0, len(poll_signature), "listener poll signature") == poll_signature):
            listener = sample["listener"] = {"code_verified": True}
            state = read(0x5e7f984, 20, "listener globals")
            address = number(state, 4) if len(state) == 20 else 0
            body = read(0x6ce910, 0x6cf6c0 - 0x6ce910, "complete init listener")
            read(0x3639dd0, 19 * 4, "init listener switch table")
            if len(body) == 0x6cf6c0 - 0x6ce910:
                sample["code"].append({"pc": hex(0x6ce910), "kind": "complete init listener",
                    "instructions": gdb.selected_inferior().architecture().disassemble(
                        0x6ce910, 0x6cf6c0 - 15)})
            data = read(address, 0x48, "init listener state")
            if len(data) == 0x48 and number(data, 0) == 0x4c11a68:
                listener.update(listener_fields(address))
                substate = listener["substate"]
                if 1 <= substate <= 19:
                    slot = read(0x3639dd0 + (substate - 1) * 4, 4, "selected listener branch")
                    if len(slot) == 4:
                        target = 0x3639dd0 + int.from_bytes(slot, "little", signed=True)
                        listener["selected_branch"] = hex(target)
                        if 0x6ce910 <= target < 0x6cf6c0:
                            enqueue(target, -1, "selected listener branch", priority=True)
                        else:
                            raise RuntimeError("Init listener switch target outside verified callback")
        if stage < 15:
            enqueue(number(table, stage * 32 + 16), 0, "active startup callback")
        enqueue(0x6c8d3b, 0, "startup dispatcher")
        for index in range(15):
            enqueue(number(table, index * 32 + 16), 0, "startup callback " + str(index))
        while queue and time.monotonic() < deadline and sample["read_bytes"] < 131072:
            address, depth, kind = queue.pop(0)
            data = read(address, 1536 if depth <= 0 else 768, kind)
            if len(data) < 16:
                continue
            instructions = gdb.selected_inferior().architecture().disassemble(
                address, address + min(len(data) - 15, 1024 if depth <= 0 else 512))
            sample["code"].append({"pc": hex(address), "kind": kind,
                                    "instructions": instructions})
            for instruction in instructions:
                asm = instruction["asm"]
                direct = re.match(r"call\w*\s+(0x[0-9a-fA-F]+)", asm)
                if direct and depth <= 0:
                    enqueue(int(direct[1], 16), depth + 1, "direct startup callee",
                            priority=depth < 0)
                reference = re.search(r"#\s*(0x[0-9a-fA-F]+)", asm)
                if not reference:
                    continue
                target = int(reference[1], 16)
                values = read(target, 256, "startup referenced data")
                if region_at(target, True):
                    if depth <= 0 and values.startswith((b"\x55\x48\x89\xe5", b"\xe9")):
                        enqueue(target, depth + 1, "referenced startup code")
                    continue
                for offset in range(0, min(len(values), 64) - 7, 8):
                    pointer = number(values, offset)
                    if region_at(pointer, True):
                        if depth == 0:
                            enqueue(pointer, 1, "startup function pointer")
                    else:
                        read(pointer, 512, "startup referenced object")
        sample["limited"] = bool(queue) or sample.get("code_limit_reached", False)
    except Exception as error:
        sample["status"] = "failed"
        sample["errors"].append(str(error))
    finally:
        sample["seconds"] = round(time.monotonic() - started, 4)
        result.setdefault("startup_stage_samples", []).append(sample)


def profile_snapshot(phase):
    original = gdb.selected_thread()
    sample = {"phase": phase, "status": "game_thread_not_found"}
    try:
        for thread in gdb.selected_inferior().threads():
            if thread.name != "Game:Main":
                continue
            thread.switch()
            pc, key = register("rip"), register("rbx")
            sample.update(status="sampled", pc=hex(pc), rbx=hex(key),
                          completion=completion_snapshot(pc, key))
            sample["backtrace"] = gdb.execute("bt 12", to_string=True)[-12000:]
            break
    except Exception as error:
        sample["read_error"] = str(error)
    finally:
        if original is not None and original.is_valid():
            original.switch()
    result.setdefault("game_samples", []).append(sample)
    if CONFIG.get("guest_wait"):
        guest_wait_snapshot(phase)
        startup_stage_snapshot(phase)
        dispatcher_snapshot(phase, result["startup_stage_samples"][-1])
        if phase == "before":
            arm_startup_listener_probes(result["startup_stage_samples"][-1])
            arm_dispatcher_probes(result["dispatcher_samples"][-1])
    if phase == "after" and CONFIG.get("screenshots") and result.get("status") == "complete":
        request_native_screenshots()
'''

PROFILE = {
    "guest_wait": True,
    "required_apis": tuple(api for api in APIS if api not in OPTIONAL_APIS),
    "allowed_apis": APIS,
    "only_required": True,
    "symbol_queries": ("sceGnmSubmit", "sceGnmDingDong", "sceVideoOutSubmit",
                       "sceVideoOutGetFlipStatus", "sceVideoOutGetVblankStatus", "VideoOutDriver::",
                       "Vulkan::Rasterizer::", "scePlayGoGet", "sceKernelStat"),
    "symbol_pattern": (r"^\s*(0x[0-9a-fA-F]+)\s+(?:Libraries::"
                       r"(?:GnmDriver|VideoOut|PlayGo|Kernel)::|Vulkan::)([A-Za-z0-9_:]+)\("),
    "profile_source": SUPPORT,
}

# The Docker-only target has the same ABI and layouts, with known sentinel values.
# No real renderer, emulator, game, or user data is mounted into that container.
FIXTURE_CPP = r'''
#define PROBE_API __attribute__((noinline, noipa, used))
namespace VideoCore {
static std::atomic<unsigned> screenshot_game_only_count{0};
static std::atomic<unsigned> screenshot_with_overlays_count{0};
}
namespace Libraries::GnmDriver {
PROBE_API int sceGnmSubmitCommandBuffers(unsigned, void**, unsigned*, void**, unsigned*) { return 0; }
PROBE_API int sceGnmSubmitCommandBuffersForWorkload(unsigned, unsigned, void**, unsigned*, void**, unsigned*) { return 0; }
PROBE_API int sceGnmSubmitAndFlipCommandBuffers(unsigned, void**, unsigned*, void**, unsigned*, unsigned, unsigned, unsigned, std::int64_t) { return 0; }
PROBE_API int sceGnmSubmitAndFlipCommandBuffersForWorkload(unsigned, unsigned, void**, unsigned*, void**, unsigned*, unsigned, unsigned, unsigned, std::int64_t) { return 0; }
PROBE_API int sceGnmSubmitDone() {
    static unsigned calls = 0;
    return ++calls % 5 == 0 ? static_cast<int>(0x80d11000u) : 0;
}
PROBE_API void sceGnmDingDong(unsigned, unsigned) {}
PROBE_API void sceGnmDingDongForWorkload(unsigned, unsigned, std::uint64_t) {}
}
namespace Libraries::VideoOut {
PROBE_API int sceVideoOutSubmitEopFlip(int, unsigned, unsigned, std::int64_t, void**) { return 0; }
// Force the same return-PC case as optimized emulator forwarding wrappers.
PROBE_API __attribute__((optimize("O2"))) int sceVideoOutSubmitFlip(int handle, int index, int mode, std::int64_t tag) {
    return sceVideoOutSubmitEopFlip(handle, index, mode, tag, nullptr);
}
PROBE_API int sceVideoOutGetFlipStatus(int, Bytes* data) {
    static std::uint64_t count = 0;
    put<std::uint64_t>(data, 0, ++count);
    put<std::uint64_t>(data, 24, 0x1122334455667788ULL);
    put<std::int32_t>(data, 48, 2);
    put<std::int32_t>(data, 52, 3);
    put<std::int32_t>(data, 56, -1);
    return 0;
}
PROBE_API int sceVideoOutGetVblankStatus(int, Bytes* data) {
    static std::uint64_t count = 100;
    put<std::uint64_t>(data, 0, ++count);
    return 0;
}
struct Request { void* frame; void* port; std::int64_t flip_arg; int index; bool eop; };
class VideoOutDriver {
public:
    PROBE_API void Flip(const Request&);
    PROBE_API void DrawBlankFrame();
    PROBE_API void DrawLastFrame();
    PROBE_API void SubmitFlipInternal(void*, int, std::int64_t, bool);
};
void VideoOutDriver::Flip(const Request&) {}
void VideoOutDriver::DrawBlankFrame() {}
void VideoOutDriver::DrawLastFrame() {}
void VideoOutDriver::SubmitFlipInternal(void*, int, std::int64_t, bool) {}
}
namespace Vulkan {
class Rasterizer {
public:
    PROBE_API void Draw(bool, unsigned) {}
    PROBE_API void DrawIndirect(bool, std::uint64_t, unsigned, unsigned, unsigned, std::uint64_t, unsigned short, unsigned short) {}
    PROBE_API void DispatchDirect() {}
    PROBE_API void DispatchIndirect(std::uint64_t, unsigned, unsigned) {}
    PROBE_API bool FilterDraw() { static unsigned count = 0; return ++count % 2 != 0; }
    PROBE_API void PrepareRenderState(const void*) {}
    PROBE_API void ResetBindings(bool) {}
};
}
// Real ELF symbols with the suffixes emitted for outlined/cold fragments.
// These must never be treated as function-entry breakpoints.
extern "C" PROBE_API void cold_one() asm("_ZN6Vulkan10Rasterizer12DrawIndirectEbmjjjmtt.cold.1");
extern "C" PROBE_API void cold_two() asm("_ZN6Vulkan10Rasterizer12DrawIndirectEbmjjjmtt.cold.2");
void cold_one() {}
void cold_two() {}
// The exact Clang lambda names observed in the installed PES emulator. They
// contain the API name but are separate functions with their own ABI.
extern "C" PROBE_API void nested_one() asm("_ZZN6Vulkan10Rasterizer12DrawIndirectEbmjjjmttENK3$_0clEv");
extern "C" PROBE_API void nested_two() asm("_ZZN6Vulkan10Rasterizer12DrawIndirectEbmjjjmttENK3$_1clEv");
void nested_one() {}
void nested_two() {}
namespace Libraries::PlayGo {
PROBE_API int scePlayGoGetInstallSpeed(unsigned, int* value) { *value = 2; return 0; }
PROBE_API int scePlayGoGetLanguageMask(unsigned, std::uint64_t* value) { *value = 0x4000000000000000ULL; return 0; }
PROBE_API int scePlayGoGetToDoList(unsigned, void*, unsigned, unsigned* value) { *value = 0; return 0; }
PROBE_API int scePlayGoGetProgress(unsigned, const unsigned short*, unsigned, Bytes* value) {
    put<std::uint64_t>(value, 0, 1000); put<std::uint64_t>(value, 8, 1000); return 0;
}
PROBE_API int scePlayGoGetLocus(unsigned, const unsigned short*, unsigned count, Bytes* value) {
    for (unsigned i = 0; i < count; ++i) value[i] = 3;
    return 0;
}
}
namespace Libraries::Kernel {
PROBE_API int sceKernelStat(const char*, void*) {
    static unsigned calls = 0;
    return ++calls % 5 == 0 ? static_cast<int>(0x80020002u) : 0;
}
}
void frame_activity() {
    using namespace Libraries::GnmDriver;
    using namespace Libraries::VideoOut;
    void* addresses[] = {reinterpret_cast<void*>(0x12345678), reinterpret_cast<void*>(0x23456789)};
    unsigned sizes[] = {64, 128};
    constexpr std::int64_t tag = 0x1122334455667788LL;
    sceGnmSubmitCommandBuffers(2, addresses, sizes, nullptr, nullptr);
    sceGnmSubmitCommandBuffersForWorkload(99, 2, addresses, sizes, nullptr, nullptr);
    sceGnmSubmitAndFlipCommandBuffers(2, addresses, sizes, nullptr, nullptr, 7, -1, 1, tag);
    sceGnmSubmitAndFlipCommandBuffersForWorkload(99, 2, addresses, sizes, nullptr, nullptr, 7, -1, 1, tag);
    sceGnmSubmitDone();
    sceGnmDingDong(3, 128);
    sceGnmDingDongForWorkload(3, 128, 99);
    sceVideoOutSubmitFlip(7, -1, 1, tag);
    sceVideoOutSubmitEopFlip(7, -1, 1, tag, nullptr);
    Bytes status[64]{};
    sceVideoOutGetFlipStatus(7, status);
    sceVideoOutGetVblankStatus(7, status);
    VideoOutDriver driver;
    Request request{addresses[0], nullptr, tag, -1, true};
    driver.SubmitFlipInternal(nullptr, -1, tag, true);
    driver.Flip(request);
    driver.DrawLastFrame();
    driver.DrawBlankFrame();
    Vulkan::Rasterizer rasterizer;
    rasterizer.Draw(true, 0);
    rasterizer.DrawIndirect(false, 0, 0, 0, 0, 0, 0, 0);
    rasterizer.DispatchDirect();
    rasterizer.DispatchIndirect(0, 0, 0);
    rasterizer.FilterDraw();
    rasterizer.PrepareRenderState(nullptr);
    rasterizer.ResetBindings(false);
    using namespace Libraries::PlayGo;
    int speed = 0;
    std::uint64_t language = 0;
    unsigned entries = 42;
    const unsigned short chunks[] = {0, 9};
    scePlayGoGetInstallSpeed(1, &speed);
    scePlayGoGetLanguageMask(1, &language);
    scePlayGoGetToDoList(1, status, 1, &entries);
    scePlayGoGetProgress(1, chunks, 2, status);
    scePlayGoGetLocus(1, chunks, 2, status);
    Libraries::Kernel::sceKernelStat("/app0/test-file.bin", status);
}
'''


def validate_observations(mode, result, require):
    apis = result['apis']
    skipped = [line for line in result.get('skipped_symbol_fragments', [])
               if 'Rasterizer::DrawIndirect' in line]
    require(len(skipped) == 4 and sum('operator()' in line for line in skipped) == 2,
            'Cold/lambda symbol fixtures were not excluded from entry breakpoints')
    require(set(apis) == set(APIS), 'Frame symbols are missing or unexpected')
    if mode == 'idle':
        require(all(v['calls'] == 0 for v in apis.values()), 'Idle target recorded frame activity')
        return
    for api, stats in apis.items():
        require(stats['calls'] == stats['returns'] == 32 and stats['capped'] and stats['out_of_scope'] == 0,
                'Incorrect entry/return cap for ' + api + ': ' + str(stats))
        expected = {'0x80d11000': 6} if api == 'sceGnmSubmitDone' else (
            {'0x80020002': 6} if api == 'sceKernelStat' else {})
        require(stats['errors'] == expected, 'Wrong return classification for ' + api)
    records = result['records']
    require(not any('read_error' in r for r in records), 'Frame argument/output read failed')
    for record in records:
        api = record.get('api', '')
        if record['event'] == 'enter':
            if api.startswith('sceGnmSubmit') and api != 'sceGnmSubmitDone':
                require(record.get('buffer_count') == 2 and record.get('dcb_bytes') == [64, 128]
                        and record.get('dcb_addresses') == ['0x12345678', '0x23456789'],
                        'Wrong command buffer arguments: ' + str(record))
            if api.startswith('sceGnmSubmitAndFlip') or api in ('sceVideoOutSubmitFlip', 'sceVideoOutSubmitEopFlip'):
                require(record.get('handle') == 7 and record.get('buffer_index') == -1
                        and record.get('flip_mode') == 1 and record.get('flip_arg') == '0x1122334455667788',
                        'Wrong register/stack flip arguments: ' + str(record))
            if api.startswith('sceGnmDingDong'):
                require(record.get('queue') == 3 and record.get('offset_dw') == 128,
                        'Wrong compute queue arguments')
            if api in ('VideoOutDriver::Flip', 'VideoOutDriver::SubmitFlipInternal'):
                require(record.get('buffer_index') == -1 and record.get('eop') is True and
                        record.get('flip_arg') == '0x1122334455667788', 'Wrong driver request fields')
            if api == 'sceKernelStat':
                require(record.get('path') == '/app0/test-file.bin' and not record.get('path_truncated'),
                        'Wrong missing-file path')
            if api in ('scePlayGoGetProgress', 'scePlayGoGetLocus'):
                require(record.get('chunks') == [0, 9] and record.get('chunk_count') == 2,
                        'Wrong PlayGo chunk IDs')
            if api == 'Rasterizer::Draw':
                require(record.get('indexed') is True, 'Wrong indexed draw argument')
            if api == 'Rasterizer::ResetBindings':
                require(record.get('is_compute') is False, 'Wrong draw/compute completion')
        elif api == 'Rasterizer::FilterDraw':
            require(record.get('draw_allowed') == (record['seq'] % 2 != 0), 'Wrong draw filter result')
        elif api.startswith(('VideoOutDriver::', 'sceGnmDingDong', 'Rasterizer::')):
            require(record.get('rc') is None, 'Void function was interpreted as an error code')
        elif api == 'scePlayGoGetInstallSpeed':
            require(record.get('install_speed') == 2, 'Wrong installation speed')
        elif api == 'scePlayGoGetLanguageMask':
            require(record.get('language_mask') == '0x4000000000000000', 'Wrong 64-bit language mask')
        elif api == 'scePlayGoGetToDoList':
            require(record.get('todo_entries') == 0, 'Wrong installation work count')
        elif api == 'scePlayGoGetProgress':
            require(record.get('installed_bytes') == record.get('total_bytes') == 1000,
                    'Wrong installation progress')
        elif api == 'scePlayGoGetLocus':
            require(record.get('loci') == [3, 3], 'Wrong installed chunk states')
        elif api == 'sceVideoOutGetFlipStatus':
            require(record.get('flip_count') == record['seq'] and record.get('current_buffer') == -1
                    and record.get('flip_pending_num') == 3 and record.get('gc_queue_num') == 2,
                    'Wrong flip status layout: ' + str(record))
        elif api == 'sceVideoOutGetVblankStatus':
            require(record.get('vblank_count') == 100 + record['seq'], 'Wrong vblank status layout')
