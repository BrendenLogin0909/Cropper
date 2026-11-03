# Windows smoke checklist

Run these after a Windows build or before a release. Start Cropper with `python server.py`, then use a browser on `http://127.0.0.1:8765/`.

1. Open a batch folder with **Open folder**. Confirm the native Windows folder dialog appears, and that the chosen folder’s photos load.
2. Click **Choose back…** on the Front + Back tab. Confirm the same native Windows folder dialog opens, and that the chosen folder is registered so you can select a back image from it.
3. List or browse a folder you did not pick in the dialog. Confirm it does not become an allowed root (merge or crop of a file there is refused until you pick that folder in the dialog).
4. Crop one photo, then Finish it. Confirm the source leaves the queue and the snapshot lands in the completed stage folder.
5. With Overwrite on, save twice and confirm the overwrite prompt. Check **Don’t ask again for this batch** and confirm later photos in that batch do not prompt.
6. Turn on Replace original (backups stay on). Confirm the replace prompt, then confirm the original is re-encoded to match its extension and a backup exists.
