#!/usr/bin/env python3
"""Apply byte-identical native-tested masked stencil correction to exact GoT user source.

The embedded zlib/base64 data is the literal unified patch from the user's
2026-10-25 successful stencil A/B archive, hashed before application.
No game-ID conditions, settings changes, fake stencil values or draw skips.
"""
from pathlib import Path
import base64
import subprocess
import sys
import zlib

root = Path(sys.argv[1])
output = Path(sys.argv[2])
relative = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
source = root / relative
expected_preimage = "2309395360af9067fd3bee671e3b1553b3cc7aa0"
expected_patch = "a88cd6b405d67dfb0d4570621b5ccd82483daa6f"
expected_output = "42842a8b833fa7dcc880c9358f9efaaff24191fd"

def git_blob(path):
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()

actual = git_blob(source)
if actual != expected_preimage:
    raise SystemExit("Refusing unknown local rasterizer source: " + actual)

payload = "eNqtV1tvo0YUfvevOEkly8gYX5Jts3jdbLTNRpF2k8ppug/VFo1hsFEw0JnBjjdxf3vPcMfg4ETlIcHDzHe+c+XDcmwber25I4D0OTP7K8eivmH6jPYZ9SzKKDNWoftAvP7qwWCEC8qcH5RpZhDA7NVHWg5ueITRyeD9yft3J5p2Ojo7HZGzGQwHg59PT1u9Xu8NTFrdbvctbD5+hN7wbDhUh2fQjW7e/QK4uPIdC6bZXl2/Dywi6G80EIs7QT3Tce8ELnQUMH2PC3hqQfFidM41S+5GJp5gvqvNiPlgE5Ma1CMzl5b3y+scQk654QfGirgdbmo8NmTYBP/I4yoUVn8EhPOa5Wy3UjWhg82QDtoYt3rpYuwACYWPtG3J13YdU8AE/vreuVhaV0Go65/8ZUAY/Rx6Jtj4R02Opc+ToEyp/ZXwh7ZEUjAovXJQRMi86DgcTaAGWtcv3DXZcGi3X952Q1eU4a6ygcyOnYVDUC5kPCVScT2O8rjVTQ/1+/DHgsI8xAOwIBw4RXOYYnQ0MtyXUEA8rIvL379cfLrsr5mDjxEVa8wzaQkLwRFJhVko4M+o+sD33A0Q08Sq4PijcBICdEbWhgbXNohFGSqxH5mOTS4xxBxJrih4PkQFRixMmc824GNgXBIEjjeHmSO4WsKa+WKR+OijUSIczCKYyG4m+QSMcuoJagF9JKZAvrNNRBU5zByPWiWwjL8Ka0csfHSVuLJj0DQ6ARYja/AZIAuIWxFkzVKu5TAcu8gx8Z+l60T4S8f8EJ6Mfo3tG5kFvAt8JvjTYDtuPhx6mSuy1ZpgCg2QlkeeGuyC9vdOY7EXwnzAVddVSZpfBzTzfTceG+iZixWkQA/9Pxlh75VwYgfkg6wjJrWNMt53Lu4YaGgjeTk2dI6KpOD5OYus6VLClF16hfGwh8i2aiNtjMnLs+T5uWqr6Wg0X15iWev4dl/sEnOGbNydCMqlvTGP2r3uVP5gf77iQVCc550s938nDijQLpNrw15oGfIq6AQGtXHKiRR6qb62c1plMgo8o8UgeVKIRRv+Le0bV3El2eLsT2Ha9RT2jBvNpsJcGMSyOkM1njNLusQxa/gMxxluc8kjtRT4AKPTsDYO8vpye2Vc33y+7UyjKWjEL4SmTj/Ox0zM6lKS/ErZnFp61CaTJ/2nxy36ltwcNyEWo5acycOaLGRBiH8fN9FMo6wmMVZLOVQLBtQcuy5l273Nlh1rngmNo/9/SKlM57eL6c31zdWrMlpJ6H2Z7JuSelBO03bFN7MhRcFhqX1NZncHg/JyqlA9XABfIJaVyqN8TKAc8XwBUWQYapw59UIUH6hGCn5U8BJu3fSdI/3UYEoFcTzEclCIJMINpd2SeKgc+Bg8OegrWDZ5QL0FPES1xrkduunrK4qJ1DQ8DCQ/juT8JRVso5VBdl4TeRwKwlvWaycV5FLyFuV3p+YToqBOjFiER6cVRb7jSkq4Iz8AMtSmb5GqkD6UySwlEn1wKBXJv79Vsm6IQ8roP6GDAc3lyXrhILNpnM3bIN9xXCv6a3sjCX+kRksZXDtupJuSBB0rhbRs81tr4xFUlYYUmlS7o2K3g3mev/M4GTvSKP3kqugs9WAvIp2XpPM8uqvaKK2mJoo9eIAn5QKuKOHYUxUOr0ukp6hNqPEH7AHfywfL4nNoLtjD0fSDHG7CS/KnKIVRsC8l3+RYlR8WvHNUUs2VEitIIh0G92rrFRUFFexSDe1AH8I7EdAx8zLR+B1RMpAIt/8A9UWH9A=="
diff = zlib.decompress(base64.b64decode(payload))
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(diff)
if git_blob(output) != expected_patch:
    raise SystemExit("Historical stencil source-patch integrity mismatch")
subprocess.run(["git", "-C", str(root), "apply", "--check", str(output)], check=True)
subprocess.run(["git", "-C", str(root), "apply", str(output)], check=True)
result = git_blob(source)
if result != expected_output:
    raise SystemExit("Stencil source output differs from native-proven A/B: " + result)
print("STENCIL_NATIVE_TESTED_SOURCE=" + result, flush=True)
print("STENCIL_PREIMAGE=" + actual, flush=True)
print("GUEST_STENCIL_MASKED_REFERENCE_COMPOSITION=YES", flush=True)
