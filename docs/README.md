# Cropper project docs (source of truth)

**Read this folder before you change anything.** These documents are the source of
truth for what Cropper is, what it must do, how it is built, what was decided and
why, and how it is tested. They exist so that quality survives when the project
outgrows any one person's or agent's memory.

**Update the docs in every pull request.** If a PR changes behaviour, a shortcut,
an endpoint, a requirement's status, the plan or the test approach, the same PR
updates the matching doc. A PR that leaves the docs wrong is not ready to merge.

| Doc | What it holds |
|---|---|
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | Purpose, users, platform, privacy stance, goals and non-goals |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Functional and non-functional requirements with IDs and status |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, security model, pipelines, API endpoints, diagram |
| [DECISIONS.md](DECISIONS.md) | Dated, ADR-style decision log |
| [PLAN.md](PLAN.md) | What is in flight, merge sequence and roadmap |
| [TESTING.md](TESTING.md) | Test strategy, CI, fail-old discipline, fixtures |
| [WINDOWS_SMOKE.md](WINDOWS_SMOKE.md) | Manual Windows smoke checklist |

## Ground rules (summary; details in DECISIONS.md)

- `stable-v1` is the revert point. Never touch `main`, `stable-v1` or
  `ai-restore-clean` directly. All work happens on feature branches and draft PRs.
- No force-push, no history rewrites, and nothing merges without the owner's
  explicit OK.
- **Private data:** never commit, upload or describe real photos or scans, and
  never send them to OpenAI for testing. Use synthetic fixtures only. No secrets,
  keys or `.env` values in the repo.

## Where the code is

`main` (= `stable-v1`, `5c1c50e`) still holds the older, simpler app. The current
development line is the stacked draft PR chain on `ai-restore-clean`
(#1 → #2 → #3 → #14 → #15); see [PLAN.md](PLAN.md). Unless a section says
otherwise, these docs describe the code at the head of that chain
(`fix/editor-shortcuts`, `43147ef`, 16 Nov 2026), plus approved work not yet built.
