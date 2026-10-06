# Local reference-caption OCR

OCR is optional and local. Manual reference text remains available without it. Install **Tesseract 5.5.3**, then explicitly prepare the English/Arabic fast assets. Recognition never downloads anything.

## Windows setup (once)

Use the [official 5.5.3 release](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3). The release installer is `tesseract-ocr-w64-setup-5.5.3.20260724.exe`, SHA-256 `bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4` (26,573,224 bytes). Check the downloaded file against [the manifest](../backend/ocr_assets.json), then install into a **new dedicated directory**, such as `C:\Projects\Reframe\.tools\ocr-engine`. Do not choose the repository root or a directory holding unrelated files: the installer’s uninstaller owns its installation directory.

```powershell
Get-FileHash .\tesseract-ocr-w64-setup-5.5.3.20260724.exe -Algorithm SHA256
# After comparing the hash above, install into the new dedicated directory.
Start-Process .\tesseract-ocr-w64-setup-5.5.3.20260724.exe -ArgumentList '/S','/D=C:\Projects\Reframe\.tools\ocr-engine' -Wait
.\backend\.venv\Scripts\python.exe backend\caption_ocr.py setup-data
.\backend\.venv\Scripts\python.exe backend\caption_ocr.py status
```

For another installation path, set `REFRAME_OCR_TESSERACT` to its executable before starting the backend. On POSIX, install/build the same 5.5.3 release and expose its executable on PATH (or use that variable); run `python caption_ocr.py setup-data` from the backend directory. Different engine versions report unavailable rather than silently changing the method. Windows CLI integration is independent of Python wheel/ABI compatibility and was checked with Python 3.12.14. Linux ordinary CI exercises mocked boundaries, not installed OCR.

English `eng.traineddata` (4,113,088 bytes) and Arabic `ara.traineddata` (1,432,056 bytes) use official `tesseract-ocr/tessdata_fast` commit **87416418657359cb625c412a48b6e1d6d41c29bd** with manifest-pinned SHA-256 hashes. Setup uses verified atomic replacements, preserving existing valid assets. Engine/assets are ignored under `.tools`; no downloaded recognition assets enter Git or CI. Tesseract and these assets use [Apache 2.0](https://github.com/tesseract-ocr/tessdata_fast/blob/87416418657359cb625c412a48b6e1d6d41c29bd/LICENSE). Installation is explicit; runtime/API code never invokes setup.

## Method and limits

`GET /api/projects/{UUID}/caption-ocr` checks the local pinned capability without decoding or recognizing. Explicit `POST` accepts retained-reference operation/hash, source seconds, normalized crop, light/dark polarity, English/Arabic/combined language, and capability token. It reuses source hashing, rotation/SAR-normalized frame extraction and the shared preview worker. Method **tesseract-fast-block-v1** makes one grayscale/polarity preprocessing pass, upscales at most 2×, adds a 10-pixel white border and invokes `--oem 1 --psm 6 --dpi 300` with explicit pinned data directory. Combined mode uses `eng+ara`, English first; ordering can affect results. No exhaustive search, whole-video scan, OCR while dragging or calibrated confidence claim.

Bounds: crop at least 8 decoded pixels per axis; prepared image **960-pixel maximum edge** including border and **1 MiB** PGM; one preprocessing/recognition attempt; **15-second recognition stage**, capped by the existing **30-second overall deadline**; **16 KiB captured recognition output**, **8 KiB version output**, **1,024 returned characters**, existing **16 MiB aggregate staging watchdog** (polling allows overshoot, not a quota). Limits fail explicitly, with no silently partial text. Existing owned process-tree containment, cancellation/joins, staging cleanup/quarantine and source revalidation before publication are reused. Raw error logs are withheld.

Proposals are transient, plain text with sensible engine line order retained; no score is invented. Empty/non-text/control-corrupt results fail usefully. Text exceeding matching’s **80 characters / two lines / four letters or numbers** can be reviewed/corrected before applying, never silently truncated. Proposals bind project/reference/hash, requested/selected time, crop, language/polarity, method, engine binary/version and assets digest. A backend-session signature prevents altered/expired proposal bindings. Explicit `/caption-ocr/apply` revalidates current source/capability and reviewed-text bounds; it returns only text, writes no captions/settings/selections, and starts no font comparison. Backend restart expires proposals. Applied text persists only when explicitly used in the existing saved font comparison.

Official references: [installation](https://tesseract-ocr.github.io/tessdoc/Installation.html), [fast assets/LSTM compatibility](https://tesseract-ocr.github.io/tessdoc/Data-Files.html), [command-line language/segmentation options](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html), [polarity/borders and quality limitations](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html). Complex effects, backgrounds, small/compressed text and Arabic shaping/punctuation can mislead OCR. Always review exact words, capitalization, line order and punctuation before matching fonts.
