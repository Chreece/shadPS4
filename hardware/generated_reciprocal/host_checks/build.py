# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import sys, tempfile
import subprocess
root=Path(sys.argv[1]).resolve()
work=Path(tempfile.mkdtemp(prefix='page-manager-check-'))
(work/'main.cpp').write_bytes(Path(__file__).with_name('main.cpp').read_bytes())
stubs={
'common/assert.h':'''#pragma once
#include <cassert>
#include <cstdlib>
#define ASSERT(x) assert(x)
#define ASSERT_MSG(x, ...) assert(x)
#define UNREACHABLE_MSG(...) abort()
#define LOG_WARNING(...)
#define LOG_ERROR(...)
#define LOG_INFO(...)
''',
'common/debug.h':'#pragma once\n#define RENDERER_TRACE\n',
'common/error.h':'#pragma once\nnamespace Common { inline const char* GetLastErrorMsg(){ return "error"; } }\n',
'common/thread.h':'#pragma once\nnamespace Common { inline void SetCurrentThreadName(const char*){} }\n',
'common/signal_context.h':'''#pragma once
#include <ucontext.h>
namespace Common {
inline bool IsWriteError(void* p){ return ((ucontext_t*)p)->uc_mcontext.gregs[REG_ERR] & 2; }
inline bool IsExecuteError(void* p){ return ((ucontext_t*)p)->uc_mcontext.gregs[REG_ERR] & 16; }
}
''',
'core/emulator_settings.h':'#pragma once\ninline struct { bool IsUserfaultfdTracking(){return false;} } EmulatorSettings;\n',
'core/memory.h':'''#pragma once
#include <sys/mman.h>
#include "common/enum.h"
#include "common/assert.h"
namespace Core {
enum class MemoryPermission : u32 { None=0, Read=1, Write=2, ReadWrite=3, Execute=4, ReadWriteExecute=7 };
DECLARE_ENUM_FLAG_OPERATORS(MemoryPermission)
struct AddressSpace { void Protect(VAddr addr, u64 size, MemoryPermission p){
 int prot=(True(p&MemoryPermission::Read)?PROT_READ:0)|(True(p&MemoryPermission::Write)?PROT_WRITE:0)|(True(p&MemoryPermission::Execute)?PROT_EXEC:0);
 assert(mprotect((void*)addr,size,prot)==0);
}};
struct Memory { AddressSpace space; static Memory* Instance(){static Memory m;return &m;} AddressSpace& GetAddressSpace(){return space;} };
}
''',
'core/signals.h':'''#pragma once
#include "common/types.h"
namespace Core { struct Signals {
 using Handler=bool(*)(void*,void*); Handler handler=nullptr;
 static Signals* Instance(){static Signals s;return &s;}
 void RegisterAccessViolationHandler(Handler h,u32){handler=h;}
}; }
''',
'video_core/renderer_vulkan/vk_rasterizer.h':'''#pragma once
#include <thread>
#include "video_core/page_manager.h"
namespace Vulkan { struct Rasterizer {
 VideoCore::PageManager* manager=nullptr;
 VAddr start=0, end=0; unsigned reads=0, writes=0;
 bool IsMapped(VAddr a,u64 n){return a>=start && a+n<=end;}
 std::thread::id GetGpuCommandProcessorThread(){return {};}
 bool InvalidateMemory(VAddr a,u64 n,bool){++writes;manager->UpdatePageWatchers(a,n,VideoCore::PageOp::Untrack);return true;}
 bool ReadMemory(VAddr a,u64 n,bool){++reads;VideoCore::RegionBits bits{};bits.Fill(false);bits[0]=1;
 manager->UpdatePageWatchersForRegion(a & ~VAddr(4095),{0,0,0,0},bits,bits,VideoCore::PageOp::Untrack,VideoCore::PageOp::Untrack);return true;}
}; }
''',
}
for name,text in stubs.items():
 p=work/'include'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
src=(root/'src/video_core/page_manager.cpp').read_text()
# Exercise the signals backend with the real page-manager implementation, tables, and mutexes.
start=src.index('#ifdef __linux__\nstruct UffdImpl')
end=src.index('#endif // __linux__',start)+len('#endif // __linux__')
src=src[:start]+src[end:]
start=src.index('#ifdef __linux__',src.index('PageManager::PageManager'))
end=src.index('#endif',start)+len('#endif')
src=src[:start]+src[end:]
(work/'page_manager.cpp').write_text(src)
subprocess.run(['clang++-19','-std=c++23','-O2','-pthread','-I'+str(work/'include'),'-I'+str(root/'src'),str(work/'main.cpp'),str(work/'page_manager.cpp'),'-o',str(work/'check')],check=True)
subprocess.run([str(work/'check')],check=True,timeout=10)
