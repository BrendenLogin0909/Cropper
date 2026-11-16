# AGENTS.md: rules for anyone (human or agent) working on Cropper

1. **Read [`docs/`](docs/README.md) first.** It is the source of truth: brief,
   requirements, architecture, decisions, plan, testing and the Windows smoke list.
2. **Keep the docs updated in every PR.** If you change behaviour, a shortcut, an
   endpoint, a requirement's status, the plan or the tests, update the matching doc
   in the same PR. Add an ADR to `docs/DECISIONS.md` for any real decision.
3. **Branch and merge rules** (ADR-001):
   - Never commit to `main`, `stable-v1` or `ai-restore-clean`. `stable-v1` is the
     revert point and is never touched.
   - Work on a feature branch and open a **draft PR**. Each PR gets code review and
     QA, then the owner tests it on Windows.
   - **No force-push, no rebases of pushed branches, no history rewrites.** Fix
     with new forward commits.
   - **Nothing merges without the owner's explicit OK.** Follow the merge sequence
     in `docs/PLAN.md`.
   - CI must be green on windows-latest and ubuntu-latest. Bug-fix tests must fail
     on the old head (fail-old).
4. **Private data rule** (ADR-009): never commit, upload, attach, describe or name
   real photos or scans, and never send them to OpenAI for testing. Use synthetic
   fixtures only. Never commit secrets, API keys or `.env` values, and never log
   keys, notes or request bodies.
5. Keep corner detection and all non-AI processing **local**. Only explicit, paid
   user actions may call OpenAI, with the user's own key.
