# Decision log

ADR style. Each entry: **Context**, **Decision**, **Consequences**, **Status**
(Proposed, Accepted, Approved-not-built, Superseded). Dates are commit dates (UTC)
where a commit exists, otherwise the date the decision was recorded. Add new
entries at the end; never rewrite an accepted entry. Supersede it with a new one.

---

## ADR-001: `stable-v1` is the revert point; all work goes through draft PRs

- **Date:** recorded 2026-11-16 (`stable-v1` = `main` = `5c1c50e`, committed 2026-10-02).
- **Context:** The app is in daily use. Large changes (Settings, AI, output
  handling, security hardening) were developed by several contributors and agents
  at once. The owner needs a known-good version to fall back to at any time.
- **Decision:**
  - `stable-v1` points at `main` `5c1c50e` and is **never touched**.
  - `main`, `stable-v1` and `ai-restore-clean` are not committed to directly.
  - All work is on feature branches as **draft PRs**, reviewed and QA-tested, then
    tested by the owner.
  - **No force-push and no history rewrites.** Fixes are new forward commits.
  - **Nothing merges to `main` (or any base) without the owner's explicit OK.**
- **Consequences:** The PR stack grows deep (#1 → #2 → #3 → #14 → #15) and must
  merge in order. The development line on `ai-restore-clean` starts from a fresh
  root commit (`6711041`, which removed hard-coded personal paths before
  publishing) and does **not** share history with `main`; how it lands on `main`
  needs its own decision when the stack is approved.
- **Status:** Accepted.

## ADR-002: Allowed folders come only from the native folder dialog

- **Date:** 2026-10-30 (`6a50494`, PR #2).
- **Context:** The server can read and write anywhere the user can. A typed or
  forged path from a web page must not be able to reach arbitrary folders.
- **Decision:** Keep an in-memory `ALLOWED_ROOTS` per session and tab. Register a
  folder only from a real pick in the OS dialog. Refuse drive roots and parents of
  an open folder. Confine every read and write to those roots. Browsing and listing
  stay possible but grant nothing.
- **Consequences:** After a restart every folder must be picked again (see ADR-003).
  Headless tests need a stubbed dialog (`e2e_server.py`).
- **Status:** Accepted (in #2, pending merge).

## ADR-003: `folder-status` and the "pick this folder again" empty state

- **Date:** 2026-11-03 (`a1deb76`, PR #2).
- **Context:** After a restart the UI remembered the last folder, but the server no
  longer allowed it, so users saw grey tiles and "outside the allowed folder" errors.
- **Decision:** Add read-only `GET /api/folder-status?path=` returning
  `{registered}`. It registers nothing, returns no listing, and reports escapes like
  a missing path. When not registered, the UI shows zero tiles, the remembered path
  and a **Browse…** button.
- **Consequences:** Calm restart behaviour with no information leak. One extra
  request on startup.
- **Status:** Accepted (in #2, pending merge).

## ADR-004: Run the folder dialog in a short-lived subprocess

- **Date:** 2026-11-09 (`cfe679a`, `c8ce13f`, `da37e45`, PR #14).
- **Context:** Tk/Tcl 8.6 crashes the whole process with `Tcl_AsyncDelete` when a
  Tk root is created, destroyed or garbage-collected off the main thread. Every
  request in `ThreadingHTTPServer` is on a worker thread, so Cropper crashed during
  folder picks.
- **Decision:** Never import Tk in the server. Launch `folder_dialog.py` per dialog
  with `-I -X utf8`, `stdin=DEVNULL`, no console window. The child writes one
  strict UTF-8 JSON envelope (`{"path"}` or `{"cancelled": true}`). The server
  enforces one dialog at a time, a 300 s timeout with kill, a 32 KiB output cap, and
  absolute, ≤ 4096-char, control-character-free paths.
- **Consequences:** No Tk crashes, and non-ASCII paths work regardless of the
  console code page. Small process-start cost per dialog. Known Low findings from
  security review remain (a grandchild holding pipes can delay EOF; the in-flight
  output cap does not fire on Windows; `..` inside an absolute path is not
  rejected; duplicate JSON keys accepted). None are reachable with the real dialog.
- **Status:** Accepted (in #14, security review PASS, pending merge).

## ADR-005: Single-key shortcuts instead of Ctrl combinations

- **Date:** 2026-11-16 (`9a37300`, PR #15). Shift-for-zoom since 2026-10-30 (`968ccf0`).
- **Context:** Ctrl+A (predict) and Ctrl+Shift+A (whole image) did not work
  reliably in Chrome, which takes Ctrl+A for select-all and reserves other Ctrl
  combinations (as reported by the owner on Windows). Zoom had already moved from
  hold-Ctrl to hold-Shift so Ctrl shortcuts could pass through.
- **Decision:** Primary crop-view actions are single letters with no modifiers
  (P predict/cycle, W whole image). Shift is hold-to-zoom. Ctrl combinations stay
  as hidden alternates (Ctrl+A, Ctrl+Shift+A, Ctrl+Z, Ctrl+Q). Letters are matched
  by `event.key.toLowerCase()`, ignore key repeat, and fire only with no
  Ctrl/Meta/Alt/Shift. `preventDefault` runs only when a shortcut fires.
- **Consequences:** Letters need strong guards (inputs, dialogs, busy/loading, IME,
  crop view only). Shift+letter can never be a shortcut. Ctrl+P and Ctrl+W keep
  their browser meaning (print, close tab).
- **Status:** Accepted (in #15, pending merge).

## ADR-006: The letter shortcut set

- **Date:** approved 2026-11-16.
- **Context:** The owner works with the right hand on the mouse. Memorable letters
  under the left hand are fastest.
- **Decision:** P predict/cycle, W whole image, **C clear** (Ctrl+Q alias),
  **Z undo** (Ctrl+Z alias), Enter save, **R rotate clockwise**, **A/D previous/next**
  (same as the arrows). **No S**: a single letter that writes files is too easy to
  hit. No E (Enhance is a separate tab). Z, Ctrl+Z and the Undo button share one
  undo function; C, Ctrl+Q and Backspace share `clearCropPoints()`; both cancel an
  in-flight predict. The Reset tooltip becomes "Reset points and rotation".
- **Consequences:** A/D drop unplaced points without asking, exactly like the
  arrow keys today. AZERTY users get a moved cluster (accepted for now). The strip,
  tooltips, README and docs must list the set.
- **Status:** Approved-not-built (shortcuts PR, first of three stacked PRs).

## ADR-007: Prediction speed package, no quick-guess-then-refine

- **Date:** approved 2026-11-16.
- **Context:** A first **P** took p95 ≈ 390–490 ms. About 75 % of detection time is
  pure-Python scoring of near-duplicate quads, and the scorer holds the GIL.
  Detection already runs on a 960 px copy; going smaller changes the chosen outline
  on about 1 in 3 scans.
- **Decision:** Build, in order: a vectorised, dedupe-first scorer with identical
  output (golden test); a server decoded-preview cache filled at image open, with
  JPEG draft decode on a miss; a server result LRU keyed by path, mtime, size and
  target, plus a client `Map` cache; a single foreground-priority background worker
  that prefetches the current then next image. **Do not** show a quick low-res guess
  and refine it: corners would visibly jump on about a third of scans and could
  overwrite a corner being dragged.
- **Consequences:** Targets: cold p95 ≤ 250 ms, cached/prefetched p95 ≤ 50 ms,
  live preview p95 ≤ 400 ms. The server becomes stateful (caches capped about
  100 MB, invalidated on mtime/size). Draft decode is not bit-identical (1 of 22
  outlines changed in testing); drop it if exact parity is required. Re-measure on
  Windows.
- **Status:** Approved-not-built (speed PR, second).

## ADR-008: Bowed-edge curve following, not a bright-edge trim

- **Date:** approved 2026-11-16.
- **Context:** Slightly bowed prints leave thin white or bright streaks down the
  sides of the straightened result even when the corners are right. The streak is
  widest mid-side and near zero at the corners.
- **Decision:** In `warp()` (shared by preview and save), detect the print edge
  along each side, fit a quadratic sag with the corners fixed, and remap with a
  Coons patch, applied only when the fit is confident. Back it with a gated trim
  capped at 1.5 % (0.3 % on corrected sides) that never fires on bright content.
  **On by default, with a Settings toggle to turn it off.** A plain "trim bright,
  uniform strips" was rejected: on synthetic tests it cut up to 5.9 % of real
  content (skies, white walls, white borders) and also trims the corners where there
  is no streak. A straight-line snap was rejected because it cannot follow a curve.
- **Consequences:** On synthetic fixtures streaks drop to ≤ 1 px with ≤ 0.07 %
  extra loss (worst 0.21 %); prints with no bow are unchanged. When the edge is
  indistinguishable from the background it abstains. Warp time rises from about
  0.02 s to a target ≤ 0.4 s. About 18 h with tests.
- **Status:** Approved-not-built (bowed-edge PR, third).

## ADR-009: Private-scan data policy

- **Date:** recorded 2026-11-16 (in force since development began).
- **Context:** Development and tuning use the owner's real family prints. The repo
  is public.
- **Decision:** Real photos and scans are **never** committed, uploaded, attached
  to issues/PRs, described in docs, or sent to OpenAI for testing. Their filenames
  and content are not mentioned anywhere public. Tests, fixtures, docs and examples
  are **synthetic**. Real-scan checks run only locally and report aggregate numbers.
  No secrets, keys or `.env` values are committed (`.env` is git-ignored;
  `.env.example` has empty values). Automated tests make no paid API calls.
- **Consequences:** Accuracy work relies on synthetic ground truth plus local,
  aggregate-only checks. Any existing public text that names private material
  should be scrubbed in a reviewed PR.
- **Status:** Accepted.

## ADR-010: Licence AGPL-3.0 plus a CLA (plan)

- **Date:** recorded 2026-11-16.
- **Context:** The goal is open-source readiness while keeping the option of a
  hosted paid version.
- **Decision (planned):** License the code AGPL-3.0, so hosted forks must share
  changes, and require a Contributor Licence Agreement so the owner can also offer
  the hosted version. Not done yet: no `LICENSE` file, CLA text or CLA check exists.
- **Consequences:** Until it lands, the repo is public but unlicensed (all rights
  reserved), and outside contributions should not be accepted.
- **Status:** Proposed.

## ADR-011: Non-text controls drop focus after use

- **Date:** 2026-11-16 (`43147ef`, PR #15).
- **Context:** After changing Output proportions or ticking Auto-next, that control
  kept focus. The input/select guard then silently disabled every shortcut, and the
  arrow keys changed the proportions instead of the image.
- **Decision:** Blur `#crop-aspect`, `#auto-next` and the crop toolbar buttons
  (undo, reset, predict, whole image, rotate, save, prev, next) after a change or
  click. Text inputs (`#crop-aspect-custom`, `#folder-path`) keep focus.
- **Consequences:** Shortcuts keep working after mouse use; keyboard users can
  still tab to controls. New non-text controls in the crop view must follow the rule.
- **Status:** Accepted (in #15, pending merge).

## ADR-012: `docs/` is the source of truth and is updated in every PR

- **Date:** 2026-11-16.
- **Context:** Work is spread across many PRs, reviewers, QA rounds and agents.
  Knowledge held in chat or memory gets lost.
- **Decision:** Keep the brief, requirements, architecture, decisions, plan, testing
  and smoke checklist in `docs/`. README and AGENTS.md point there first. Every PR
  updates the docs it affects.
- **Consequences:** Small overhead per PR; reviewers check docs as part of review.
- **Status:** Proposed (this PR; needs the owner's OK).

## ADR-013: Hosted-build hardening is tracked separately (issue #13)

- **Date:** 2026-10-30 (issue #13 opened).
- **Context:** The final security pass found items that do not affect the local,
  single-user app but must be fixed before any hosted build.
- **Decision:** Track them in issue #13 (path checks on every input, output-folder
  confinement, TOCTOU re-checks, NFC path keys, `Sec-Fetch-Site` on `/api/session`,
  image size limits and format checks, HMAC idempotency keys), each with a test.
- **Consequences:** No hosted build ships until #13 is closed.
- **Status:** Accepted.
