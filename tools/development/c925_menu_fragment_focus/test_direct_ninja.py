from __future__ import annotations
import pathlib,subprocess,tempfile,hashlib
import direct_ninja as n

with tempfile.TemporaryDirectory() as td:
 p=pathlib.Path(td)
 (p/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20)\nproject(test CXX)\nadd_executable(shadps4 src/vk_rasterizer.cpp src/stub.cpp)\n')
 (p/'src').mkdir()
 (p/'src/vk_rasterizer.cpp').write_text('int main(){return 0;}\n')
 (p/'src/stub.cpp').write_text('int foo(){return 7;}\n')
 subprocess.run(['cmake','-S',str(p),'-B',str(p/'build'),'-G','Ninja'],stdout=subprocess.DEVNULL,check=True)
 subprocess.run(['ninja','-C',str(p/'build'),'shadps4'],stdout=subprocess.DEVNULL,check=True)
 build=p/'build';source=p/'src/vk_rasterizer.cpp';obj=build/'CMakeFiles/shadps4.dir/src/vk_rasterizer.cpp.o';exe=build/'shadps4'
 before={x:hashlib.sha256(y.read_bytes()).hexdigest() for x,y in [('source',source),('obj',obj),('exe',exe)]}
 # The dry run reports a generator re-check when the CMake source is touched,
 # yet the exact compile/link commands are still obtainable without mutation.
 (p/'CMakeLists.txt').touch()
 pair=n.parse(build,source,obj,exe,p)
 assert len(pair)==2
 assert 'vk_rasterizer.cpp' in n.command_string(pair[0])
 assert ' -o shadps4 ' in ' '+n.command_string(pair[1])+' '
 source.write_text('int main(){return 3;}\n')
 subprocess.run(['bash','-c',n.command_string(pair[0])],check=True)
 subprocess.run(['bash','-c',n.command_string(pair[1])],check=True)
 assert subprocess.run([str(exe)],check=False).returncode==3
 assert not (build/'CMakeFiles/shadps4.dir/src/vk_rasterizer.cpp.o.d').exists()
 assert before['exe']!=hashlib.sha256(exe.read_bytes()).hexdigest()
 assert 'Re-running CMake' in subprocess.check_output(['ninja','-C',str(build),'-n','shadps4'],text=True)
 print('DIRECT_NINJA_CMAKE_REGEN_BYPASS=PASS')
