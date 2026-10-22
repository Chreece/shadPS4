# Shared SRT dependency restoration — 22 October 2026

## Exact scope

This is the first shared-code restoration from the previously rendering reference,
not a complete indexed-image/sampler-table port or a recovered-world claim.

The immutable emulator parent is the user's built `9d70b53b323f54a38bb2f77eed4435154679adfe`,
tree `e55a6ab5bf8460a14429744310b6da7facf0c7f0`. A package distribution commit is NOT
that emulator source tree. The host creates a separate full-tree candidate commit
with exactly four incremental paths: the SRT pass, two implementation include files,
and both shader-cache versions in the existing serialization translation unit.
All preceding source and dependency corrections stay byte-identical outside this delta.

The reference at `94ed29b7c5767718ead87164558d6ec7523bcc9a` retries local and
cross-root dependencies. The retained clean candidate visits each root once.
A descriptor offset can depend on another read that is encountered later; rejecting
it on the first traversal loses a resolvable dependency. The restoration retries
only deferred reads, shares only actually emitted slots, and stops at no progress.
It does not infer values for Phi nodes, GPU attributes, unknown instructions, or
cyclic expressions. The existing CPU-uniformity proof is unchanged.

The dependency table's dynamic-value comparator also returned true when comparing
an instruction with itself. The comparator now obeys strict self-ordering and uses
std::less for unrelated block pointers. Flatbuffer slots cannot wrap into reserved
slot zero. Partially emitted machine code is rolled back before retrying a read.
These are compiler scheduling decisions, not guest loop bounds or GPU stalls.

## Not included

No dynamic image/sampler descriptor-array implementation is claimed here. The old
bounded-pointer expansion sampled every candidate and selected afterward; it is
not copied wholesale. Correct GPU-indexed resource selection, descriptor type and
lifetime handling, and CPU/GPU ownership still require separate implementation.
No readback mode, driver flag, title ID, shader identity, null-descriptor substitution,
rendering skip, save path or active emulator installation is changed by this build.
No claim is made that these dependencies are the only cause of the regression.

## Tests and limits

`verify_srt_dependencies.py` executes the production scheduler and VisitPointer
with synthetic IR. Locally the emitter backend is an explicit instruction
interpreter; this is not a full shader compiler or native Xbyak test. It checks
reverse local chains, cross-root reads, aliases, a dependent pointer subtree,
partial-emission rollback, GPU-dependent-source rejection, cycles, and slot capacity.
The old VisitPointer fails the same reverse-dependency test. The real Boost flat_map
comparator fixture rejects the old self-ordering and accepts the new comparator.

The scheduler covers 4,096 randomized-order acyclic graphs (133,120 vertices) per
compiler. The old single sweep leaves unresolved nodes in 3,982 of those graphs.
GCC and Clang runs pass; the Clang run uses address and undefined-behavior sanitizers.

On the host, the verifier extracts the exact checked-out pointer functions and old
negative-control function, verifies the complete transformed source, and additionally
compiles/executes the new walker against the pinned Xbyak headers. That mandatory
native test has NOT run in the assistant container. The full emulator build and
real GPU/world result have NOT run here either.

## Operation

Install all manifest files from the pinned Git package, then run:

    python3 -B run_srt_dependencies.py --build-only

The driver reuses the existing checksum-verified atomic/native build and dependency
restoration workflow, verifies the latest build evidence, and starts directly from
the latest complete candidate instead of reconstructing old source patches.
It retains the exact Sirit gitlink already built on the server. It requires the
native dependency tests, real Sirit/SPIR-V checks, production compilation and full
link. Original checkout, dependency checkout and build executable are restored.
Any unexpected local edits cause refusal instead of a forced reset.

The command never launches a game. It does not touch either the persistent
reference-progress profile or the normal profile. The build report is printed as
`UPLOAD_THIS=` on success or failure. No `test_package.py` is assumed or invoked.
