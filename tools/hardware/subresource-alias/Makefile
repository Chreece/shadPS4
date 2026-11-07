TOOLCHAIN := $(OO_PS4_TOOLCHAIN)
CC := clang
LD := ld.lld
.PHONY: all
all: eboot.bin
build/main.o: wave_probe/main.c wave_probe/shaders.h wave_probe/registers.h
	mkdir -p build
	$(CC) --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -std=c11 -O2 -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include -c $< -o $@
build/probe.elf: build/main.o
	$(LD) -m elf_x86_64 -pie --script $(TOOLCHAIN)/link.x --eh-frame-hdr -L$(TOOLCHAIN)/lib $< $(TOOLCHAIN)/lib/crt1.o -lc -lkernel -lSceGnmDriver -o $@
eboot.bin: build/probe.elf
	$(TOOLCHAIN)/bin/linux/create-fself -in=$< -out=build/probe.oelf --eboot $@ --paid 0x3800000000000011
