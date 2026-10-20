# Scalar condition correction from the 083636 capture

The uploaded `got-frame-history-evidence-20261020-083636.tar.gz` ran source
`11f48f35b474df772a6724500dcb478e8403dd5f` for 300 seconds. The input helper sent
N at 151 seconds. The later screenshots include gameplay, with sparse ground and
grass but much of the world absent. Menu screenshots still lack the sword and
contain a pink region. This is not a completed graphics fix.

The capture contains six frame checkpoints, 2,832 completed draw-query rows and
330 pass images, with no reported GPUVM fault or texture-pool exhaustion. The
new depth-only readback shows terrain at the bottom of the main menu depth
prepass, without a sword. A later normal attachment contains a sword silhouette,
but that alone does not establish that a current draw generated it. Several
indirect paths remain empty; there is no basis for overriding their counts.

## Confirmed compiler error

The compiler used `InverseBallot(result)` for SCC after 64-bit scalar logical
operations. That extracts the executing lane's bit. AMD's scalar instruction
semantics instead set SCC to whether the entire 64-bit result is nonzero.
For example, a result containing only bit 63 must set SCC for every lane,
including lane zero.

The four affected translator helpers now compare the whole result with zero:
`S_AND_B64`, `S_OR_B64`, `S_NOT_B64` and `S_SAVEEXEC_B64`. Together these cover
the supported AND/ANDN2/NAND, OR/ORN2/NOR/XOR/XNOR, NOT and SAVEEXEC variants.
SAVEEXEC continues to update each lane's EXEC bit separately. Shader cache
version 58 prevents reuse of binaries compiled with the old flag semantics.

The captured grass fragment shader `836253ca` and visibility compute shader
`5065ed7b` both contain an affected sequence: remove processed lanes from a
remaining mask, AND that mask with itself to set SCC, restore EXEC, then branch
on SCC to continue the loop. The visibility shader's relevant AND and branch
are decoded instructions 952 and 954; the grass shader's are 50 and 52. Testing
a lane's individual bit at this point can disagree with the required
whole-mask loop condition. This establishes that captured shaders use the
incorrectly translated semantics; it does not establish how much missing
geometry the correction will restore.

Reference: [AMD Sea Islands ISA, scalar logical and SAVEEXEC instructions](https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/sea-islands-instruction-set-architecture_0.pdf).

## Validation

- Full local Release build and link passed; the executable's CLI starts.
- An external compiler/IR regression check evaluates eight mask pairs across
  eleven instructions and all 64 lanes: 5,632 results. The old compiler produces
  1,493 mismatches; the corrected compiler produces zero. Store addresses are
  also checked. This is an IR evaluation, not a GPU run.
- The captured visibility shader's frontend/SSA output now uses `INotEqual64`
  against zero for its remaining-mask loop branch. Its unrelated signed
  comparison branch remains a signed comparison.
- The captured grass shader recompiles through AMD interpolation, KHR
  barycentric, flat fallback and manual interpolation paths. All four SPIR-V
  outputs pass Vulkan 1.3 validation. The AMD path retains three valid Input
  interpolation operands and no invalid operands.
- The assembled regression probe's SPIR-V validates. The corresponding
  [OpenOrbis hardware probe](homebrew/scalar-condition/README.md) is supplied,
  with its hardware-unrun status explicit.
- Shell syntax, embedded Python syntax, the 96-file source overlay and the
  host's actual source preflight checks pass. The overlay now includes the
  corrected scalar translator.

No native Vulkan device or PS4 was available here. The sword, wind, menu colour
and world must be judged from the next native capture. The isolated runner
retains the 150-second N press and 300-second capture; the production executable
remains unchanged. This change introduces no game/hash-specific rendering
condition, forced draw count, clipping/depth override or GDS experiment.
