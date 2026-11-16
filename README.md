# Cropper

Local photo cropping, front/back pairing and auto-improvement, with optional paid
OpenAI API restoration and colourisation. Local steps require no API calls.
Add your own OpenAI API key in Settings. Local crop, merge and auto-improve
steps require no API calls.

## Start

Run `python server.py` in this folder. Default address: http://127.0.0.1:8765/.
`--port` wins if passed; otherwise `CROPPER_PORT` is used; otherwise 8765.
Use `--data-dir` to choose Cropper's per-user folder (default `%APPDATA%\Cropper`
on Windows). Restart the backend after Python updates; refresh the browser after
UI updates. Dependencies are pinned in `requirements.txt`.

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

Skipped snapshots are unchanged bytes, not improvements. By default existing destinations are never overwritten; conflicting names stop
saving. Settings can switch that to Overwrite or Auto-suffix, and can set a
filename template (`{name}`, `{date}`, `{index}`, `{ext}`). Replace original
requires backups. Queue sources leave only after required snapshots and output
have been saved and verified. Nested stage inputs are rejected rather than
creating another stage tree. Paid AI names are reserved before the API call so
a result is never stuck pending. Incomplete pending files appear under Recovered
results; readable images can be kept or discarded, damaged images discarded only.

Finish selected — no further changes files unchanged stage snapshots and clears
the selected queue sources. Rejecting a pending AI result does NOT finish or
advance its source; it leaves the source queued for another attempt.

## Crop

Open a batch root or 1. Original. Select a photo and place four points in any order.
P predicts corners locally; repeat to cycle outlines (Ctrl+A is an alternate). For multi-photo pages,
place one point inside the intended print first. C or Ctrl+Q clears all points; Z or Ctrl+Z
undoes one point; Backspace also clears. R rotates the crop clockwise. A and D (or the
arrow keys) move to the previous and next image and drop unplaced points. Hold Shift
to zoom. Enter saves. Prediction is approximate: inspect every outline before saving.
Whole image (W) selects the full frame (Ctrl+Shift+A is an alternate). Re-crop from the original, not a later stage.

Choose output proportions using the physical print size when known. Perspective
corners alone cannot establish the correct aspect ratio. The live preview helps
check proportions before writing a full-resolution image. Straighten bowed edges
is on by default: after the four-corner warp, Cropper remaps an edge only when a
scanner-background band, a full-resolution bow, and a consistent inward sag all
agree. Picture content at the border is not treated as an edge. A poor fit is
left straight (no trim). Depth is capped at 3% of the side; an edge that would
move any pixel more than 2% of the side is skipped. Turn it off in Save
settings to get the previous homography-only crop; off is byte-identical to that
older output. Preview and save share that path. No downscaled remap on save.

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

## Scan-set migration

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

## Corner-prediction timing harness

Synthetic only (no real photos, no paths outside a temp dir). Refuses to run if
`OPENAI_API_KEY` is set:

```
python tools/perf/bench_crop_corners.py --runs 7
```

It prints p50/p95 for decode, 960 px resize, detect, open (JPEG response path,
before preview-cache fill), live-preview warp of the cached 1400 px image with
and without bowed-edge straighten (on applies the full-res curve, scaled),
cold vs cached server predict, and warp on/off
at 1200×900, 3000×2200 and 4500×3400, then warp-off/on plus strip and remap times
for synthetic bowed rectangles that qualify (target ≤ +50 ms p95 extra on a
straight save; ≤ +0.5 s p95 extra on a 4500 px bowed save).
