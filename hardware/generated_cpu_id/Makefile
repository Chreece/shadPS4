# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
CXX := clang++
LD := ld.lld
SDK := $(OO_PS4_TOOLCHAIN)

.PHONY: all
all: eboot-0.bin eboot-1.bin eboot-2.bin eboot-3.bin

state.o: state.S
	$(CXX) --target=x86_64-pc-freebsd12-elf -c $< -o $@

main-%.o: main.cpp
	$(CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -fPIC -funwind-tables -DTEST_OPERATION=$* -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@

generated-%.elf: main-%.o state.o
	$(LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib $^ $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@

eboot-%.bin: generated-%.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=generated-$*.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000
