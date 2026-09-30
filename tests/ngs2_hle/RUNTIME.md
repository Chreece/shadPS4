# NGS2 runtime lifecycle integration

## Scope

The branch began at `fad0b3223c2117f53dc5a1119890fe91d55f1ecd`. The foundation's
registry now backs these public exports, implemented in `ngs2_impl.cpp` and
registered under the existing NIDs in `ngs2.cpp`:

- System: ResetOption, QueryBufferSize, Create, CreateWithAllocator, Destroy,
  GetInfo, GetUserData, SetUserData, SetGrainSamples, SetSampleRate, EnumHandles,
  EnumRackHandles.
- Rack: QueryBufferSize, Create, CreateWithAllocator, Destroy, GetInfo,
  GetUserData, SetUserData, GetVoiceHandle.
- Voice: GetOwner.

The previous implementation returned system handle 1 for every system, did not
publish rack/voice handles, and dropped allocator userData. These paths now create
independent objects, reject wrong-kind/stale handles and invalidate descendants.

## Ownership and synchronization

The host owns the registry and metadata. The queried guest context size is one
8-byte opaque token, with 8-byte alignment. This is the HLE storage contract, **not
an assertion about the proprietary library's work-buffer sizes**. Caller buffers
are retained and returned by value. No guest buffer is used as a host object pointer.

CreateWithAllocator initializes all context fields and passes allocator.userData
to allocHandler. Negative allocator results are propagated without assuming
ownership. After a successful callback, creation validates the returned buffer,
output address, parent identity and parent grain size again. Any failure invokes
the supplied free callback exactly once and publishes no handle. Application
user data set by SetUserData is distinct from the allocator context userData.

Destroy invalidates a rack and all its voices, or a system and all its racks and
voices, before invoking callbacks. Children are freed before the parent. All
cleanup callbacks are attempted even if one returns an error; the first negative
result is returned. A second Destroy returns the typed invalid-handle error.
A registered free callback remains responsible for its allocation even when
outBufferInfo is requested; returned metadata is not a second ownership transfer.
Without a free callback, storage remains caller-owned.

No guest callback runs with the internal runtime lock held. A callback may query,
create or destroy objects. Creation snapshots options before the callback and
checks state again afterward. Normal metadata and registry operations form one
transaction under the runtime mutex. The public SystemLock/RackLock pairs are
still stubs; multi-call guest locking semantics are not implemented here.

GuestAccessible uses a shared lock over the memory manager's mapping table and
checks every touched VMA, mapped state, CPU permissions, gaps and integer overflow.
Copies use memcpy to avoid alignment-dependent metadata loads. As with other HLE
APIs, guest mappings must remain valid while a call accesses them: this check does
not pin pages against concurrent guest unmapping. Callback code is checked for
execute permission before invocation; this is a range check, not instruction
validation.

## Compatibility boundaries

System defaults and supported sample rates come from the existing repository
implementation. The active grain may not exceed either the system maximum or any
live rack maximum. Invalid updates leave existing values unchanged.

Sampler, submixer and mastering rack IDs (0x1000, 0x2000, 0x3000) are supported.
Full known extensions retain decoder/channel-work/block counts and submixer/master
channel capacity, including eight channels. Sampler channel-work counts are a
resource capacity and are not limited to eight total works. Resource budgets stay
host limits. Effect/decoder capacities are metadata until those engines exist.

The following choices still need native/guest compatibility evidence:

- Null/base-only rack defaults: one voice, eight-channel capacity, 512 maximum
  grain samples. These are conservative HLE defaults, not verified SDK defaults.
  The public example below only establishes that null options and voice index 0
  are used, not the complete default limits.
- Enumeration: capacity zero returns the total; otherwise the return value is the
  number written, bounded by capacity. Info outputs fill known structures without
  overwriting a future tail; rack `type` currently follows its supported rack ID.
- Error precedence for simultaneous invalid inputs and free-callback failure
  propagation. Tests establish this implementation's contract, not equivalence
  with every native error path.
- Destruction with live children follows the existing foundation's cascading
  lifetime model. No claim is made about native callback ordering beyond that
  model. Caller-defined flags and unsupported future option fields are not
  implemented merely because known metadata can be retained.

The existing ABI structs are used, with compile-time checks on core sizes. No
firmware or proprietary implementation was imported. The public application
example used to corroborate allocator callback shape, null-option creation and
7.1 mastering setup is:
https://github.com/PhilNCL/PS4/blob/57379b0f4c73bd5f822cdc264444ccd705690779/GraphicsSkeleton/PS4AudioSystem.cpp
It does not validate the entire ABI.

## Next work / deployment gate

1. Connect waveform parser results to the public waveform/block APIs after
   validating their field semantics, zero-sample request behavior and ATRAC9
   preroll/loop rules. The internal encoded-window helper is not that ABI.
2. Decode with per-voice ATRAC9 state; add source buffering, resampling, playback
   advancement, loop exit and completion callbacks.
3. Implement voice controls and sampler/submixer/master routing, then render into
   the game's supplied eight-channel buffers. Preserve channel distinctions and
   check the speaker mapping end to end. NGS2 rendering should not open a second
   host audio device: the game submits the rendered buffers to AudioOut.
4. Complete a full emulator build and targeted in-game validation on an isolated
   deployment, retaining the user's working sparse-queue installation.

There is no ready game-audio deployment in this milestone. No silent-buffer
substitute, fabricated playback-completion flags, game-specific ID checks or
speaker-setting changes were introduced.
