#!/usr/bin/env python3
import hashlib, pathlib, struct, subprocess, sys, zlib
root = pathlib.Path(__file__).parent
def generate(name, stage):
 subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'llvm-mc', '-triple=amdgcn-amd-amdhsa',
                '-mcpu=bonaire', '-filetype=obj', str(root/(name+'.s')), '-o', str(root/(name+'.o'))], check=True)
 b = (root/(name+'.o')).read_bytes()
 assert b[:6] == b'\x7fELF\x02\x01'
 off = struct.unpack_from('<Q', b, 40)[0]
 size, count, names = struct.unpack_from('<HHH', b, 58)
 sections = [struct.unpack_from('<IIQQQQIIQQ', b, off+i*size) for i in range(count)]
 n = sections[names]; strings = b[n[4]:n[4]+n[5]]
 text = next(s for s in sections if strings[s[0]:].split(b'\0', 1)[0] == b'.text')
 code = bytearray(b[text[4]:text[4]+text[5]])
 while len(code) % 8: code += struct.pack('<I', 0xbf800000)
 struct.pack_into('<I', code, 4, len(code)//8-1)
 identity = hashlib.sha256(code).digest()[:8]
 meta = b'OrbShdr'+bytes([1])+struct.pack('<II', (len(code)<<8)|(stage<<2), 0)+identity+struct.pack('<II', zlib.crc32(code), 0)
 blob = code+meta
 words = struct.unpack('<'+'I'*(len(blob)//4), blob)
 (root/(name+'.h')).write_text(f'#include <stdint.h>\nstatic const uint32_t {name}_shader[] = {{\n'+
     ',\n'.join('    '+', '.join(f'0x{w:08x}u' for w in words[i:i+8]) for i in range(0,len(words),8))+'\n};\n')
 print(f'code_bytes={len(code)} binary_bytes={len(blob)} sha256={hashlib.sha256(blob).hexdigest()}')

for name, stage in [('vs',1),('ps',0)]:
 generate(name, stage)
 (root/(name+'.o')).unlink()
