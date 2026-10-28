// CPU-only fault-query protocol tests. Driver responses are simulated.
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <limits>
#include "video_core/renderer_vulkan/vk_device_fault_report.h"

using namespace Vulkan::DeviceFault;
static void Check(bool value, const char* why) {
    if (!value) { std::fprintf(stderr, "FAULT_CAPTURE_TEST_FAIL=%s\n", why); std::exit(2); }
}
static unsigned calls;
static int mode;
static VkResult VKAPI_CALL Fake(VkDevice, VkDeviceFaultCountsEXT* c, VkDeviceFaultInfoEXT* i) {
    ++calls;
    Check(c->sType == VK_STRUCTURE_TYPE_DEVICE_FAULT_COUNTS_EXT && c->pNext == nullptr,
          "counts initialized");
    if (!i) {
        if (mode == 1) return VK_ERROR_UNKNOWN;
        c->addressInfoCount = mode == 3 ? std::numeric_limits<std::uint32_t>::max() : (mode == 2 ? 0 : 2);
        c->vendorInfoCount = mode == 3 ? 1000000 : (mode == 2 ? 0 : 1);
        c->vendorBinarySize = mode == 6 ? 0xffffffffULL : 0;
        return VK_SUCCESS;
    }
    Check(i->sType == VK_STRUCTURE_TYPE_DEVICE_FAULT_INFO_EXT && i->pNext == nullptr,
          "info initialized");
    Check(c->vendorBinarySize == 0 && i->pVendorBinaryData == nullptr, "binary disabled");
    Check(c->addressInfoCount <= AddressCapacity && c->vendorInfoCount <= VendorCapacity,
          "bounded driver counts");
    Check((c->addressInfoCount != 0) == (i->pAddressInfos != nullptr), "address pointer/count agreement");
    Check((c->vendorInfoCount != 0) == (i->pVendorInfos != nullptr), "vendor pointer/count agreement");
    if (mode == 4) return VK_ERROR_UNKNOWN;
    if (mode == 5) {
        c->addressInfoCount = 0; c->vendorInfoCount = 0;
        return VK_SUCCESS;
    }
    std::memset(i->description, 'D', sizeof(i->description));
    for (std::uint32_t n = 0; n < c->addressInfoCount; ++n) {
        i->pAddressInfos[n] = {};
        i->pAddressInfos[n].addressType = VK_DEVICE_FAULT_ADDRESS_TYPE_READ_INVALID_EXT;
        i->pAddressInfos[n].reportedAddress = 0x1234000ULL + n * 4096;
        i->pAddressInfos[n].addressPrecision = 4096;
    }
    for (std::uint32_t n = 0; n < c->vendorInfoCount; ++n) {
        auto& v = i->pVendorInfos[n]; v = {};
        std::memset(v.description, 'V', sizeof(v.description));
        v.vendorFaultCode = 17; v.vendorFaultData = 33;
    }
    return mode == 3 ? VK_INCOMPLETE : VK_SUCCESS;
}
int main() {
    const auto device = reinterpret_cast<VkDevice>(std::uintptr_t{0x1000});
    unsigned cases = 0;
    for (mode = 0; mode <= 6; ++mode) {
        calls = 0;
        const auto r = Capture([&](VkDeviceFaultCountsEXT* c, VkDeviceFaultInfoEXT* i) { return Fake(device, c, i); });
        if (mode == 1 || mode == 6) {
            Check(calls == 1 && !r.queried_details && r.address_count == 0, "count failure or unexpected binary stops safely");
            Check(mode != 6 || r.truncated, "unexpected binary explicitly flagged");
        } else {
            Check(calls == 2 && r.queried_details, "exact two calls");
            if (mode == 2 || mode == 4 || mode == 5) {
                Check(r.address_count == 0 && r.vendor_count == 0, "empty or failed details ignored");
            } else {
                Check(r.address_count == (mode == 3 ? AddressCapacity : 2), "address count");
                Check(r.vendor_count == (mode == 3 ? VendorCapacity : 1), "vendor count");
                Check(r.addresses[0].reportedAddress == 0x1234000 &&
                      r.addresses[0].addressPrecision == 4096, "fault address retained");
                Check(BoundedText(r.description.data(), r.description.size()).size() == VK_MAX_DESCRIPTION_SIZE,
                      "unterminated description bounded");
                Check(r.truncated == (mode == 3), "truncation explicit");
            }
        }
        ++cases;
    }
    mode = 0; calls = 0;
    Check(!Register(VK_NULL_HANDLE, Fake) && !Register(device, nullptr), "invalid registration refused"); ++cases;
    Check(Register(device, Fake), "register feature-enabled device");
    Check(!OnResult(device, VK_SUCCESS) && !OnResult(device, VK_ERROR_OUT_OF_HOST_MEMORY) && calls == 0,
          "query only after DEVICE_LOST"); ++cases;
    Check(OnResult(device, VK_ERROR_DEVICE_LOST).has_value() && calls == 2, "first loss captured"); ++cases;
    Check(!OnResult(device, VK_ERROR_DEVICE_LOST) && calls == 2, "repeated loss not queried"); ++cases;
    Forget(device);
    Check(!OnResult(device, VK_ERROR_DEVICE_LOST), "forgotten device not queried"); ++cases;
    Check(Register(device, Fake) && OnResult(device, VK_ERROR_DEVICE_LOST).has_value() && calls == 4,
          "reused handle registered afresh"); ++cases;
    Forget(device);
    for (std::uintptr_t n = 1; n <= devices.size(); ++n) {
        Check(Register(reinterpret_cast<VkDevice>(n), Fake), "multiple devices independent");
    }
    Check(!Register(reinterpret_cast<VkDevice>(9), Fake), "registry capacity enforced"); ++cases;
    for (std::uintptr_t n = 1; n <= devices.size(); ++n) Forget(reinterpret_cast<VkDevice>(n));
    Check(BoundedText("ok\0extra", 8) == "ok", "bounded terminated text"); ++cases;
    std::printf("DEVICE_FAULT_CPU_PROTOCOL=PASS cases=%u NO_GPU_WORK\n", cases);
}
