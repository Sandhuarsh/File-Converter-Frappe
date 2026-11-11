# File Converter (Frappe / ERPNext v15)

Drag-and-drop file conversion on every ERPNext form, with a Filee-style donut menu.

Hold **Ctrl** (Cmd on Mac), drag a file over any form, and drop it onto a format segment.
The converted file is attached to the document (or offered as a download on unsaved forms).
Without Ctrl, ERPNext's normal attachment drop is unchanged. Every form's **...** menu also has
**Convert File...** as a fallback.

## Conversions

Only conversions whose engine exists on the server are offered.

| From | To | Engine |
|---|---|---|
| PNG, JPG, WEBP, BMP, GIF, TIFF, ICO | each other, PDF | Pillow |
| HEIC | images, PDF | pillow-heif (optional) |
| PDF | PNG, JPG (first page), TXT | PyMuPDF or poppler |
| CSV, XLSX, XLS, JSON, YAML | each other (see `REGISTRY` in `api.py`) | openpyxl, xlrd, PyYAML |
| Markdown, HTML, TXT | HTML, TXT, PDF | markdown2, wkhtmltopdf |
| DOC/DOCX/ODT/RTF, XLS/XLSX/ODS, PPT/PPTX/ODP | PDF and office formats | LibreOffice (optional) |
| Audio, video | other audio/video, MP3 extract | FFmpeg (optional) |

Add a converter by appending an entry to `REGISTRY` in `file_converter/api.py`.

## Install (one site only)

```bash
cd ~/frappe-bench
bench get-app file_converter <this-repo-url>
bench --site <site> install-app file_converter
bench build --app file_converter
bench --site <site> clear-cache
# restart this bench's web process (supervisor) so the new module is importable
```

HEIC (iPhone photos) needs `pillow-heif`. On an existing bench install it without letting pip
upgrade Pillow or other Frappe pins:

```bash
~/frappe-bench/env/bin/pip install --no-deps "pillow-heif==0.21.0"
```

## Attachments

Every attachment in a form's sidebar gets a small convert button. It opens the same donut;
tick **Replace original** to delete the source file after the converted one is saved
(needs delete permission on the File; the new file is always saved first).

Optional engines (server-wide, need sudo):

```bash
sudo apt-get install -y libreoffice-writer libreoffice-calc libreoffice-impress ffmpeg poppler-utils
```

## Configuration

* `file_converter_max_mb` in `site_config.json` - upload limit in MB (default 25; text/data formats are capped at 10).
* Bump the `?v=` in `hooks.py` on each release so browsers fetch the new JS/CSS.

## Security notes

* Login required; attaching to a document requires write permission on it.
* HTML/Markdown to PDF strips scripts, iframes, and non-`data:` image sources, and runs wkhtmltopdf with local file access and JavaScript disabled.
* Spreadsheet text starting with `=`, `+`, `-`, `@` is stored as text, not a formula.
* Conversions run synchronously with a 180 s timeout.

## Merge to one PDF

Drop two or more images and/or PDFs (Ctrl + drag, or the form's **Convert File...** menu) and pick the
green **Merge to 1 PDF** segment. Files are added in the order they were dropped. Each image becomes one
A4 page (portrait or landscape to suit the photo, EXIF rotation applied, downscaled to keep the PDF small);
PDFs are appended page by page. Limits: 30 files, 100 MB total, 300 pages, password-protected PDFs rejected.
Requires PyMuPDF on the server.

## Merge existing attachments

With two or more image/PDF attachments on a form, each attachment row gets a checkbox. Tick the files (the
order you tick is the page order); a bar appears under the list with **Merge to 1 PDF** and an optional
**Replace originals** (off by default; the merged PDF is saved first, and only files the user may delete
are removed). Server method: `file_converter.api.merge_attachments`.
