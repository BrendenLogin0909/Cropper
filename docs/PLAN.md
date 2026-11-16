# Plan

_Last updated: 2026-11-16. This branch does not carry the full `docs/` tree
from #16; the follow-up below is recorded here so it is not lost. It is a
**separate future PR**, not #20 / `feat/bowed-edges`._

## Later (not this PR)

**Corner inward refinement.** Move a locked edge inward only on a positive
print-edge signal (a step, line, or shadow). An even-width bed strip is a
corner-placement problem, not a bowed edge, so it does not belong in
`warp()`. The must-not-cut fixture is a picture with a uniform border whose
colour matches the bed.
