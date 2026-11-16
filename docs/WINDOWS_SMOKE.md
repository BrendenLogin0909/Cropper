# Windows smoke checklist

Manual checks to run on a real Windows PC before the owner approves a merge that
affects folders, saving, the folder dialog or AI features. CI covers the automated
suites; this list covers what only a real Windows desktop can show (the native
folder dialog, Credential Manager, file permissions, non-ASCII paths).

_Last updated: 2026-11-16. Labels were last checked against `da37e45` (PR #14);
re-check labels when the UI changes, and add a step for every new manual check._

About 20 minutes (steps 2b–2i add about 5, plus 5 min of waiting in the optional
step 2h; skip 2h, 11b, 25f and 28 if you are short of time). Run in **PowerShell**.
Tick each box: `[x] Pass` or `[x] Fail` (add a note if it fails).

Key behaviours under test:
- **Folder dialog:** **Open folder** and **Choose back…** open the real Windows
  folder dialog, and only a folder you pick there is allowed (steps 2b–2h). Typing a
  path or browsing still lists files, but crop/merge/AI on a folder you did not
  pick is refused.
- Thumbnails and previews load through the app; a saved **output folder** is
  re-checked on every save and must be inside a folder you opened (step 2i).
- A dialog that does not close by itself blocks new dialogs until it is really gone
  (step 2h).
- The overwrite checkbox reads "Don't ask again for the N photos in this folder for
  15 minutes". It covers later crops of the photos that were in the folder when you
  opened it, in the same tab. A photo added afterwards, another folder or a
  subfolder asks again. Browsing to another folder, starting a run, or 15 minutes
  end the grant. The checkbox is hidden for an empty folder or more than 2000 photos
  (steps 9 and 11). A photo changed in place after you tick the box asks again
  (step 9c2c); a photo added after you opened the folder can be replaced with its
  own one-time dialog (step 11).
- A 33rd browser tab closes the least-recently-used **idle** tab; if all tabs are
  busy the new tab is refused with `All browser tabs are busy…`.

> **Only steps 19 and 23 cost money** (step 19: one AI restore at medium quality, about 6c; step 23: up to four restores, about 24c). Steps 24–27 must cost nothing. Every other step is free and works offline.
> **Never use original photos.** Use only copies in the test folder below, and never attach real photos to issues or PRs.

---

## 1. Setup (3 min)

1. Get the branch:
   ```powershell
   cd $HOME\src   # or wherever you keep repos
   git clone https://github.com/BrendenLogin0909/Cropper.git; cd Cropper   # skip the clone if you already have it
   git fetch origin; git checkout <branch under test>; git pull
   git log --oneline -1
   python -m pip install -r requirements.txt
   ```
   **Expected:** checkout works; the log line shows the head commit of the PR under test. The install works with no errors.
   `[ ] Pass  [ ] Fail`

2. Make the test folders, using **copies** of 3–4 JPGs (for step 19 use an old photo you are happy to send to OpenAI, such as a public-domain image; **never a private family scan**):
   ```powershell
   $T = "$env:USERPROFILE\Desktop\Cropper Smoke"
   New-Item -ItemType Directory -Force "$T\batch" | Out-Null
   Copy-Item "C:\path\to\some\photos\*.jpg" "$T\batch\"   # COPIES only
   Get-ChildItem "$T\batch"
   ```
   **Expected:** 3–4 JPGs are listed in `...\Cropper Smoke\batch` (this path has a space on purpose).
   `[ ] Pass  [ ] Fail`

   **2b–2i: the real folder dialog (do these after step 3 has started the app; about 5 min, free)**
   b. **Open folder:** click **Open folder**. **Expected:** the native Windows folder dialog (title **Choose folder**) opens on top of the browser. Pick `$T\batch` → OK. The photos load.
   c. **Cancel adds nothing:** click **Open folder** again and press **Cancel**. **Expected:** no error, the current folder stays, nothing new is allowed (the photos in `$T\batch` still work).
   d. **Drive root refused:** **Open folder** → pick `C:\` (This PC → Local Disk) → OK. **Expected:** a red toast `A drive or filesystem root cannot be registered.` and no folder change.
   e. **Parent refused:** **Open folder** → pick `$T` (`Desktop\Cropper Smoke`, the parent of `batch`) → OK. **Expected:** `A parent of an already opened folder cannot be registered.`
   f. **Choose back… uses the same dialog:** open the **Front + Back** tab, click **Choose back…**. **Expected:** the same native dialog. Press **Cancel**: nothing changes. (Step 25d uses it for real.)
   g. **One dialog at a time:** click **Open folder** and leave the dialog open. In the browser, open a second tab at the same address and click **Open folder** there. **Expected:** the second tab shows a red toast `A folder dialog is already open.` and no second dialog appears. Cancel the first dialog.
   h. **Timeout (optional, 5 min wait):** click **Open folder** and do not touch the dialog for 5 minutes. **Expected:** after about 5 minutes the app shows a red toast: `The folder dialog timed out. Try again.` if the dialog **closed by itself**, or `The folder dialog is still open.` only if it is **still on screen**. Fail if the toast says "still open" when no dialog is visible. Then:
      - If the dialog **closed by itself**: click **Open folder** once more → exactly **one** new dialog opens. Cancel it.
      - If the dialog is **still on screen**: click **Open folder** (in this tab or a second tab) → the toast `A folder dialog is already open.` and **no second dialog**. Cancel the old dialog, wait 2 seconds, click **Open folder** → one dialog opens.
      **Fail** if two dialogs are ever on screen at once. Note which case you saw (QA verified both with a simulated dialog).
   i. **Saved output folder is re-checked:** **Settings** → **Output folder override** → type `$T\batch\out-test` (a folder that does not exist yet) → **Save settings**. **Expected:** saved; `out-test` is NOT created yet. Close the console window (stop the app), start it again (step 3), click **Open folder** and pick `$T\other` (create it first with one JPG copy: `New-Item -ItemType Directory -Force "$T\other"; Copy-Item "$T\batch\*.jpg" "$T\other\" -Exclude *_c*`). Crop that photo and save. **Expected:** a red error `The output folder is outside the allowed folder.`; the photo is still in `$T\other`; nothing appears in `$T\batch\out-test`.
      **Stage-sibling check:** make `$T\p\C2` with one JPG copy and `$T\p\1.5. TBP Cropped`. **Open folder** → `$T\p` → **Settings** → **Output folder override** `$T\p\1.5. TBP Cropped\x` → **Save settings**. Restart the app, **Open folder** → `$T\p\C2`, crop the photo and save. **Expected:** `The output folder is outside the allowed folder.`; the photo stays in `C2`; nothing is created under `1.5. TBP Cropped`. Then clear the output folder in **Settings** (leave it blank) → **Save settings**.
   `[ ] Pass  [ ] Fail`

3. Launch: `python server.py` (or double-click `Start Cropper.cmd`).
   **Expected:** the console prints `Cropper is running at http://127.0.0.1:8765` and the browser opens the app. Leave this window open.
   `[ ] Pass  [ ] Fail`

## 2. Your own OpenAI key (3 min)

4. Click **Settings** → paste your key into **OpenAI API key** → **Save settings**.
   **Expected:** you see `Saved key: …XXXX. Only the last four characters are shown.` and only the last 4 characters are visible. (If `OPENAI_API_KEY` is set in your environment, it says "Using OPENAI_API_KEY from the environment…" instead. For this test, clear it with `Remove-Item Env:OPENAI_API_KEY` and restart.)
   `[ ] Pass  [ ] Fail`

5. Check that the key is in Credential Manager:
   ```powershell
   cmdkey /list | Select-String -Context 0,3 "Cropper"
   ```
   (or Control Panel → Credential Manager → Windows Credentials). **Expected:** there's an entry for `Cropper` / user `openai_api_key`.
   `[ ] Pass  [ ] Fail`

6. Check that the key isn't saved as plain text anywhere. Paste the **last 12+ characters** of your key:
   ```powershell
   $frag = "PASTE-LAST-12-CHARS"
   Get-ChildItem "$env:APPDATA\Cropper", "$PWD" -Recurse -File -ErrorAction SilentlyContinue |
     Select-String -SimpleMatch $frag -List | Select-Object Path
   ```
   **Expected:** no output. (`%APPDATA%\Cropper\settings.json` exists but doesn't contain the key.)
   `[ ] Pass  [ ] Fail`

7. Click **Test key**. **Expected:** the message `This API key is valid.` Then click **Clear key**, run `cmdkey /list | Select-String Cropper` again and **expect no entry**. The AI tab shows "Add your OpenAI API key in Settings to enable AI restore." **Paste the key again and click Save settings** so you can do step 19.
   `[ ] Pass  [ ] Fail`

## 3. Output modes and conflict policy (4 min) — free, uses Crop only

Settings → **If the output name already exists** has these options: **Skip — keep the existing file** (default), **Overwrite**, **Auto-suffix**. There's also **Filename template** (default `{name}{suffix}{ext}`), **Backup originals before Replace original**, and **Replace original (requires backups)**.

8. **Skip (default):** in the **Crop** tab, open `$T\batch`, select a photo, click **Whole image**, then **Crop & save**. Do it again for the same photo.
   **Expected:** the first save creates `1.5. TBP Cropped\<name>_c.jpg`. The second stops with a conflict message, and the existing file is unchanged.
   `[ ] Pass  [ ] Fail`

9. **Overwrite:** change the setting to **Overwrite** → **Save settings**. Put two photos `A` and `B` in `$T\batch` that already have `_c` files in `1.5. TBP Cropped` (from step 8 or copies), then crop `A`.
   **Expected:** one dialog, **Overwrite existing files?**, saying "This batch would overwrite **1** existing file(s)." and naming `A_c.jpg`, with buttons **Cancel** and **Overwrite** and a **Don't ask again this batch** switch.
   a. Click **Cancel**: `A_c.jpg` is unchanged (same timestamp).
   b. Crop `A` again, tick **Don't ask again this batch**, click **Overwrite**: `A_c.jpg`'s timestamp updates.
   c. Crop `B` in the same tab: **no dialog**, `B_c.jpg` is overwritten.
   c2. Crop a third photo in the same tab (one that was **not** named in the first dialog): still **no dialog**.
   c2a. **Label:** in the dialog from step b, the checkbox text is `Don't ask again for the N photos in this folder for 15 minutes`, where N = the number of photos directly in `$T\batch` (`(Get-ChildItem $T\batch -File -Include *.jpg,*.jpeg,*.png,*.tif,*.tiff,*.webp -Name).Count`; subfolders don't count). Write down N and the count. In a folder with exactly 1 photo the text must read `Don't ask again for the 1 photo in this folder for 15 minutes` ("1 photos" is a Fail; fixed on a2de7c4). In an empty folder you can't crop, so skip that case.
   c2b. **New file after grant:** with the grant still live, copy a new photo and its `_c` file in (`Copy-Item "$T\batch\A.jpg" "$T\batch\NEW.jpg"`; `Copy-Item "$T\batch\1.5. TBP Cropped\A_c.jpg" "$T\batch\1.5. TBP Cropped\NEW_c.jpg"`). Click **Refresh** so `NEW.jpg` appears, then crop it: the dialog **appears again**. Refresh starts a new batch, so the old grant must not cover it. Cancel: `NEW_c.jpg` is unchanged. Fail if `NEW_c.jpg` changes without a dialog. (QA checked the no-refresh case at API level: refused.)
   c2c. **Photo changed in place after the grant:** with the grant from step b still live (same tab, no Refresh), rewrite `B` in place without renaming it: `$f="$T\batch\B.jpg"; [IO.File]::WriteAllBytes($f, [IO.File]::ReadAllBytes($f))` (same bytes, new change time). Crop `B`: the dialog **appears again**. Cancel: `B_c.jpg` is unchanged. Fail if `B_c.jpg` changes without a dialog. (QA checked this on Linux at API level for Overwrite and Replace: asks again.)
   c3. **New batch:** click **Parent** (or a subfolder) and browse back to `$T\batch` → crop `A`: the dialog **appears again**. Cancel. Repeat once with **Open folder** → pick `$T\batch`: same result.
   c4. **15-minute expiry (optional):** tick **Don't ask again** on a crop, wait 15 minutes without picking a folder, crop `B` again: the dialog **appears again**.
   d. Open the app in a **second browser tab** (Ctrl+T, then open the app URL; not Duplicate tab) and crop another photo that has a `_c` file: the dialog **appears again**. Click **Cancel**.
   Two prompts for one crop, no prompt in step a, or no prompt in step d, is a Fail.
   `[ ] Pass  [ ] Fail`

10. **Auto-suffix:** change the setting to **Auto-suffix** → crop the same photo again.
    **Expected:** a new file `<name>_c-2.jpg` (then `-3`, and so on). Nothing is overwritten.
    `[ ] Pass  [ ] Fail`

11. **Replace original with backup:** pick a different photo `P` and hash it first:
    ```powershell
    $P = "$T\batch\PHOTO.jpg"; $before = (Get-FileHash $P -Algorithm SHA256).Hash; $before
    ```
    In Settings, tick **Replace original (requires backups)**. **Expected:** **Backup originals before Replace original** ticks itself, and unticking backup also unticks Replace. Replace can never run with backup off (there is no "replace without backup" confirmation). Save the settings, then crop `P` and save.
    **Expected:** a dialog **Replace original files?** saying "This batch would replace **1** original file(s). A backup is kept in 0. Backups until the new file is verified.", with **Cancel** and **Replace**. Click **Cancel** first: `P`'s hash still equals `$before` and nothing new is in `0. Backups`. Crop `P` again and click **Replace**. Then crop a second photo in the same batch: **no second dialog** (once per batch, in this tab and folder; **Open folder**, starting a run or 15 minutes starts a new batch and asks again). The Overwrite dialog must **not** appear for Replace. Repeat this one-dialog check once with the policy set to **Overwrite** and once with **Auto-suffix**: Replace asks once either way. **New photo after opening:** copy a photo in (`Copy-Item $P "$T\batch\NEWR.jpg"`), crop it **without** Refresh: a Replace dialog appears for that one photo; click **Replace** and it is replaced once (backup in `0. Backups`). It must not loop.
    ```powershell
    (Get-FileHash $P -Algorithm SHA256).Hash   # expect: different from $before
    Get-ChildItem "$T\batch\0. Backups" -Recurse | Get-FileHash -Algorithm SHA256 | Where-Object Hash -eq $before
    ```
    **Expected:** the original was replaced, and a file in `0. Backups` has exactly the `$before` hash. If the save fails, the original must still match `$before` (look for a `.cropper-recovered` file if it doesn't).
    `[ ] Pass  [ ] Fail`
11b. **Replace keeps the file format:** copy a photo as a PNG (`Add-Type -AssemblyName System.Drawing; $i=[Drawing.Image]::FromFile("$T\batch\<photo C>.jpg"); $i.Save("$T\batch\fmt.png",[Drawing.Imaging.ImageFormat]::Png); $i.Dispose()`). With **Replace original** still ticked, crop `fmt.png` and click **Replace**. Then check the first bytes of the replaced file and of `P` from step 11:
    ```powershell
    foreach ($f in "$T\batch\fmt.png", $P) { $b=[IO.File]::ReadAllBytes($f)[0..3]; "$f  " + (($b | % { $_.ToString('X2') }) -join ' ') }
    ```
    **Expected:** `fmt.png` starts `89 50 4E 47` (PNG) and the `.jpg` starts `FF D8 FF` (JPEG). A `.jpg` starting `89 50 4E 47` is a Fail. `0. Backups` has `fmt.png`.
    `[ ] Pass  [ ] Fail`

12. **GPS:** copy a phone photo that has a location into `$T\batch` as `gps.jpg` (check: right-click → Properties → Details shows GPS). With default settings, crop it and save.
    **Expected:** the `_c` file's Properties → Details shows **no GPS** section (camera make/model are still there, and the image is the right way up). GPS being kept by default is a Fail. Then in Settings tick **Keep GPS location in saved photos** (off by default) → **Save settings**, crop `gps.jpg` again (with Replace original unticked; click **Overwrite** if the Overwrite dialog appears, or use Auto-suffix): this output **does** show GPS. Untick it again afterwards.
    `[ ] Pass  [ ] Fail`

12b. Reset Settings to **Skip**, untick Replace original, then **Save settings**.
    `[ ] Pass  [ ] Fail`

## 4. Lock and two windows (1 min)

13. In a **second** PowerShell window, from the same folder, run `python server.py`.
    **Expected:** it prints `Cropper is already running at http://127.0.0.1:8765`, opens a browser tab and exits. A second server does **not** start.
    `[ ] Pass  [ ] Fail`

14. With two browser tabs open on the app, select two different photos in `$T\batch` and click **Crop & save** in both tabs as close together as you can.
    **Expected:** both saves succeed or one gets a clear message. There's no crash, no half-written file and no duplicate `_c` file under Skip. Check `1.5. TBP Cropped`.
    `[ ] Pass  [ ] Fail`

## 5. Long path and spaces (1 min)

15. ```powershell
    $L = "$T\" + ("long folder name " * 14).Trim()   # well over 260 chars once the filename is added
    New-Item -ItemType Directory -Force "\\?\$L" | Out-Null
    Copy-Item "$T\batch\*.jpg" "\\?\$L\" -ErrorAction Continue
    ($L + "\1.5. TBP Cropped\x_c.jpg").Length
    ```
    Open that folder in Cropper and **Crop & save** one photo.
    **Expected:** it works (output in the folder's `1.5. TBP Cropped`), or you get a plain-language error. There's no crash or Python traceback in the UI. Note the result: if long paths aren't enabled in Windows, the error message counts as a Pass. All the earlier steps already used a path with spaces.
    `[ ] Pass  [ ] Fail`

## 6. Read-only folder (1 min)

16. Make a stage folder unwritable, then save two photos:
    ```powershell
    $RO = "$T\batch\1.5. TBP Cropped"
    icacls $RO /deny "${env:USERNAME}:(W)"     # attrib +r alone doesn't stop writes to a folder on Windows
    ```
    Select 2 photos and **Crop & save**.
    **Expected:** you see `Cropper cannot write to this location. Choose another output folder or check this folder's permissions.` There's no traceback, other work still continues, and the app keeps running. Undo it with `icacls $RO /remove:d "$env:USERNAME"`.
    `[ ] Pass  [ ] Fail`

## 7 + 8. Guidance notes and recovery after restart (3 min) — **the only paid step, about 6c**

17. Settings → **Quality** = **Medium — default** → **Save settings**. Crop the step 2 photo you chose for step 19 so it's in `1.5. TBP Cropped`. Open the **ChatGPT Image Restoration** tab and select **only that one photo**.
    **Expected:** a **Guidance (optional)** panel with the field **Guidance for this photo** and a counter `0/500`.
    `[ ] Pass  [ ] Fail`

18. Type a short note, e.g. `Her coat was dark green. The man on the left is wearing glasses.`
    **Expected:** the counter goes up as you type (e.g. `63/500`); it counts Unicode code points, so an emoji like 👍🏽 counts 2 and 👨‍👩‍👧 counts 5. Now paste more than 500 characters: the text is **not** cut down. Either the paste is blocked or an error says it's over 500, and **Generate** is disabled while it's over the limit. Trimming the text, or still allowing Generate, is a Fail. At this build the paste is refused whole: the field keeps your earlier text and the notice says `Guidance is limited to 500 characters. The extra text was not added.` Also try a Japanese IME entry and dragging text in near the limit; both are refused whole the same way.
    `[ ] Pass  [ ] Fail`

19. ⚠ **This costs about 6c.** Click **Generate selected photos (paid)** once.
    **Expected:** one result appears under **PENDING REVIEW** (count 1). The image reflects the note (green coat, glasses), and a **Guidance used** section shows your exact note with an **Edit** button.
    `[ ] Pass  [ ] Fail`

20. **Recovery:** **don't accept it.** Close the browser, press Ctrl+C in the server window, then run `python server.py` again.
    **Expected:** the same pending result is still under **PENDING REVIEW**, and **Guidance used** still shows the same note. Its files are in `%APPDATA%\Cropper\pending\`:
    ```powershell
    Get-ChildItem "$env:APPDATA\Cropper\pending"
    ```
    `[ ] Pass  [ ] Fail`

21. Check that the note text isn't in the logs: copy the server console text to `$T\console.txt`, then run `Select-String -SimpleMatch "dark green" "$T\console.txt"`. **Expected:** no matches.
    `[ ] Pass  [ ] Fail`

22. Click **Reject; keep originals** (free; **Accept and file result** is also fine).
    **Expected:** the pending item disappears and its files are removed from `pending\`. After Reject, the source stays queued. (If there were other attempts, Accept would say "Accept this version and discard the other N attempts". The **RECOVERED RESULTS** count should be 0.)
    `[ ] Pass  [ ] Fail`

> Don't click Generate again. A rerun or **Modify image** is a new paid edit.

---

## 9. AI restore outside a stage folder (3 min) — **paid, up to about 24c**

23. Make a plain folder that is **not** a TBP stage folder and put a **copy** of the same shareable photo in it under the exact name below:
    ```powershell
    $N = "$env:USERPROFILE\Desktop\CropperTest"; New-Item -ItemType Directory -Force $N | Out-Null
    [IO.File]::Copy("$T\batch\<any photo>.jpg", "$N\[Test]_sample_0001 [B&W].jpg", $true)   # avoids Copy-Item treating [ ] as wildcards
    $F = "$N\[Test]_sample_0001 [B&W].jpg"; $before = (Get-FileHash -LiteralPath $F -Algorithm SHA256).Hash; $before
    ```
    In Settings, set **Quality** = **Medium — default**, untick **Replace original (requires backups)**, then do a–c. Each time, open `Desktop\CropperTest` in the app, select the photo, click **Generate selected photos (paid)** once (⚠ about 6c each), then **Accept and file result**.
    a. **Suffix:** **Filename template** `{name}{suffix}{ext}`, **Output folder override** empty → **Save settings**. **Expected:** no error; `CropperTest\[Test]_sample_0001 [B&W]_ai.png` is created.
    b. **Copy:** **Filename template** `{name}{ext}`, override empty → **Save settings**, delete a's output first. **Expected:** `CropperTest\[Test]_sample_0001 [B&W].png` is created next to the `.jpg`.
    c. **New folder (must be inside the opened folder):** template `{name}{suffix}{ext}`, **Output folder override** = `$N\out` → **Save settings**. **Expected:** saved; `out` is created on the first save and `CropperTest\out\[Test]_sample_0001 [B&W]_ai.png` is there. (An override outside the opened folder, such as `Desktop\CropperTest-out`, is refused with `The output folder is outside the allowed folder.` since 7e8c14c.)
    After each: `(Get-FileHash -LiteralPath $F -Algorithm SHA256).Hash` still equals `$before`, and the `.jpg` is still in `CropperTest` (not moved into a stage folder).
    Any message **"Open a 1.5, 1.6 or 1.7 TBP stage folder for flat batch routing"**, a missing output, or a changed original is a Fail. If the build uses different output names, write down the exact path you get.
    d. **Uppercase .PNG original (Windows-only check):** clear the override, template `{name}{ext}`, **If the output name already exists** = **Overwrite**. Run `[IO.File]::Copy("$T\batch\<any photo>.png", "$N\old.PNG", $true); $p = (Get-FileHash -LiteralPath "$N\old.PNG").Hash`, then generate on `old.PNG` and accept (⚠ about 6c). **Expected:** the result is `CropperTest\old-2.png` and the app shows `Saved as old-2.png to protect the original`; `(Get-FileHash -LiteralPath "$N\old.PNG").Hash` still equals `$p`. If `old.PNG` changed, that is a Fail (original overwritten). Write down whether an **Overwrite existing files?** dialog appeared. Skip d if you have no PNG photo.
    Afterwards reset the template to `{name}{suffix}{ext}`, **If the output name already exists** to **Skip — keep the existing file**, and clear the override.
    `[ ] Pass  [ ] Fail`

## 10. Output folder you can't write to (2 min) — free

24. Deny yourself write access to the step-23 output folder, then try a restore into it:
    ```powershell
    $O = "$N\ro-out"; New-Item -ItemType Directory -Force $O | Out-Null
    $before24 = (Get-ChildItem -LiteralPath $O | Measure-Object).Count
    icacls "$O" /deny "${env:USERNAME}:(OI)(CI)(W)"
    ```
    In Settings set **Output folder override** = `$O` → **Save settings**. Open `Desktop\CropperTest`, select `[Test]_sample_0001 [B&W].jpg`, click **Generate selected photos (paid)** once.
    **Expected:** an error right away saying Cropper cannot write to this location (permission error; the server console shows a 403 for `/api/chatgpt-generate`), no review card, `(Get-ChildItem -LiteralPath $O | Measure-Object).Count` still equals `$before24`, and **no new request** on your OpenAI usage page (platform.openai.com → Usage). A charge or a written file is a Fail.
    Then remove the deny entry and clear the override:
    ```powershell
    icacls "$O" /remove:d "$env:USERNAME"
    icacls "$O"    # your user should no longer show (DENY)
    ```
    `[ ] Pass  [ ] Fail`

## 11. Front + Back outside a stage folder (2 min) — free

25. Put a front and a back copy in the plain folder (any two JPGs will do; the back may be a different size):
    ```powershell
    [IO.File]::Copy("$T\batch\<photo A>.jpg", "$N\[Test]_0005 front.jpg", $true); [IO.File]::Copy("$T\batch\<photo B>.jpg", "$N\[Test]_0005 back.jpg", $true)
    $hf = (Get-FileHash -LiteralPath "$N\[Test]_0005 front.jpg").Hash; $hb = (Get-FileHash -LiteralPath "$N\[Test]_0005 back.jpg").Hash
    ```
    Open the **Front + Back** tab and `Desktop\CropperTest`. Click the front, then the back.
    a. **Use name from** = **Second image (back)** → **Merge & save**. **Expected:** `CropperTest\[Test]_0005 back_front-back.jpg` is created, no stage-folder message. A name ending `_c.jpg` is a Fail.
    b. Choose **First image (front)** → **Merge & save**. **Expected:** `CropperTest\[Test]_0005 front_front-back.jpg`.
    c. Both hashes still equal `$hf` / `$hb`, and both files are still in `CropperTest`.
    (A plain folder like `CropperTest` writes its outputs inside itself; no stage folder is created or needed.)
    d. **Back in another folder (dialog):** `New-Item -ItemType Directory -Force "$N-back" | Out-Null; [IO.File]::Copy("$N\[Test]_0005 back.jpg", "$N-back\[Test]_0005 back.jpg", $true)`. Click **Clear pair**, open `CropperTest` (via **Open folder**), click the front, then type `$N-back` in the folder bar and press Enter. **Expected:** the files are listed but clicking the back / **Merge & save** is refused with `...outside the allowed folder.` (typing a path never allows it).
    d2. Now click **Choose back…** → the Windows dialog → pick `CropperTest-back` → OK. **Expected:** toast **Back folder opened. Click the matching back image.**; the panel still shows the front. Click the back → **Merge & save** is enabled → the merged file lands in `CropperTest` (the front's folder). Then try **Choose back…** → `C:\` → **Expected:** refused (drive root).
    f. **Forged list (optional, PowerShell, free):** with the app still running, paste:
       ```powershell
       $tab = -join ((1..32) | % { '{0:x}' -f (Get-Random -Max 16) })
       $s = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/session" -Headers @{ 'X-Cropper-Tab' = $tab }
       $h = @{ 'X-Cropper-Token' = $s.token; 'X-Cropper-Tab' = $tab }
       foreach ($p in 'C:\', $env:USERPROFILE, "$env:USERPROFILE\Desktop") { Invoke-RestMethod -Uri ("http://127.0.0.1:8765/api/list?path=" + [uri]::EscapeDataString($p)) -Headers $h | Out-Null }
       $w = (Get-ChildItem C:\Windows\Web -Recurse -Filter *.jpg | Select -First 1).FullName
       try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/merge -Headers $h -ContentType application/json -Body (@{ front_path = "$N\[Test]_0005 front.jpg"; back_path = $w; settings = @{} } | ConvertTo-Json); 'FAIL: merge accepted' } catch { $_.ErrorDetails.Message }
       try { Invoke-RestMethod -Uri ("http://127.0.0.1:8765/api/info?path=" + [uri]::EscapeDataString($w)) -Headers $h; 'FAIL: info accepted' } catch { $_.ErrorDetails.Message }
       ```
       **Expected:** both lines print an `...outside the allowed folder.` error (never `FAIL: ... accepted`), and no new file appears in `CropperTest`. This tab never used the dialog, so listing `C:\`, home or Desktop must not allow anything.
    e. **Read-only preflight:** `icacls "$N" /deny "${env:USERNAME}:(OI)(CI)(W)"`, then in `CropperTest` click the front and back and **Merge & save**. **Expected:** `Cropper cannot write to this location…`, no new file (`Get-ChildItem -LiteralPath $N` unchanged), front and back hashes unchanged. Then `icacls "$N" /remove:d "$env:USERNAME"`.
    `[ ] Pass  [ ] Fail`

## 12. Processing-location labels (1 min) — free

26. Click through the tabs **Crop**, **Front + Back**, **Auto improve**, **ChatGPT Image Restoration** and **ChatGPT Image Colorization** (and Dust repair if you use it).
    **Expected:** the top bar no longer says **Runs on this computer** anywhere. Under each tab title there is one line: **Local** for Crop, Front + Back, Auto improve and Dust repair; **OpenAI, uses your API key** for the two ChatGPT tabs. Any other wording, or a missing line, is a Fail.
    `[ ] Pass  [ ] Fail`

## 13. Hold-Shift zoom and normal Ctrl keys (2 min) — free

27. In **Crop**, open any photo in `$T\batch` and rest the mouse on it.
    a. Hold **Shift**: a ×3 zoom appears with the label **Precision zoom ×3 SHIFT**. Release Shift: the zoom ends.
    b. Hold **Ctrl**: nothing zooms. Press **Ctrl+A** on the photo: the page text is not selected (Ctrl+A is a hidden alternate for **P**; if the browser takes it, that is acceptable).
    c. Hold Shift, then **Alt+Tab** to another window and release Shift there; come back: the photo is not stuck zoomed.
    d. Open **Settings** → click **Filename template**. **Ctrl+A** selects only the template text (not the whole page); **Ctrl+C**, then **End**, then **Ctrl+V** pastes it once more. Typing capitals with Shift does not zoom anything. Press Ctrl+Z to undo the paste, or retype `{name}{suffix}{ext}`, then close without saving.
    e. In the ChatGPT tab, select one photo, click in **Guidance for this photo**, type `Dark Green`, then **Ctrl+A**, **Ctrl+C**, **End**, **Ctrl+V**: the field shows `Dark GreenDark Green`. Clear it. Don't click Generate.
    `[ ] Pass  [ ] Fail`

## 14. Ctrl+Z and Ctrl+Q (1 min) — free

28. In **Crop**, open a photo in `$T\batch` and click 3 corners.
    a. **Ctrl+Z** removes the last corner (2 left). **Ctrl+Q** removes all corners. The shortcut strip under the photo shows **Ctrl Z undo point** and **Ctrl Q clear points**.
    b. Click 2 corners, then click in the folder path box at the top and type `x`. **Ctrl+Z** undoes the typing in the box only (the 2 corners stay); **Ctrl+Q** in the box does nothing to the corners. Delete the `x`.
    `[ ] Pass  [ ] Fail`
    c. **Save after navigating (data-safety check):** in `$T\batch` open a photo, click 4 corners, but **don't** save. Make a subfolder with its own photo and hash it: `New-Item -ItemType Directory "$T\batch\sub" -Force; Copy-Item $P "$T\batch\sub\photo1.jpg"; Get-ChildItem "$T\batch\sub" -Recurse -File | Get-FileHash -Algorithm SHA256 | Format-Table Hash,Path > "$T\sub-before.txt"`. Click **Refresh**, double-click `sub` in the folder list, then click **Crop & save** (or press Enter). Then go back with **Parent** and click **Crop & save** again.
    ```powershell
    Get-ChildItem "$T\batch\sub" -Recurse -File | Get-FileHash -Algorithm SHA256 | Format-Table Hash,Path > "$T\sub-after.txt"; Compare-Object (Get-Content "$T\sub-before.txt") (Get-Content "$T\sub-after.txt")
    ```
    **Expected:** no output from `Compare-Object` (nothing in `sub` was cropped, moved or created). The editor closes or shows nothing to save after you navigate. Any new or changed file in `sub` is a Fail.
    `[ ] Pass  [ ] Fail`

## 15. Restart with a remembered folder (2 min) — free

29. Open a folder with many photos (ideally a **copy** of a big batch, 400+ photos) with **Open folder**, so it's remembered. Close the browser tab, press Ctrl+C in the server window, run `python server.py` again and open the app.
    **Expected:** **no grey tiles** and no `outside the allowed folder` message. The middle panel says `Cropper needs you to pick this folder again before it can open your photos.`, shows the remembered path under it, and the button reads **Browse…**. The photo list is empty and the folder/photo counts show 0. Wait 10 seconds: nothing flickers and no toasts repeat.
    a. Click **Browse…** and pick the same folder in the Windows dialog. **Expected:** the grid fills, thumbnails load, and a crop + **Crop & save** works (the `_c` file appears where step 8 put it, or in your output folder if one is set).
    b. Repeat the restart, but this time rename the remembered folder in Explorer before you open the app. **Expected:** the same empty state with the old path. **Browse…** → pick a **different** folder: that folder opens normally.
    `[ ] Pass  [ ] Fail`

## 16. Folder dialog under load (2 min) — free

30. With Cropper running and a folder open, stress the folder dialog while other requests are in flight.
    a. In a second PowerShell window, hammer the server (adjust the port if yours is not 8767):
    ```powershell
    $base='http://127.0.0.1:8767'
    $s=(Invoke-RestMethod "$base/api/session"); $t=$s.token
    1..200 | ForEach-Object {
      Invoke-RestMethod "$base/api/folder-status?path=$([uri]::EscapeDataString("$T\batch"))" -Headers @{'X-Cropper-Token'=$t; 'X-Cropper-Tab'=('load'+$_.ToString().PadLeft(28,'0'))} | Out-Null
      Invoke-RestMethod "$base/api/settings" -Headers @{'X-Cropper-Token'=$t; 'X-Cropper-Tab'=('load'+$_.ToString().PadLeft(28,'0'))} | Out-Null
    }
    ```
    b. While that loop is running (or run it a few times), click **Open folder** about **10 times**. For each: Cancel once, then pick `$T\batch` again (or Cancel most times and pick once or twice). Also try opening the dialog from a second browser tab while the first dialog is still open: the second should be refused until the first closes.
    **Expected:** Cropper does **not** crash or freeze. The server window shows no Tcl / `main thread` / `Tcl_AsyncDelete` errors. Exactly one dialog at a time. After the last pick, the folder opens and a crop still works.
    `[ ] Pass  [ ] Fail`

## 17. Non-ASCII folder path (2 min) — free

31. Make a folder with non-ASCII characters, e.g. `$T\Fotos Müller\写真 ñ é` (and optionally `$T\相册 🎉`), copy 2–3 JPGs into it, then **Open folder** and pick that folder in the Windows dialog.
    **Expected:** the grid lists the photos, thumbnails load, and **Crop & save** writes `_c` into that folder's `1.5. TBP Cropped` (path still shows the non-ASCII name). Close the browser and restart the server; the app should show the "pick this folder again" empty state with the remembered non-ASCII path; **Browse…** and re-pick it restores the grid.
    `[ ] Pass  [ ] Fail`

## 18. Single-key crop shortcuts (2 min) — free (PR #15 and later)

32. In **Crop**, open a photo in `$T\batch`.
    a. Press **P**: an outline appears. Press **P** again: the next outline (or the same one if only one was found); it wraps around. Hold **P** down: no flood of requests or flicker.
    b. Press **W**: the whole image is selected. Press **P** then immediately **W**: the whole-image selection stays.
    c. Change **Output proportions**, then press **P**: it still predicts. Tick **Auto-next**, then press **←/→**: the image changes (the proportions do not).
    d. Click in the folder path box and type `pw`: only the box changes. Open **Settings** and type `p` and `w` in a text field: nothing happens in the editor. Close Settings.
    e. Shift+P and Ctrl+P do not predict (Ctrl+P may open the print dialog; cancel it).
    f. The shortcut strip and button tooltips show the current keys (P, W, and the letters added by later PRs: C, Z, R, A/D).
    `[ ] Pass  [ ] Fail`

## Cleanup (1 min)

If step 24 or 25e stopped part-way, run `icacls "$env:USERPROFILE\Desktop\CropperTest\ro-out" /remove:d "$env:USERNAME"; icacls "$env:USERPROFILE\Desktop\CropperTest" /remove:d "$env:USERNAME"` first, or the folder can't be deleted.
Also remove step 23's folders: `Remove-Item -LiteralPath "$env:USERPROFILE\Desktop\CropperTest","$env:USERPROFILE\Desktop\CropperTest-out","$env:USERPROFILE\Desktop\CropperTest-back" -Recurse -Force -ErrorAction SilentlyContinue`

```powershell
cmdkey /delete:Cropper   # or Settings → Clear key; check with: cmdkey /list | Select-String Cropper
Remove-Item -Recurse -Force "\\?\$env:USERPROFILE\Desktop\Cropper Smoke"
```
Stop the server with Ctrl+C. Leave `%APPDATA%\Cropper` unless you want a fresh start (deleting it removes your settings).
If `cmdkey /delete:Cropper` says "element not found", use **Clear key** or delete the `Cropper` entry in Credential Manager (the entry is named `Cropper`).

## What to send back

- Failed step numbers, each with one line on what happened.
- A screenshot of each failure, including any error toast and the Settings panel.
- Log excerpts: the server console lines around each failure. **Delete any API key text first.** Note text shouldn't be there anyway.
- `git log --oneline -1` output, plus your Windows version (`winver`).
