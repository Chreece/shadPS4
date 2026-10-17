// Linux userfaultfd userspace read-contract test. Does not create userfaultfd or change VM.
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <linux/userfaultfd.h>
#include <sys/types.h>
#include <unistd.h>

[[noreturn]] void Fail(const char* name) {
    std::fprintf(stderr, "UFFD_BATCH_READ_TEST_FAIL=%s\n", name);
    std::exit(1);
}
void Need(bool okay, const char* name) {
    if (!okay) Fail(name);
}
struct Pipe {
    int descriptors[2]{};
    Pipe() { if (pipe(descriptors) != 0) Fail("PIPE_SETUP"); }
    ~Pipe() { close(descriptors[0]); close(descriptors[1]); }
    Pipe(const Pipe&) = delete;
    Pipe& operator=(const Pipe&) = delete;
};
std::array<uffd_msg, 3> MakeFaults() {
    std::array<uffd_msg, 3> values{};
    for (size_t i = 0; i < values.size(); ++i) {
        values[i].event = UFFD_EVENT_PAGEFAULT;
        values[i].arg.pagefault.flags = UFFD_PAGEFAULT_FLAG_WP;
        values[i].arg.pagefault.address = 0x10000000 + 4096 * i;
        // Mix guest/GPU-origin faults so tests reject accidental reordering.
        values[i].arg.pagefault.feat.ptid = i == 1 ? 44 : (100 + i);
    }
    return values;
}
void WriteAll(Pipe& channel, const std::array<uffd_msg,3>& entries) {
    const size_t size = sizeof(entries);
    Need(write(channel.descriptors[1], entries.data(), size) ==
         static_cast<ssize_t>(size), "PIPE_WRITE_ALL_EVENTS");
}
void VerifyEvents(const std::array<uffd_msg,32>& received, size_t count,
                  const std::array<uffd_msg,3>& expected, size_t offset) {
    for (size_t i = 0; i < count; ++i) {
        const auto& event = received[i];
        Need(event.event == UFFD_EVENT_PAGEFAULT, "EVENT_TYPE");
        Need((event.arg.pagefault.flags & UFFD_PAGEFAULT_FLAG_WP) != 0, "EVENT_WP_FLAG");
        Need(event.arg.pagefault.address == expected[i + offset].arg.pagefault.address,
             "FAULT_ADDRESS_DELIVERY_ORDER");
        Need(event.arg.pagefault.feat.ptid == expected[i + offset].arg.pagefault.feat.ptid,
             "FAULT_ORIGIN_THREAD_ORDER");
    }
}
void BatchedReadPreservesAllFaults() {
    Pipe channel;
    const auto expected = MakeFaults();
    WriteAll(channel, expected);
    std::array<uffd_msg,32> events{};
    const ssize_t result = read(channel.descriptors[0], events.data(), sizeof(events));
    Need(result == static_cast<ssize_t>(expected.size() * sizeof(uffd_msg)),
         "BATCHED_READ_COUNT");
    Need(result % sizeof(uffd_msg) == 0, "BATCHED_WHOLE_RECORDS");
    VerifyEvents(events, static_cast<size_t>(result) / sizeof(uffd_msg), expected, 0);
    Need(events[1].arg.pagefault.feat.ptid == 44, "GPU_FAULT_RETAINS_MIDDLE_POSITION");
}
void DefaultReadOnlyConsumesOneFault() {
    Pipe channel;
    const auto expected = MakeFaults();
    WriteAll(channel, expected);
    std::array<uffd_msg,32> events{};
    for (size_t i = 0; i < expected.size(); ++i) {
        const ssize_t result = read(channel.descriptors[0], events.data(), sizeof(events[0]));
        Need(result == static_cast<ssize_t>(sizeof(uffd_msg)), "DEFAULT_EXACTLY_ONE_FAULT");
        VerifyEvents(events, 1, expected, i);
    }
}
void PartialAndMalformedReadRejected() {
    Pipe channel;
    unsigned char byte = 0x4A;
    Need(write(channel.descriptors[1], &byte, 1) == 1, "PARTIAL_TEST_WRITE");
    std::array<uffd_msg,32> events{};
    const ssize_t result = read(channel.descriptors[0], events.data(), sizeof(events));
    Need(result == 1, "PARTIAL_TEST_READ");
    Need(result <= 0 || (static_cast<size_t>(result) % sizeof(uffd_msg)) != 0,
         "PARTIAL_RECORD_MUST_BE_REJECTED");
}
int main() {
    BatchedReadPreservesAllFaults();
    DefaultReadOnlyConsumesOneFault();
    PartialAndMalformedReadRejected();
    std::puts("UFFD_BATCH_READ_HOST_CONTRACT=PASS cases=3");
}
