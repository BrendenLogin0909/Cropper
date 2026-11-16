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
image fills a decoded 960/1400 preview cache; JPEG cache misses may draft-decode
to ~960 px. Prediction results are LRU-cached on the server and in a small client
Map. Opening also prefetches the current and next image on one background worker.
Cancelling predict (C, Z, Backspace, Whole image) aborts the in-flight request.
Switching photo or folder also aborts; begin-batch/folder change resets the
server caches and prefetch queue. Saving a crop, restore, or enhance evicts that
resolved path from both LRUs, and the client Map drops that path so the next P
refetches. Save/export still uses the full-resolution decode. Run
`python tools/perf/bench_crop_corners.py --runs 7` for synthetic timings.
