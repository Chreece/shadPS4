# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
ORBIS_CXX ?= clang++
ORBIS_LD ?= ld.lld
HOST_CXX ?= g++
SDK := $(OO_PS4_TOOLCHAIN)
all: eboot.bin
main.o: main.cpp
	$(ORBIS_CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -O2 -mavx -Wall -Wextra -Werror -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@
cases.o: cases.S
	$(ORBIS_CXX) --target=x86_64-pc-freebsd12-elf -c $< -o $@
sse4a.elf: main.o cases.o
	$(ORBIS_LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib main.o cases.o $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@
eboot.bin: sse4a.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=sse4a.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000

native-probe: native_probe.cpp cases.S
	$(HOST_CXX) -std=c++17 -O2 -Wall -Wextra -Werror $^ -Wl,-z,noexecstack -o $@

console.o: native_probe.cpp
	$(ORBIS_CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -O2 -Wall -Wextra -Werror -DSSE4A_ORBIS -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@
console.elf: console.o cases.o
	$(ORBIS_LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib console.o cases.o $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@
console.bin: console.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=console.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000
