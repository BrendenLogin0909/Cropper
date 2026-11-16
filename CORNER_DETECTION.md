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
on both preview and save. Sag remaps only with all of: a scanner-bed
Lab band outside the fitted curve along ≥ ~30% of the side (`BACKGROUND_DE` 14,
`BACKGROUND_BAND_FRAC` 0.30; background = median Lab of a sparse ring of
full-resolution samples just outside the crop quad, falling back to a ≤
`BACKGROUND_MAX` (400 px) outside-median), full-resolution depth
≥ `max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX)` (`BOW_DEPTH_FRAC` 0.2%, floor 3
full-res px). Depth is the observed background-band width (p90) so a 3 px
bow is not lost to 1 px end AA; chord-relative sag must still be a bow
(≥ half the gate, consistent inward sign, `SIGN_FRAC` 0.80; the peak need not
be centred) so a strip or wedge stays identity. Band WIDTH must track that sag (Pearson
correlation ≥ `BAND_TRACK_CORR` 0.75 — a real bow widens toward the middle;
noise and picture edges do not). The profile is quadratic, cubic, or a spline
with ≤ `SPLINE_CONTROLS` (5) points, ends locked; the model is chosen by BIC
on residual vs the background band (`k` = 0 for a zero-sag baseline, then
1/2/3). A fit with RMSE > `POOR_RESIDUAL_PX` (2.5) is discarded so a spline
does not win on picture content or a perspective margin. Picture-content gradients never count.
A linear (offset + tilt) bed-strip term is **not applied** (option (i)).
The 80% background rule plus the ΔE content measure cannot tell a matching-colour
print border from a scanner-bed strip (synthetic probe below); there is no
blind fallback and no workaround. An even-width bed strip is a
**corner-placement** problem, not a bowed edge, so it does not belong in
`warp()`. Unqualified edges stay exactly straight; if none
qualify, output is byte-identical to OFF. Poor quadratic fit is left
straight — there is no unguarded trim. Fitted sag is clipped to
`DEPTH_CAP_FRAC` (3% of the side). Displacement is skipped above
`DISPLACEMENT_CAP_FRAC` (2% of the side), or `DISPLACEMENT_BACKGROUND_FRAC`
(3%) when the pixels being pulled out are scanner background under
`BACKGROUND_DE`. Predicted extra area loss is a **per-print** budget: after
edges qualify, content loss (sag pixels that do not match the ring Lab within
`BACKGROUND_DE`) must stay ≤ `AREA_LOSS_FRAC` (0.5%), and geometric sag area ≤
`GEOMETRIC_LOSS_FRAC` (1.5%). Largest-loss edges are dropped until both hold;
if that removes them all, output is byte-identical to OFF. All-background sag
of e.g. 0.82% is allowed: the 0.5% budget is **content-only**, with the 1.5%
geometric backstop. The ≤800 px estimate is refined in a band around the
coarse edge at ~2000 px (`REFINE_MID_MAX`) and/or full resolution. A cheap
≤960 (`EARLY_MAX`) band check can skip full detection when no side is a
candidate. Remap is restricted to the affected
edge band. Preview and save apply **one** curve: `/api/crop-preview` loads the
full-resolution source, runs that detect+refine on the full-res homography, and
scales the already-qualified params onto the 1400 px warp instead of refitting.
If the source file cannot be read after the 1400 cache is filled, preview falls
back to detecting on the 1400 warp alone. Turn it off in Save settings (the checkbox is stored in
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

## Decisions (2026-11-17)

Preview-vs-save bounds are in **preview pixels**. With one qualify/skip decision
and one param set, computed at full res and scaled onto the 1400 warp, the
remaining 4500 gap is resampling (1 preview px ≈ 3 save px), not drift. Convert
save-scale distances by dividing by the save/preview scale factor. Corner max
is the converted per-axis error (a 3 px-per-axis LANCZOS step is one preview
pixel; the 3√2 Euclidean is the same step on both axes). Bounds: MAE < 0.5,
corner max ≤ 1.0 preview px, curve p95 ≤ 1.0 preview px. `docs/` is not on this
branch; this ADR lives here until #16's DECISIONS.md.

A. Depth gate: `BOW_DEPTH_FRAC` is **0.2%** of the side, with an absolute floor
of `BOW_DEPTH_PX` (3 full-res px). The gate is
`max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX)`. Retune the fraction constant.
Band-width-vs-sag Pearson ≥ `BAND_TRACK_CORR` 0.75. A 3 px bow on a 1500 px
side with a bed streak remaps; a noisy or picture edge stays identity.

(a)–(d) 2026-11-16 tuning: (a) the 0.2%/3 px gate above. (b) quadratic, cubic,
or ≤5-point spline, ends locked, BIC vs a k=0 zero-sag baseline then k=1/2/3;
RMSE > 2.5 px is discarded so a spline cannot fit picture content. (c)
multi-scale refine at ≤800 then ~2000 or full-res; 60 px bows on a ~3000 px
side remap under the 2%/3% displacement caps, 0.5% content budget, and 1.5%
geometric backstop. (d) cheap ≤960 early exit before full detection; the
early-exit decision equals the full-path decision on every suite fixture.
All-background geometric loss of 0.82% is allowed because the 0.5% budget is
content-only.

B. Linear (offset + tilt) correction is **omitted** (option (i)). A uniform
border the same colour as the bed is indistinguishable from bed under the
evidence and content guards. Synthetic numbers (3000×2200, 15 px border, no
print-edge line or shadow): evidence **1.00**, content **0**, **15 px**.
Every stated guard would have passed; a probe that applied the term ate the
left and right borders. An even-width bed strip is a corner-placement
problem, not a bowed edge, so it does not belong in `warp()`. No workaround.

C. Displacement cap is 3% (matching the depth cap) only where the pulled
pixels are background under `BACKGROUND_DE`; elsewhere it stays 2%. Asymmetric
bows are allowed when the sag sign is consistent along the edge.

## PLAN

Live preview p95 on real-world photos ~434 ms after a cold press vs a 400 ms goal;
re-measure with the conservative bow remap (synthetic cache-hit preview-1400
and 4500 px save extra are reported in the bowed-edges PR). Target ≤ +50 ms p95
on a straight save (no-qualify ≈ zero remap) and ≤ +0.5 s p95 extra on a 4500 px
bowed save.

A planned follow-up is **not this PR**: corner refinement that moves an edge
inward only on a positive print-edge signal. See `docs/PLAN.md`.

## TESTING

Synthetic fixtures only in-repo. False-positive cases (stripes, dark/light
blocks, horizon near the border, bright sky at top) have content to the print
edges and no background band outside; ON must be byte-identical to OFF, with
area loss ≤ 0.1%. A qualifying bowed print on scanner background with a thin
varying streak must leave a residual streak ≤ 2 px at save scale, and extra
content loss ≤ 0.5% **per print** and geometric loss ≤ 1.5%. Preview-vs-save
bounds are in preview pixels: MAE < 0.5, corner max ≤ 1.0, curve p95 ≤ 1.0
(save-scale distances divided by the save/preview scale). On a bowed edge the
**curved** residual must be ≤ 2 full-res px. A leftover constant or tilted
strip is **reported per edge, not failed**. A 3 px bow on a 1500 px side
remaps; noisy, picture-edge, strip, wedge, uniform-border, and busy fixtures
stay byte-identical to OFF. Early-exit modes match the full path on every
suite fixture. Real-scan validation is local and off-repo.
