# Cropper

Local photo cropping, front/back pairing and auto-improvement, with optional paid
OpenAI API restoration and colourisation. Local steps require no API calls.
Add your own OpenAI API key in Settings. Local crop, merge and auto-improve
steps require no API calls.

## Start

Run `python server.py` in this folder. Default address: http://127.0.0.1:8765/.
Use `--port 8767` if desired. Use `--data-dir` to choose Cropper's per-user
folder (default `%APPDATA%\Cropper` on Windows). Restart the backend after
Python updates; refresh the browser after UI updates. Dependencies are pinned
in `requirements.txt`.

## One mandatory flat batch workflow

Every batch can have any location/name. All stage folders are siblings directly
inside that batch root. There is no legacy destination mode, overwrite mode,
custom output subfolder, or switch to disable flat routing.

| Action | Preserve incoming version | New output |
| --- | --- | --- |
| Crop from batch root or 1. Original | 1. Original | 1.5. TBP Cropped, append _c |
| Auto-improve or local repair from 1.5 | 2. Cropped | 1.6. TBP Auto-Improved, append _e |
| AI restoration from 1.5 | 2. Cropped AND 3.2. Skipped Auto-Improved | 1.7. TBP AI-Improved, append _ai |
| AI restoration from 1.6 | 3. Auto-Improved | 1.7. TBP AI-Improved, append _ai |
| Colourise from 1.7 | 4. AI-Enhancement | 4.1.1. AI Colourised, append _col |
| Colourise from 1.5 or 1.6 | Incoming completed stage plus unchanged snapshots for bypassed auto/AI stages | 4.1.1. AI Colourised, append _col |

Example progression: photo.jpg -> photo_c.jpg -> photo_c_e.jpg ->
photo_c_e_ai.png -> photo_c_e_ai_col.png.

Skipped snapshots are unchanged bytes, not improvements. Existing destinations
are never overwritten; conflicting names stop saving. Queue sources leave only
after required snapshots and output have been saved and verified. Nested stage
inputs are rejected rather than creating another stage tree.

Finish selected — no further changes files unchanged stage snapshots and clears
the selected queue sources. Rejecting a pending AI result does NOT finish or
advance its source; it leaves the source queued for another attempt.

## Crop

Open a batch root or 1. Original. Select a photo and place four points in any order.
Ctrl+A predicts corners locally; repeat to cycle outlines. For multi-photo pages,
place one point inside the intended print first. Ctrl+Q clears all points; Ctrl+Z
undoes one point; Backspace resets. Hold Ctrl to zoom. Left/right arrows navigate;
Enter saves. Prediction is approximate: inspect every outline before saving.
Whole image selects the full frame. Re-crop from the original, not a later stage.

Choose output proportions using the physical print size when known. Perspective
corners alone cannot establish the correct aspect ratio. The live preview helps
check proportions before writing a full-resolution image.

## Front + Back

Merge two images from the same 1.5. TBP Cropped folder. Choose which filename to
use and rotate the back if necessary. The new composite ends in _front-back_c;
standalone files remain intact. Approved restoration links let one paid front
edit produce both a standalone result and a rebuilt composite. The back stays
local and unchanged. Both files follow the same flat routing.

## AI restoration and colourisation

Select images using click, Ctrl-click, Shift-click or Select all. Generation saves
results locally for independent review while other requests continue. Adjustable
concurrency controls simultaneous requests. Stop prevents new requests; submitted
requests can still finish and incur charges.

Accept files the chosen result through the table above. Reject keeps the source.
Modify image reveals correction comments and resubmission options; a corrected
request is a new paid edit. All pending results, including older records whose
routing flag is false or missing, use the SAME flat workflow. Invalid stage
sources are rejected before a paid request; no fallback creates nested folders.

Colourisation appends _col universally, including after _ai. It preserves source
brightness/detail locally; colours are plausible estimates, not historical facts.
The free local B&W/sepia scan helps select appropriate fronts.

Open Settings to paste an OpenAI API key. Cropper stores it in Windows Credential
Manager, never in a plaintext file. Other settings are saved in
`%APPDATA%\Cropper\settings.json`. If `OPENAI_API_KEY` is set in the environment,
it takes precedence. The UI shows only the last four characters of a key.
`OPENAI_BASE_URL` may be set for tests or HTTP proxies; it defaults to
`https://api.openai.com/v1`.
The default image model is `gpt-image-2.5-sunburst`. Quality defaults to medium;
low is used for previews; high is an opt-in labelled about 20c per photo.
Without a key, AI restore is disabled and every other tool still works offline.
Do not paste keys into logs. API billing is separate from ChatGPT.

## Local repairs

Dust/noise/sharpening operations retain their algorithms and use auto-improvement
filing: input from 1.5, archive to 2. Cropped, output to 1.6 with _e. An unnecessary
repair leaves the source unchanged. No operation creates a nested Before folder.

## Navy album migration — 15 October 2026

174 nested legacy files moved into sibling folders; 84 unchanged skipped-auto
snapshots added. All 258 destinations were SHA-256 verified. Four empty legacy
folders were removed. Existing page-layout images and originals were left alone.
Eight stale pending attempts for already-accepted photos were preserved outside
the active queue, with original metadata, in the migration backup.

Journal and comparison copies were kept in the local migration backup folder.

No paid requests or new image rendering was needed for the migration.


## AI request failures

The ChatGPT tabs show a **Failed photos** panel with each failed filename and
its reason. API failures include an error code and request reference when the
service provides them. This panel remains visible after the batch finishes;
the notification also names the first failed photo and its reason. Failure
reasons cover authentication, project/model access, quota, throttling, image
rejection, upload size, timeout, connection and service errors. Private raw API
responses are not shown.

Unsuccessful photos remain selected for manual retry, and originals stay in
place. Other photos continue unless an account/access error stops dispatch.
A rate-limit 429 is retried once with backoff; out-of-credit 429s are not retried.
Other errors need the Retry failed photos button. Raw provider errors are not shown.
Restart the idle Cropper server and refresh the browser after updating Python
files. No paid image requests are made by the automated tests.
