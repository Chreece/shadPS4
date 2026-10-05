OO_PS4_TOOLCHAIN ?= /opt/OpenOrbis/PS4Toolchain
CXX := clang++
LD := ld.lld
SDK := $(OO_PS4_TOOLCHAIN)

.PHONY: all
all: eboot.bin

main.o: main.cpp
	$(CXX) --target=x86_64-pc-freebsd12-elf -std=c++17 -fPIC -funwind-tables -c -isysroot $(SDK) -isystem $(SDK)/include -isystem $(SDK)/include/c++/v1 $< -o $@

cpu-affinity.elf: main.o
	$(LD) -m elf_x86_64 -pie --script $(SDK)/link.x --eh-frame-hdr -L$(SDK)/lib main.o $(SDK)/lib/crt1.o -lc -lkernel -lc++ -o $@

eboot.bin: cpu-affinity.elf
	$(SDK)/bin/linux/create-fself -in=$< -out=cpu-affinity.oelf --eboot $@ --paid 0x3800000000000011
