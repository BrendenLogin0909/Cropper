# Cropper

Cropper is a local browser-based image cropper for quickly straightening photos, documents, and other four-corner subjects. The image processing runs on your computer with OpenCV and Pillow. No account, subscription, upload, or internet connection is required.

Cropper also has **Front + Back** for pairing both sides of a print, **Auto improve** for colour cast and tonal correction, and separate **Dust repair**, **Noise reduction**, and **Sharpen** tabs.

## Start

On Windows, double-click **Start Cropper.cmd**. Your browser opens automatically. Keep the command window open while using the app; close it to stop the local server.

If Python reports a missing dependency, run this once in the Cropper folder:

```powershell
python -m pip install -r requirements.txt
```

You can also run `python server.py` directly. Cropper binds only to `127.0.0.1`, using port `8765` so your browser remembers the batch settings between launches. Use `--port` if that port is occupied by another app.

## Batch workflow

1. Choose an image folder with **Open folder**. Its built-in picker lets you browse drives and folders. You can also paste a folder path into Explorer.
2. Choose a save destination. The setting persists in this browser for later sessions.
3. Double-click a thumbnail. Click the four corners of the subject in any order.
4. Hold **Ctrl** to magnify around the mouse pointer for precise clicking or dragging. Release Ctrl to return to the fitted view.
5. Press **Enter** or click **Crop & save**. With **Move to next image** enabled, the next image opens automatically.

Drag any corner after placing it. Use **Ctrl+A** to select the whole image, **Ctrl+Z** to undo the last corner, **Backspace** to reset, and **Left/Right arrow** to move between images. The Rotate buttons rotate the saved crop by 90 degrees.

The image list has its own scrollbar, so a large folder does not move the crop frame off-screen. If Cropper reports that a folder is not writable, choose another output folder or check that folder's Windows permissions.

Output choices:

- **Source subfolder:** saves into a named subfolder beside the source image. Default: `Cropped`.
- **Beside original:** creates a copy in the same folder, using a prefix or suffix.
- **Another folder:** sends every crop to one chosen folder.
- **Replace original:** atomically replaces the source file after the cropped file has been encoded.

For copy modes, an existing destination filename gets a numeric suffix so it is not overwritten. Supported formats: JPEG, PNG, WebP, TIFF, and BMP. Cropper uses a conventional perspective transform; it does not generate new image content.

If the cropped photo looks stretched, choose a standard **Output proportions** setting and check the live straightened preview before saving. The list identifies common physical print shapes: 6 × 4 inch prints are 2:3, 7 × 5 are 5:7, 8 × 10 are 4:5, and 11 × 14 are 11:14. Cropper automatically uses the portrait or landscape version of the selected ratio.

**Whole image** selects the four outer corners when you want to correct an existing crop's proportions. Save the repair as a new copy. Re-cropping from the original photograph gives the best result when it remains available.

The four corners determine perspective but do not, by themselves, determine the original physical width-to-height ratio. Cropper's measured-edge option is only a convenient starting point when the camera was nearly square to a flat print. The live preview lets you compare a likely print shape before a full-resolution file is written.

## Front + Back

Select the front photo first, then its matching back. Cropper places the back to the right of a portrait front or below a landscape front. You can rotate the back before saving the pair as a new image.

## Auto improve

1. Open whichever folder contains the images to improve. It can hold original photos, cropped photos, front and back images, or any other supported images.
2. Select **Auto improve**. Click an image to add it to the batch and see its before and after preview. Click more images, or use **Select all**.
3. Choose a destination. The default is an `Enhanced` subfolder with `_enhanced` added to filenames. These settings persist in the browser. After an enhanced copy is written successfully, Cropper moves the source into a `Pre-Enhancement` subfolder beside it, preserving the original for later comparison or recovery.
4. Review the preview, then select **Save selected copies**. Cropper analyses each photo individually and reports the batch result.

The automatic correction uses local pixel processing. It can reduce a colour cast and improve faded tones, but it cannot know the original colours of every aged photo. Photos with unusual lighting or intentional sepia tones should be reviewed before saving. The preview is limited to 1,800 pixels; saved copies use the image's full resolution. Originals are preserved in Auto improve.

The same decision rules run on every image, but the result is measured separately for each one:

- **Colour cast:** Cropper samples likely whites and darker, less colourful areas separately. It balances the two ends of the photo so a reddish black can be corrected without making a white dress blue. It skips this step if suitable areas are scarce or the cast is small.
- **Lingering yellow:** If those areas indicate a strong warm cast across the photo, Cropper also softens excess yellow and some red in already-warm colours. Nearly neutral whites are left alone. The strength depends on that photo's measured cast, and the reduction is capped so naturally warm objects retain colour.
- **Contrast:** For prints with faded blacks and dull whites, it expands the measured tonal range with a soft toe and shoulder to retain highlight texture. For other low-contrast photos it uses a gentler midpoint adjustment. It skips this step when contrast is already broad or the image is nearly uniform.
- **Gray veil:** For a nearly neutral print whose darkest areas are still mid gray, it lowers the black floor while protecting bright detail. The flattest of these prints also get a small, limited local contrast lift. Clearer or colourful prints skip this step.
- **Saturation:** If the image contains coloured areas that appear weak, it adds a small colour lift to muted pixels. The lift tapers to zero for vivid pixels, and neutral black-and-white photos do not receive it.

The preview names the corrections selected for that photo. These adjustments do not remove scratches, sharpen blur, invent missing detail, or accurately reconstruct unknown original colours. Local contrast can make existing grain and scratches more visible, so check a preview before saving a large batch.

## Dust repair, Noise reduction, and Sharpen

Each restoration step has its own tab. Open the folder you want to process, click photos to select them, review the before and after preview, then choose **Save selected photos**. **Select all** adds every image in the current folder. Cropper measures every photo separately and applies only a bounded adjustment when it detects a suitable issue. An image that needs no adjustment is still copied into the results folder so the batch can move forward without processing it twice.

| Tab | Automatic change | New image folder | Previous version folder |
| --- | --- | --- | --- |
| Dust repair | Removes small, isolated marks and short scratches only where the mark is clearly distinct from nearby image detail. | `Dust Repaired` | `Before Dust Repair` |
| Noise reduction | Reduces fine grain and colour speckles in photos with measurable noise; leaves cleaner photos alone. | `Noise Reduced` | `Before Noise Reduction` |
| Sharpen | Adds a modest edge contrast boost where existing detail can be recovered; skips images with too much noise or too little edge detail. | `Sharpened` | `Before Sharpening` |

After a successful save, the previous file moves into the corresponding **Before…** folder beside its old location. The current folder then contains only images that have not gone through that step. **Open results folder** opens the new files so you can select the next restoration tab. You can start with any tab or any folder; the steps do not require a fixed order. The old version remains available in its archive folder. Existing result or archive filenames get a number appended so they are not overwritten.

Long scratches often need guidance. On the Dust repair tab, select a photo and choose **Mark a scratch**, then draw over the scratch in the **Before** preview. Choose **Done marking** when finished. **Show repair marks** displays in red exactly which pixels will be repaired; **Show result** returns to the repaired preview. **Clear marks** removes all hand-drawn marks for that photo. Marks stay associated with the selected photo until its save completes or you change folders.

These steps use OpenCV pixel processing on your computer. They do not create new scene detail or reliably repair severe damage, motion blur, or an out-of-focus face. Automatic dust detection is deliberately cautious around faces and textured areas; use the marking tool for remaining scratches and review the preview before saving. Previews are limited to 1,800 pixels while saved images use the original resolution.
