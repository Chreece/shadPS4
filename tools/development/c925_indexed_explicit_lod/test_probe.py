import unittest
from probe_indexed_lod import analyze,new_mode,parse

def sample(flags=8):
 text='''[a] %1 = GetAttributeU32 WorkgroupId, #0 (uses: 1)
[a] %2 = GetUserData SGPR0 (uses: 1)
[a] %3 = GetUserData SGPR1 (uses: 1)
[a] %4 = GetUserData SGPR2 (uses: 1)
[a] %5 = GetUserData SGPR3 (uses: 1)
[a] %6 = CompositeConstructU32x4 %2, %3, %4, %5 (uses: 8)
[a] %7 = IMul32 %1, #384 (uses: 1)
[a] %8 = IAdd32 %7, #144 (uses: 1)
[a] %9 = ShiftRightLogical32 %8, #2 (uses: 8)
'''
 for i in range(8):text+=f'[a] %{10+i}= IAdd32 %9, #{i} (uses: 1)\n[a] %{20+i}= ReadConstBuffer (sharp_source=true, flatbuf_off_dw=0x0) %6, %{10+i} (uses: 1)\n'
 text+='[a] %30 = CompositeConstructU32x4 %20, %21, %22, %23 (uses: 1)\n[a] %31 = CompositeConstructU32x4 %24, %25, %26, %27 (uses: 1)\n[a] %32 = ImageHandle %30, %31 (uses: 1)\n'
 return text+f'[a] %33 = ImageSampleRaw (flags={hex(flags)}) %32, %6, %6, %6, %6, #0 (uses: 1)\n'
class Probe(unittest.TestCase):
 def test_lz(self):self.assertEqual(analyze(sample())[0][0]['record_stride'],384)
 def test_l(self):self.assertEqual(analyze(sample(16))[0][0]['mode'],'L')
 def test_array(self):self.assertFalse(analyze(sample(0x208))[0])
 def test_implicit(self):self.assertFalse(analyze(sample(0))[0])
 def test_gradient(self):self.assertFalse(analyze(sample(0x100))[0])
 def test_unaligned(self):self.assertFalse(analyze(sample().replace('#384','#385'))[0])
 def test_host_flat(self):self.assertFalse(analyze(sample().replace('flatbuf_off_dw=0x0','flatbuf_off_dw=0x14'))[0])
 def test_partial(self):self.assertFalse(analyze(sample().replace('%24, %25, %26, %27','%24, %25, %26, %26'))[0])
 def test_no_input(self):self.assertEqual(analyze(''),([],{}))
 def test_selector(self):self.assertEqual(analyze(sample())[0][0]['gpu_selector'],'%1')
if __name__=='__main__':unittest.main()
