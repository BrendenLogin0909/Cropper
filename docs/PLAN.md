# Plan and roadmap

_Last updated: 2026-11-16. Update this file in every PR that changes the plan or a
PR's state._

## Branches and the PR stack

| Ref | Head | Notes |
|---|---|---|
| `main` = `stable-v1` | `5c1c50e` | Revert point. Never touched (ADR-001). |
| `ai-restore-clean` | `6711041` | Base of the development line (fresh root commit; not on `main`'s history). |
| #1 `feat/settings-byo-key` → `ai-restore-clean` | `d94d04d` | Settings, BYO OpenAI key, 429-only retry. Draft. |
| #2 `feat/output-handling` → `feat/settings-byo-key` | `a1deb76` | Flat batch routing, allowed roots, overwrite/replace confirmations, folder-status, CI. Draft. |
| #3 `feat/first-run-guidance` → `feat/output-handling` | `06cf667` | Optional first-run guidance for restore/colourise. Draft. |
| #14 `fix/tk-dialog-thread` → `feat/first-run-guidance` | `da37e45` | Subprocess folder dialog. Draft; frozen for the Windows smoke test. |
| #15 `fix/editor-shortcuts` → `fix/tk-dialog-thread` | `43147ef` | P/W shortcuts, Ctrl+A alternates, loading/IME/focus guards. Draft; awaiting QA retest and review of `43147ef`. |
| `pairing-engine` | `6711041` | Same commit as `ai-restore-clean`; no pairing commits pushed yet. |

## In flight

1. **#14 and #15**: finish QA and review, then the owner tests them on Windows.
2. **Three stacked feature PRs**, each its own draft PR, checked by QA and code
   review, then tested by the owner on Windows before any merge:
   1. **Letter shortcuts** (`feat/letter-shortcuts`, off #15): C clear, Z undo,
      R rotate clockwise, A/D previous/next, shared undo/clear functions, Backspace
      routing, strip/tooltips/README. About 6 h. (ADR-006)
   2. **Prediction speed**: vectorised dedupe-first scorer, preview and result
      caches, background prefetch. About 6–8 h. Targets: cold p95 ≤ 250 ms,
      cached/prefetched p95 ≤ 50 ms. (ADR-007)
   3. **Bowed-edge correction**: curve-following warp with a gated trim, on by
      default with a Settings toggle. About 18 h. Target warp ≤ 0.4 s. (ADR-008)
3. **This docs PR** (`docs/sdlc` → `main`): docs only, needs the owner's OK.

## Merge sequence (each step needs the owner's explicit OK)

1. #1 into `ai-restore-clean`
2. #2 (retarget to `ai-restore-clean` after #1 merges)
3. #3
4. #14
5. #15
6. Then the three feature PRs in order (shortcuts, speed, bowed edge).

Retarget each PR's base only after the one below it merges. No force-push; resolve
conflicts with forward merge commits. Landing `ai-restore-clean` on `main` is a
separate decision (the histories are unrelated; see ADR-001).

## Later

- **Licence and CLA:** add AGPL-3.0 `LICENSE`, CLA text and a CLA check (ADR-010).
- **Hosted hardening:** close every item in issue #13, each with a test (ADR-013).
- **Pairing work:** front/back pairing suggestions across all picked folders, with
  no reliance on filename patterns or scan order (FR-02, FR-03).
- **Help panel:** an unobtrusive help surface reachable from anywhere (FR-30).
- **Accessibility audit** (NF-32) and **Windows performance re-measure** (NF-01–04).
- **Scrub existing public text** on the PR stack that refers to private material
  (a README section and the validation note in `CORNER_DETECTION.md`), per ADR-009.
- **Hosted paid version**, only after #13 and the licence are done.
- **Branch cleanup** of merged or stale branches, only with the owner's OK.
