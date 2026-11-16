# Requirements

_Last updated: 2026-11-16._ Status is one of **Done** (on the development line at
`43147ef` or earlier, still pending merge to `main` unless noted), **In progress
(#PR)**, or **Planned**. "Approved" means the owner has approved the work but it is
not built yet. Update the status in the PR that changes it.

## Functional

### Folders and files

| ID | Requirement | Status |
|---|---|---|
| FR-01 | Open any folder of images (JPEG, PNG, WebP, TIFF, BMP) at any location and with any name, including non-ASCII and space-containing paths. | Done (#2, #14 UTF-8 dialog envelope) |
| FR-02 | **No reliance on filename patterns or scan order.** Detection, pairing suggestions, crop and save must work whatever the camera or scanner named the files and in whatever order they were captured. Names are only used to build output names. | Done for crop, detection and visual pair suggestions; Planned: audit of every feature as part of the pairing work |
| FR-03 | **No feature may be blocked by which folder a file is in.** Front/back pairing and every other feature work across any folders the user has picked, subject only to the allowed-roots safety model (FR-04). | In progress: folder location no longer blocks crop, enhance, restore, Finish, merge or AI (#2, `968ccf0`); manual cross-folder pairing (#2, `f6a670b`); pair *suggestions* still scan one folder at a time; Planned: pairing work |
| FR-04 | Only folders the user picked in the native folder dialog are readable or writable. Typing or browsing a path lists folders but does not grant access. | Done (#2, `6a50494`) |
| FR-05 | After a restart, a remembered folder that is not yet registered shows a calm "pick this folder again" empty state, not errors or grey tiles. | Done (#2, folder-status) |
| FR-06 | Originals are never silently overwritten. Conflicts stop the save unless the user chose Overwrite or Auto-suffix; Replace original requires backups. | Done (#2) |

### Crop editor

| ID | Requirement | Status |
|---|---|---|
| FR-10 | Place four corners in any order; drag to adjust; straightened live preview. | Done (stable-v1) |
| FR-11 | Predict corners locally (OpenCV) and cycle through alternative outlines; a point placed first restricts candidates to the print containing it. | Done |
| FR-12 | Select the whole image as the crop. | Done |
| FR-13 | Output proportions presets and custom ratio; rotation in 90° steps. | Done |
| FR-14 | **Bowed-edge correction:** when a print is slightly curved, follow each side's curve so no thin bright streaks remain along the edges. On by default, with a Settings toggle to turn it off. Preview and save must match. | Approved, Planned (third stacked PR) |
| FR-15 | Front + Back merge into one composite, with back rotation and a chosen name source. | Done |

### Keyboard shortcuts (crop view)

All single-letter shortcuts are matched with `event.key.toLowerCase()`, fire only
with no Ctrl/Meta/Alt/Shift held, and ignore key repeat.

| ID | Key | Action | Status |
|---|---|---|---|
| KS-01 | **P** | Predict corners; press again to cycle cached outlines (wraps). Ctrl+A is a hidden alternate. | Done (#15) |
| KS-02 | **W** | Select the whole image (also invalidates an in-flight predict). Ctrl+Shift+A is a hidden alternate. | Done (#15) |
| KS-03 | **C** | Clear all points (and cancel an in-flight predict). **Ctrl+Q** stays as an alias. | Approved, Planned (shortcuts PR); Ctrl+Q Done |
| KS-04 | **Z** | Undo the last point (shared with Ctrl+Z and the Undo button; cancels an in-flight predict). **Ctrl+Z** stays as an alias. | Approved, Planned (shortcuts PR); Ctrl+Z Done |
| KS-05 | **Enter** | Save (and move to the next image when Auto-next is on). | Done |
| KS-06 | **R** | Rotate clockwise. | Approved, Planned (shortcuts PR) |
| KS-07 | **A / D** | Previous / next image (same as ←/→). | Approved, Planned (shortcuts PR) |
| KS-08 | **No S** | S is deliberately unbound: a single letter that writes files is too easy to hit by mistake. | Decided |
| KS-09 | **Shift** (hold) | Precision zoom ×3. | Done |
| KS-10 | ←/→, Backspace | Previous/next image; Backspace resets points (to be routed through the clear function). | Done; Backspace routing Planned |
| KS-11 | **Guards:** shortcuts are ignored when focus is in a text input, textarea, select or contenteditable; when a dialog or the folder picker is open; while busy or while an image is loading; during IME composition (`isComposing` / keyCode 229); and outside the crop view. Non-text controls (Output proportions, Auto-next, toolbar buttons) drop focus after use so shortcuts keep working. | Done (#15, `43147ef`) |

### Help

| ID | Requirement | Status |
|---|---|---|
| FR-30 | **Unobtrusive, always-available in-app help.** Help is visible where it is needed without blocking work: the shortcut strip under the image, button tooltips naming their shortcut, and inline notes. No modal tours on every launch. | Done for strip and tooltips (#15); Planned: a help panel reachable from anywhere |
| FR-31 | The shortcut strip and tooltips always list the current primary keys; docs and README match. | Done (#15); update with every shortcut change |
| FR-32 | Optional first-run guidance for AI restore and colourise. | In progress (#3) |

### Optional AI features

| ID | Requirement | Status |
|---|---|---|
| FR-40 | Bring-your-own OpenAI key in Settings, stored in Windows Credential Manager; `OPENAI_API_KEY` overrides it. UI shows only the last four characters. | In progress (#1) |
| FR-41 | AI restoration and colourisation run only on an explicit, clearly paid user action; results are staged for review (accept, reject, modify) and survive a restart. | In progress (#1–#3) |
| FR-42 | A 429 rate-limit is retried once with backoff; out-of-credit errors are not retried; failures are listed per photo. | In progress (#1) |

## Non-functional

### Performance

| ID | Requirement | Status |
|---|---|---|
| NF-01 | Corner prediction, cold press on a typical 2–3k px scan: **p95 ≤ 250 ms** end to end (p50 ≤ 180 ms). Measured today: p95 ≈ 390–490 ms. | Approved, Planned (speed PR) |
| NF-02 | Prefetched or cached prediction: **p95 ≤ 50 ms**, via background prefetch of the current and next image. | Approved, Planned (speed PR) |
| NF-03 | Live crop preview shown after a press: p95 ≤ 400 ms. | Planned (speed PR) |
| NF-04 | Warp including bowed-edge correction: **≤ 0.4 s** at 4500 px. Today's plain warp is 0.01–0.02 s. | Approved, Planned (bowed-edge PR) |
| NF-05 | The UI never blocks while a request is in flight; stale results never land on a different image. | Done (#15) |

### Privacy and data

| ID | Requirement | Status |
|---|---|---|
| NF-10 | All processing is local except explicitly invoked AI features, which use the user's own key. No telemetry, no external UI assets. | Done |
| NF-11 | Server binds to 127.0.0.1; requests need the per-process token and a matching localhost origin. | Done (#1) |
| NF-12 | GPS EXIF is stripped from outputs unless the user opts in. | Done |
| NF-13 | Request bodies, notes and settings are never logged; keys are never logged or shown in full. | Done (#1) |
| NF-14 | The repo never contains real user photos or scans, their filenames, or secrets; tests use synthetic fixtures. | Done (policy, ADR-009) |

### Security

| ID | Requirement | Status |
|---|---|---|
| NF-20 | Reads and writes are confined to dialog-picked allowed roots; drive roots and parents of an open folder are refused. | Done (#2) |
| NF-21 | The native folder dialog never runs Tk inside the server process; its output is a strict UTF-8 JSON envelope with size and time limits. | Done (#14) |
| NF-22 | Overwrite and replace need short-lived, tab-scoped confirmation tokens. | Done (#2) |
| NF-23 | Hosted-build hardening (issue #13): path checks for every input, output-folder confinement, TOCTOU re-checks, NFC path keys, `Sec-Fetch-Site`, image-size limits, HMAC idempotency keys. | Planned (#13, required before any hosted build) |

### Accessibility and usability

| ID | Requirement | Status |
|---|---|---|
| NF-30 | Every crop action is reachable by keyboard and by a labelled button with a tooltip. | Done for current actions; Planned for C/Z/R/A/D labels |
| NF-31 | Shortcuts favour memorable letters under the left hand while the right hand uses the mouse. | Decided (ADR-005, ADR-006) |
| NF-32 | Visible keyboard focus, accessible names on icon buttons, sufficient contrast, and no colour-only signals. | Planned (audit) |
| NF-33 | Works with non-QWERTY layouts and IME users (letters by `event.key`; IME composition ignored). AZERTY moves the left-hand cluster; acceptable for now. | Partly done (#15) |
| NF-34 | Every error is a plain-language message with no tracebacks. | Done |

### Platform and quality

| ID | Requirement | Status |
|---|---|---|
| NF-40 | Runs on Windows with the pinned `requirements.txt`; port 8765 by default, `--port` or `CROPPER_PORT` to change it. | Done |
| NF-41 | CI runs the Python and Node suites on windows-latest and ubuntu-latest for every push and PR. | Done (#2 onwards, `b61b32f`) |
| NF-42 | Every PR updates `docs/`. | Done (this PR sets it up) |
