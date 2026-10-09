// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

extern "C" void probe_auto_flags16(uint64_t *);
extern "C" void probe_auto_flags64(uint64_t *);
extern "C" void probe_auto_redirect(uint64_t *);
extern "C" const unsigned char probe_auto_redirect_fault[], probe_auto_redirect_query[];
extern "C" int scePthreadCreate(void **, const void *, void *(*)(void *), void *, const char *);
extern "C" int scePthreadJoin(void *, void **);
extern "C" void scePthreadExit(void *);

static bool UntouchedTail(const unsigned char *data) {
  const auto expected = _mm_set1_epi8(static_cast<char>(0xa5));
  for (size_t offset = 0; offset < 81920 - 832; offset += 16) {
    const auto actual = _mm_loadu_si128(reinterpret_cast<const __m128i *>(data + offset));
    if (_mm_movemask_epi8(_mm_cmpeq_epi8(actual, expected)) != 0xffff)
      return false;
  }
  return true;
}

struct AutoTraceWorker {
  Runner entry;
  unsigned checked;
  bool explicit_exit;
};
static unsigned auto_ready, auto_start;
static unsigned auto_redirects;

static bool CheckAutomaticRedirect(int signal, void *context) {
  auto &machine = *static_cast<OrbisContextPrefix *>(context);
  if (signal != 4 || machine.rip != reinterpret_cast<uintptr_t>(probe_auto_redirect_fault))
    return false;
  machine.rip = reinterpret_cast<uintptr_t>(probe_auto_redirect_query);
  machine.registers[4] = 0;
  ++auto_redirects;
  return true;
}

static void *RunAutoTraceWorker(void *argument) {
  auto &worker = *static_cast<AutoTraceWorker *>(argument);
  __atomic_fetch_add(&auto_ready, 1, __ATOMIC_RELEASE);
  while (!__atomic_load_n(&auto_start, __ATOMIC_ACQUIRE))
    asm volatile("pause");
  for (unsigned i = 0; i < 128; ++i) {
    Observation value{};
    worker.entry(nullptr, 0, &value);
    if (value.rax != 7 || value.rdx || ((value.flags_before | value.flags_after) & 0x100))
      return reinterpret_cast<void *>(1);
    ++worker.checked;
  }
  if (worker.explicit_exit) {
    scePthreadExit(nullptr);
    _Exit(95);
  }
  return nullptr;
}

static bool RunAutomaticTraceChecks(unsigned char *generated) {
  FILE *progress = fopen("/data/xstate-auto-progress.txt", "w");
  if (!progress) return false;
  setvbuf(progress, nullptr, _IONBF, 0);
  fprintf(progress, "AUTO_STAGE=redirect\n");
  uint64_t redirected[2]{};
  probe_auto_redirect(redirected);
  if (redirected[0] != 7 || redirected[1] || auto_redirects != 1)
    return false;
  fprintf(progress, "AUTO_STAGE=flags\n");
  unsigned flags_checked = 0;
  void (*probes[])(uint64_t *){probe_auto_flags16, probe_auto_flags64};
  for (auto probe : probes) {
    for (unsigned i = 0; i < 32; ++i) {
      uint64_t value[4]{};
      probe(value);
      if ((value[0] & 0x100) || value[1] != 7 || value[2] ||
          (value[3] & 0xdd5) != 0xcd5)
        return false;
      ++flags_checked;
    }
  }
  fprintf(progress, "AUTO_STAGE=threads\n");
  AutoTraceWorker workers[2]{{reinterpret_cast<Runner>(generated + 6 * 1024), 0, false},
                           {reinterpret_cast<Runner>(generated + 6 * 1024), 0, true}};
  void *threads[2]{};
  for (unsigned i = 0; i < 2; ++i) {
    if (scePthreadCreate(&threads[i], nullptr, RunAutoTraceWorker, &workers[i], "xstate-auto"))
      _Exit(94);
  }
  while (__atomic_load_n(&auto_ready, __ATOMIC_ACQUIRE) != 2)
    asm volatile("pause");
  __atomic_store_n(&auto_start, 1, __ATOMIC_RELEASE);
  bool ok = true;
  for (void *thread : threads) {
    void *result = reinterpret_cast<void *>(1);
    ok &= scePthreadJoin(thread, &result) == 0 && result == nullptr;
  }
  FILE *extra = fopen("/data/xstate-auto-extra.txt", "w");
  if (!extra)
    return false;
  const unsigned checked = workers[0].checked + workers[1].checked;
  fprintf(extra, "AUTO_EXTRA flags=%u threads=2 queries=%u redirects=%u explicit_exit=1 errors=%u\n", flags_checked,
          checked, auto_redirects, unsigned(!ok));
  fclose(extra);
  fclose(progress);
  return ok && checked == 256 && flags_checked == 64;
}
