# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
CXX := clang++
LD := ld.lld
SDK := $(OO_PS4_TOOLCHAIN)

.PHONY: all
all: signal_requeue.bin thread_recycle.bin

%.o: %.cpp
	$(CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include $< -o $@

%.elf: %.o
	$(LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib $^ $(SDK)/lib/crt1.o -lc -lkernel -lSceSystemService -o $@

%.bin: %.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=$*.oelf --eboot $@ --paid 0x3800000000000011 --sdkver 0x3000000
