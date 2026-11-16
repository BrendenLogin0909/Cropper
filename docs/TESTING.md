# Testing

_Last updated: 2026-11-16. Counts are at `43147ef`._

## Layers

| Layer | What | How to run |
|---|---|---|
| Python unit/integration | `test_*.py` in the repo root (unittest): routing, path safety, allowed roots, folder dialog envelope, confirmations, settings, OpenAI error mapping (mocked), crop detection, enhance/restore. 260 tests (7 skipped on Ubuntu, 1 on Windows). | `python -m unittest discover -p "test_*.py" -v` |
| Node UI tests | `test_*.cjs` in the repo root (`node:test`; extracts functions from `app.js` and runs them in a `vm` sandbox with a stub DOM): shortcuts and guards, crop corners, folder load/restore, overwrite UI, AI queue/guidance. 102 tests. | `node --test test_*.cjs` |
| Playwright QA | End-to-end browser checks run by QA against a local server with the folder dialog stubbed (`e2e_server.py`, `CROPPER_TEST_DIALOG_FOLDER`) and no API key. Kept outside the repo for now. | QA-run; results are linked in PR comments |
| Security review | Read-only exploit scripts against each security-relevant PR (dialog envelope, path escapes, tokens). | Security reviewer; findings go to the PR or issue #13 |
| Performance | Stage timing and end-to-end press-to-paint timing on synthetic images. | Performance reviewer; re-measure on Windows |
| Manual Windows smoke | [WINDOWS_SMOKE.md](WINDOWS_SMOKE.md) (the full list; the short root-level `WINDOWS_SMOKE.md` on the PR stack is a subset) | The owner, before merging a release-affecting PR |

## CI

`.github/workflows/tests.yml` (from #2) runs on every push and pull request, on
**windows-latest** and **ubuntu-latest**, with Python 3.12 and Node 20: install
`requirements.txt`, run the Python suite, then the Node suite. A PR is not ready
unless both jobs are green on its head commit.

## Rules

- **Fail-old discipline for bug fixes.** Every bug-fix test must fail on the
  parent commit and pass on the fix. Record in the PR how many new tests fail on the
  old head, and which ones are expected to pass there (for example guard tests
  where the old code did nothing).
- **Golden-output tests for refactors** that must not change results (for example
  the faster corner scorer must return the same candidates in the same order).
- **Synthetic fixtures only.** Generate images in code with deterministic seeds
  (tilted prints on textured or scanner-white backgrounds, bowed edges, bright
  skies, white borders, dark-on-dark). Never commit real photos or scans, their
  filenames or anything derived from them (ADR-009). Real-scan checks are local
  only and reported as aggregates.
- **No paid calls in tests.** OpenAI is mocked; CI has no key. QA runs with
  `OPENAI_API_KEY` unset and `OPENAI_BASE_URL` pointed at a dead local port.
- **Every keyboard shortcut** gets tests for the action, its alias, modifiers being
  ignored, key repeat, and each guard (text input, textarea, select,
  contenteditable, open dialog, busy, loading, IME, non-crop view).
- **Preview and save parity:** any change to `warp()` is tested through both
  `/api/crop-preview` and `/api/crop`.
- Update this file and `WINDOWS_SMOKE.md` when a PR adds a test layer or a manual
  step.
