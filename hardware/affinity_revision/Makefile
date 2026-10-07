# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
CXX := clang++
LD := ld.lld
SDK := $(OO_PS4_TOOLCHAIN)

.PHONY: all
all: eboot.bin eboot-seven.bin

main.o: main.cpp
	$(CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@

affinity.elf: main.o
	$(LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib main.o $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@

eboot.bin: affinity.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=affinity.oelf --eboot $@ --paid 0x3800000000000011

eboot-seven.bin: affinity.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=affinity-seven.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000
