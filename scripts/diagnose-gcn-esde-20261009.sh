#!/usr/bin/env bash
set -uo pipefail
umask 077

build="$HOME/shadps4-esde-verified-builds/20261009-174801"
tests="$build/tests"
source_dir="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
if pgrep -x ctest >/dev/null || pgrep -f '[/]shadps4_gcn_test' >/dev/null; then
  echo 'STOP: previous ctest is still running. Press Ctrl+C ONCE in that SSH terminal, then retry.'
  exit 2
fi
if [[ ! -d "$tests" ]]; then
  echo "Missing test build: $tests. No changes made."
  exit 2
fi
stamp="$(date +%Y%m%d-%H%M%S)"
out="$HOME/shadps4-gcn-diagnostic-$stamp.tar.gz"
work="$(mktemp -d "$HOME/.shadps4-gcn-diagnostic-XXXXXX")" || exit 1
finish() {
  rc=$?
  trap - EXIT
  tar -czf "$out" -C "$work" . || echo 'Could not create the archive.'
  rm -rf -- "$work"
  echo
  echo "DIAGNOSTIC_EXIT=$rc"
  echo "REPORT=$out"
  echo 'SSH_SESSION=UNCHANGED'
}
trap finish EXIT
{
  echo "DATE=$(date -Is)"
  echo "OS=$(uname -a)"
  echo "TEST_BUILD=$tests"
  echo "SOURCE=$source_dir"
  echo "USER_GROUPS=$(id -nG)"
  for name in DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR XDG_SESSION_TYPE VK_ICD_FILENAMES VK_DRIVER_FILES VK_LAYER_PATH VK_INSTANCE_LAYERS MESA_VK_DEVICE_SELECT AMD_VULKAN_ICD DRI_PRIME RADV_PERFTEST LD_LIBRARY_PATH; do
    printf '%s=%s\n' "$name" "${!name:-unset}"
  done
  echo '--- DRM nodes ---'
  ls -l /dev/dri 2>&1 || true
  echo '--- Vulkan ICD files ---'
  ls -l /usr/share/vulkan/icd.d /etc/vulkan/icd.d 2>&1 || true
  echo '--- graphics devices ---'
  if command -v lspci >/dev/null; then lspci -nnk | grep -Ei -A4 'VGA|3D controller|Display controller' || true; fi
  echo '--- GPU/CTEST processes ---'
  pgrep -af 'shadps4|amdgpu|ctest' || true
  echo '--- candidate ---'
  if [[ -d "$source_dir/.git" ]]; then
    git -C "$source_dir" rev-parse HEAD
    git -C "$source_dir" rev-parse refs/remotes/origin/main
    git -C "$source_dir" status --short
    echo '--- changes affecting the GCN runner and shader translator ---'
    git -C "$source_dir" diff --name-status refs/remotes/origin/main HEAD -- tests/gcn src/shader_recompiler
  fi
  echo '--- binary linked libraries ---'
  gcn_binary="$(find "$tests" -maxdepth 3 -type f -name shadps4_gcn_test -print -quit)"
  if [[ -n "$gcn_binary" ]]; then ldd "$gcn_binary" 2>&1; fi
} > "$work/environment.txt" 2>&1

if command -v vulkaninfo >/dev/null; then
  timeout 20s vulkaninfo --summary > "$work/vulkan-default.txt" 2>&1
  echo "VULKANINFO_STATUS=$?" > "$work/vulkaninfo-status.txt"
else
  printf 'vulkaninfo not installed. No packages were changed.\n' > "$work/vulkan-default.txt"
fi

# Fast CPU-only control proves shader translation/test discovery separately.
ctest --test-dir "$tests" -R '^GcnTest[.]fragment_front_face_uses_float_sign_bits$' \
  --output-on-failure --timeout 20 > "$work/translate-control.txt" 2>&1
printf 'TRANSLATOR_CONTROL_STATUS=%s\n' "$?" > "$work/control-status.txt"

# Reproduce exactly ONE GPU execution failure; never launch the full 574-test suite.
VK_LOADER_DEBUG=error,warn timeout 25s ctest --test-dir "$tests" -R '^GcnTest[.]add_f32$' \
  --output-on-failure --timeout 20 > "$work/gcn-default.txt" 2>&1
default_result=$?
printf 'DEFAULT_GCN_STATUS=%s\n' "$default_result" > "$work/gcn-status.txt"

# Compare against the installed RADV driver only when default execution failed.
radv=''
for candidate in /usr/share/vulkan/icd.d/radeon_icd.x86_64.json /etc/vulkan/icd.d/radeon_icd.x86_64.json; do
  if [[ -f "$candidate" ]]; then radv="$candidate"; break; fi
done
if [[ "$default_result" -ne 0 && -n "$radv" ]]; then
  VK_DRIVER_FILES="$radv" VK_ICD_FILENAMES="$radv" VK_LOADER_DEBUG=error,warn \
    timeout 25s ctest --test-dir "$tests" -R '^GcnTest[.]add_f32$' \
    --output-on-failure --timeout 20 > "$work/gcn-radv.txt" 2>&1
  printf 'RADV_ICD=%s\nRADV_GCN_STATUS=%s\n' "$radv" "$?" >> "$work/gcn-status.txt"
fi

if [[ -f "$tests/Testing/Temporary/LastTest.log" ]]; then
  tail -n 200 "$tests/Testing/Temporary/LastTest.log" > "$work/last-ctest.log"
fi
if command -v journalctl >/dev/null; then
  timeout 6s journalctl --no-pager -k --since '-10 minutes' -n 150 > "$work/kernel-recent.txt" 2>&1 || true
fi
cat "$work/gcn-status.txt"
echo 'DIAGNOSTICS_CAPTURED=YES'
