# Project brief

_Last updated: 2026-11-16._

## Purpose

Cropper helps people restore and crop scanned or photographed prints. A user opens
a folder of scans or phone photos, marks (or auto-detects) the four corners of each
print, and saves a straightened, correctly proportioned crop. Around that core it
offers front/back pairing, local auto-improvement and repair, and optional paid AI
restoration and colourisation.

## Users

- Many users across many countries and languages (non-ASCII folder and file names
  are normal, not an edge case).
- Varied capture devices: flatbed scanners, phone cameras, compact cameras,
  photo-scanning apps, each with different resolutions, formats, naming schemes
  and lighting.
- Mostly non-technical people digitising family prints. They work in long batches,
  mostly keyboard-plus-mouse, and must not lose work or originals.

## Platform

- **Windows only for now.** The app is a local Python server (`server.py`) plus a
  browser UI. It is started with `Start Cropper.cmd` or `python server.py`.
- CI also runs on Ubuntu to keep the code portable, but Linux and macOS are not
  supported targets yet.

## Privacy

Verified against the code at `43147ef`:

- **Processing is private and local.** The server binds to `127.0.0.1` only and
  accepts only same-origin, token-bearing requests from localhost.
- **Automatic corner detection is local OpenCV** (`crop_detection.py`). It imports
  only `cv2`, `numpy` and `PIL`, makes no network call and needs no key.
- Cropping, front/back merge, auto-improve, local repairs (dust, noise, sharpen),
  pair suggestions and the B&W/sepia scan are all local.
- **Optional AI restoration and colourisation call the OpenAI Images API**
  (`chatgpt_restore.py`), and only when the user explicitly clicks a paid
  generate/resubmit action, or **Test key** in Settings. They use the user's own
  key (Windows Credential Manager via `keyring`, or `OPENAI_API_KEY`). Without a
  key these features are disabled and everything else works offline.
- The UI loads no external scripts, fonts or analytics.
- The older app on `main` / `stable-v1` has no OpenAI code at all.

## Goals

1. **Improve the existing app rather than rebuild it.** Small, reviewed, reversible
   PRs on top of what works.
2. **Fast, accurate cropping** at batch scale: good corner prediction, keyboard-first
   editing, and straight, streak-free output.
3. **Never lose originals or work:** no silent overwrites, verified saves, backups
   for replace-original.
4. **Become open-source ready:** licence AGPL-3.0 plus a Contributor Licence
   Agreement (CLA) is **planned, not done**; see DECISIONS.md ADR-010.
5. **A hosted paid version later**, after the hosted-hardening items in issue #13.

## Non-goals (for now)

- macOS or Linux desktop support, installers, or a native (non-browser) shell.
- Cloud storage, accounts, sync or uploading photos anywhere by default.
- Training or shipping a neural corner detector.
- Generating new image content locally (AI edits are opt-in and remote only).
- Relying on a particular scanner, filename scheme or scan order.
- A full photo-library manager (tagging, albums, search).
