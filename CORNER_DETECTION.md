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
cache off the request path in two stages: the 960 px detect image is published
first (same identity + generation check under `_fill_lock`), then the 1400 px
preview is built with `cv2.resize` INTER_AREA and published the same way. A
predict waiting on that fill is released as soon as the 960 entry is available,
not after the 1400 build. The 960 detect resize is unchanged (PIL BILINEAR
thumbnail). An evict between the two puts stops the second put. A fill captures
`file_identity` and a per-path eviction generation before decode and puts only
if both are still unchanged; otherwise the decoded pixels are discarded. The
identity re-stat, generation check, and `IMAGE_CACHE.put` share one fill lock,
acquired before the LRU lock (never the reverse), so a same-identity
`evict_path` cannot land between the check and the put. Prefetch and every
RESULT_CACHE entry use a full decode (JPEG draft decode was removed).
Prediction results are LRU-cached on the server and in a small client Map.
Opening also prefetches the current and
next image on one background worker; a foreground predict re-queues a skipped
neighbour rather than dropping it. Cancelling predict (C, Z, Backspace, Whole
image) aborts the in-flight request. Switching photo or folder also aborts;
begin-batch/folder change resets the server caches and prefetch queue. Saving a
crop, restore, or enhance evicts that resolved path from both LRUs, and the
client Map drops that path so the next P refetches. Save/export still uses the
full-resolution decode. After the homography, warp may follow bowed print edges.
**Straighten bowed edges is on by default.** It runs after the four-corner warp
on both preview and save. An edge remaps only with all three of: a scanner-bed
Lab band outside the fitted curve along ≥ ~30% of the side (`BACKGROUND_DE` 14,
`BACKGROUND_BAND_FRAC` 0.30; background = median Lab of a ≤ `BACKGROUND_MAX`
(400 px) copy of the source outside a dilated quad), full-resolution bow depth
≥ `BOW_DEPTH_PX` (4 px), and consistent
inward sag (`SIGN_FRAC` 0.80). Picture-content gradients never count.
Unqualified edges stay exactly straight; if none qualify, output is
byte-identical to OFF. Poor quadratic fit is left straight — **there is no
trim**, even when the background match is strong. Fitted depth is clipped to
`DEPTH_CAP_FRAC` (3% of the side). If any pixel would move more than
`DISPLACEMENT_CAP_FRAC` (2% of the side), that edge is skipped (straight warp).
Predicted extra area loss above `AREA_LOSS_FRAC` (0.5%) also skips the edge.
The ≤800 px estimate is refined at full resolution in a `REFINE_BAND_PX` (6 px,
widened by the downsample scale) band; remap is restricted to the affected
edge band. Turn it off in Save settings (the checkbox is stored in
localStorage like auto-next). **Off matches the previous homography-only
output** (byte-identical to the crop-speed warp). The flag is not part of the
corner-prediction cache key. Run
`python tools/perf/bench_crop_corners.py --runs 7` for synthetic timings.

Cache keys are `(resolved path, mtime_ns, size)` — not ctime. An external edit
in the same mtime tick that leaves the file size unchanged can therefore hit a
stale cached result. Our own writes evict the path explicitly. NTFS mtime is
100 ns; Windows `st_ctime` is the file's creation time, so adding it would not
close that hole. This becomes an ADR in DECISIONS.md when this stack joins #16's
docs.

## PLAN

Live preview p95 on real-world photos ~434 ms after a cold press vs a 400 ms goal;
re-measure with the conservative bow remap (synthetic cache-hit preview-1400
and 4500 px save extra are reported in the bowed-edges PR). Target ≤ +50 ms p95
on a straight save (no-qualify ≈ zero remap) and ≤ +0.5 s p95 extra on a 4500 px
bowed save.

## TESTING

Synthetic fixtures only in-repo. False-positive cases (stripes, dark/light
blocks, horizon near the border, bright sky at top) have content to the print
edges and no background band outside; ON must be byte-identical to OFF, with
area loss ≤ 0.1%. A qualifying bowed print on scanner background with a thin
varying streak must leave a residual streak ≤ 2 px at save scale, and extra
area loss ≤ 0.5%. Preview-vs-save bounds stay MAE < 0.5, corner max ≤ 6.0 px,
curve p95 ≤ 5.0 px (measured 4500×3400 MAE 0.208 / corner 4.24 / curve p95 3.00).
Real-scan validation is local and off-repo.
