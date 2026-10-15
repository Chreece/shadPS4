// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cassert>
#include "core/guest_cpu_selection.h"

int main() {
    using Core::SelectGuestCpu;
    for (unsigned host = 0; host < 128; ++host) {
        for (unsigned allowed = 1; allowed < 256; ++allowed) {
            const int selected = SelectGuestCpu(host, allowed);
            assert(selected >= 0 && selected < 8);
            assert((allowed & (1u << selected)) != 0);
        }
    }
    for (unsigned guest = 0; guest < 8; ++guest) {
        for (unsigned host = 0; host < 16; ++host) {
            assert(SelectGuestCpu(host, 1ull << guest) == static_cast<int>(guest));
        }
    }
    assert(SelectGuestCpu(0, 0) == -1);
    assert(SelectGuestCpu(7, 0x100) == -1);

    // CPU mode is determined by guest SDK and PSF flags, independently
    // of title or host processor. Exercise every flag combination.
    constexpr unsigned FW300 = 0x3000000;
    for (unsigned sdk : {0x2000000u, 0x3000000u, 0x8000000u}) {
        for (unsigned six = 0; six < 2; ++six) {
            for (unsigned seven = 0; seven < 2; ++seven) {
                const unsigned expected_mode = sdk < FW300 || (six && seven) ? 2
                                               : seven || !six ? 5 : 0;
                const unsigned mode = Core::GuestCpuModeFromPsf(
                    sdk, FW300, six != 0, seven != 0);
                assert(mode == expected_mode);
                const unsigned mask = Core::GuestCpuMaskFromMode(mode);
                assert(mask == (expected_mode == 5 ? 0x7fu : 0x3fu));
                for (unsigned slot = 0; slot < 128; ++slot) {
                    const int guest = SelectGuestCpu(slot, mask);
                    assert(guest >= 0 && guest < 8);
                    assert((mask & (1u << guest)) != 0);
                }
            }
        }
    }

    // CPU mode must never expose an unavailable application processor,
    // including when the host scheduler chooses virtual CPU 6 or 7.
    for (unsigned mode : {0u, 2u, 5u}) {
        const unsigned mask = Core::GuestCpuMaskFromMode(mode);
        assert(mask == (mode == 5 ? 0x7fu : 0x3fu));
        for (unsigned host = 0; host < 128; ++host) {
            const int selected = SelectGuestCpu(host, mask);
            assert(selected >= 0 && selected <= (mode == 5 ? 6 : 5));
            assert((mask & (1u << selected)) != 0);
        }
    }
    return 0;
}
