TOOLCHAIN := $(OO_PS4_TOOLCHAIN)
CC := clang
LD := ld.lld
CFLAGS := --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -O2 -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include
all: eboot.bin
main.o: main.c vertex.h copy.h
	$(CC) $(CFLAGS) -c $< -o $@
probe.elf: main.o
	$(LD) -m elf_x86_64 -pie --script $(TOOLCHAIN)/link.x --eh-frame-hdr -L$(TOOLCHAIN)/lib $(TOOLCHAIN)/lib/crt1.o main.o -lc -lkernel -lSceGnmDriver -o $@
eboot.bin: probe.elf
	$(TOOLCHAIN)/bin/linux/create-fself -in=$< -out=probe.oelf --eboot $@ --paid 0x3800000000000011
shader:
	python3 make_shader.py llvm-mc vertex
	python3 make_shader.py llvm-mc copy
.PHONY: all shader
