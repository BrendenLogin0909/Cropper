# Corner prediction

Uses local OpenCV edge, contour and colour-boundary detection, not a trained
neural model. No API request or image upload; the endpoint only reads the source.

- P / Predict corners: suggest an outline. Repeat to cycle cached alternatives. Ctrl+A is an alternate.
- W / Whole image: select the full image boundary. Ctrl+Shift+A is an alternate.
- On a page containing multiple photos, clear points and place one point inside
  the intended photo before predicting. Candidates must contain that point.
- C or Ctrl+Q clears points and cancels application of any outstanding prediction.
- Z or Ctrl+Z undoes the last point and cancels an outstanding prediction.
- R rotates the crop clockwise. A and D (or the arrow keys) change image.
- Shift zoom remains unchanged. Enter saves.
- Inspect and adjust every suggestion before saving. Rounded corners, weak
  boundaries, internal rectangular objects and partial prints remain difficult.

## Validation

Development used the first 24 originals from a private family scan set. A subsequent
12-image sample from a set of local test scans was checked without further tuning.
Visual review of the first outline found 9/12 usable: 75%. This is an informal
small-sample estimate, not an independently labelled corner-accuracy benchmark
or a guarantee. Two no-detections and one incorrect outline were counted as
failures. Detection-only time was 222–583 ms; image loading and HTTP overhead
are additional. No original photos were modified.

The new endpoint requires an idle backend restart. Browser refresh alone cannot
install a new Python handler. The UI explains this if the old backend returns 404.

Detection still runs at 960 px. Quad scoring is vectorised and de-duplicated
before the expensive side walk so the same corners come back faster. Opening an
image replies with the display JPEG first, then fills a decoded 960/1400 preview
cache off the request path; an in-progress fill is waited on and reused so predict
does not decode twice. A fill captures `file_identity` and a per-path eviction
generation before decode and puts only if both are still unchanged; otherwise
the decoded pixels are discarded. Prefetch and every RESULT_CACHE entry use a
full decode (JPEG draft decode was removed). Prediction results are LRU-cached
on the server and in a small client Map. Opening also prefetches the current and
next image on one background worker; a foreground predict re-queues a skipped
neighbour rather than dropping it. Cancelling predict (C, Z, Backspace, Whole
image) aborts the in-flight request. Switching photo or folder also aborts;
begin-batch/folder change resets the server caches and prefetch queue. Saving a
crop, restore, or enhance evicts that resolved path from both LRUs, and the
client Map drops that path so the next P refetches. Save/export still uses the
full-resolution decode. After the homography, warp may follow bowed print edges
(quadratic per edge, Coons remap, capped trim on a poor fit). That step is on
by default and skipped when the toggle is off, so the off path matches the
previous warp. The flag is not part of the corner-prediction cache key. Run
`python tools/perf/bench_crop_corners.py --runs 7` for synthetic timings.

Cache keys are `(resolved path, mtime_ns, size)` — not ctime. An external edit
in the same mtime tick that leaves the file size unchanged can therefore hit a
stale cached result. Our own writes evict the path explicitly. NTFS mtime is
100 ns; Windows `st_ctime` is the file's creation time, so adding it would not
close that hole. This becomes an ADR in DECISIONS.md when this stack joins #16's
docs.

## PLAN

Live preview p95 on real-world photos ~434 ms after a cold press vs a 400 ms goal;
re-measure with step 3 curve warp (synthetic cache-hit preview-1400 with the
remap is reported in the bowed-edges PR).
