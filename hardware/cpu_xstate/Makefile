# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
ORBIS_CXX ?= clang++
ORBIS_LD ?= ld.lld
SDK := $(OO_PS4_TOOLCHAIN)

native-xstate: main.cpp cases.S
	$(CXX) -std=c++17 -O2 -Wall -Wextra -Werror -mavx main.cpp cases.S -o $@

main.o: main.cpp
	$(ORBIS_CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -O2 -Wall -Wextra -Werror -DXSTATE_ORBIS -mavx -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@

cases.o: cases.S
	$(ORBIS_CXX) --target=x86_64-pc-freebsd12-elf -fPIC -c $< -o $@

cpu_xstate.elf: main.o cases.o
	$(ORBIS_LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib main.o cases.o $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@

eboot.bin: cpu_xstate.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=cpu_xstate.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000
