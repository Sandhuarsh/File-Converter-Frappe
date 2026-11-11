"""Server side of the drag-and-drop converter.

Conversions are described by a small registry (``REGISTRY``). Each entry says
which source extensions it reads, which targets it can write, whether its
engine is available on this server right now, and the function that does the
work. Only available conversions are ever offered to the user.

Engines
* Pillow            - image <-> image, image -> PDF
* PyMuPDF / poppler - PDF -> PNG/JPG (first page), PDF -> TXT (PyMuPDF)
* openpyxl / xlrd   - spreadsheet <-> CSV / JSON
* PyYAML            - JSON <-> YAML (safe loader only)
* markdown2 + wkhtmltopdf - Markdown / HTML / text -> HTML / PDF
* LibreOffice       - Word / Excel / PowerPoint (if installed)
* FFmpeg            - audio and video (if installed)
"""

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile

import frappe
from frappe import _
from frappe.utils import cint

DEFAULT_MAX_MB = 25
DATA_MAX_MB = 10  # text / data formats are parsed in memory
TIMEOUT_SECONDS = 180
DATA_EXTS = {"csv", "json", "yaml", "xlsx", "xls", "md", "html", "txt"}

PILLOW_FORMAT = {
	"png": "PNG",
	"jpg": "JPEG",
	"webp": "WEBP",
	"bmp": "BMP",
	"tiff": "TIFF",
	"pdf": "PDF",
	"gif": "GIF",
	"ico": "ICO",
}

_ALIASES = {
	"jpeg": "jpg",
	"tif": "tiff",
	"htm": "html",
	"yml": "yaml",
	"markdown": "md",
	"heif": "heic",
}


def _norm(ext):
	ext = (ext or "").lower().lstrip(".")
	return _ALIASES.get(ext, ext)


# --------------------------------------------------------------------------
# engine detection
# --------------------------------------------------------------------------


def _soffice():
	return shutil.which("soffice") or shutil.which("libreoffice")


def _ffmpeg():
	return bool(shutil.which("ffmpeg"))


def _pymupdf():
	try:
		import pymupdf  # noqa: F401

		return True
	except Exception:
		try:
			import fitz  # noqa: F401

			return True
		except Exception:
			return False


def _pdf_raster():
	return _pymupdf() or bool(shutil.which("pdftoppm"))


def _wkhtml():
	return bool(shutil.which("wkhtmltopdf"))


def _heif():
	try:
		import pillow_heif  # noqa: F401

		return True
	except Exception:
		return False


def _always():
	return True


# --------------------------------------------------------------------------
# converters: fn(src, target, in_path, out_dir, tmp) -> output path
# --------------------------------------------------------------------------


def _flatten_alpha(img):
	from PIL import Image

	rgba = img.convert("RGBA")
	background = Image.new("RGB", rgba.size, (255, 255, 255))
	background.paste(rgba, mask=rgba.split()[-1])
	return background


def _convert_image(src, target, in_path, out_dir, tmp):
	from PIL import Image

	if src == "heic":
		import pillow_heif

		pillow_heif.register_heif_opener()

	out_path = os.path.join(out_dir, "output." + target)
	with Image.open(in_path) as img:
		img.load()
		if img.mode in ("CMYK", "YCbCr", "LAB", "HSV", "I", "I;16", "F"):
			img = img.convert("RGB")
		if target in ("jpg", "pdf", "bmp"):
			if img.mode in ("RGBA", "LA", "P"):
				img = _flatten_alpha(img)
			elif img.mode != "RGB":
				img = img.convert("RGB")
		if target == "ico":
			img = img.convert("RGBA")
			img.thumbnail((256, 256))
			img.save(out_path, "ICO", sizes=[img.size])
		else:
			img.save(out_path, PILLOW_FORMAT[target])
	return out_path


def _open_pdf(in_path):
	try:
		import pymupdf as fitz
	except Exception:
		import fitz

	doc = fitz.open(in_path)
	if doc.needs_pass:
		doc.close()
		frappe.throw(_("This PDF is password protected"))
	return doc


def _convert_pdf(src, target, in_path, out_dir, tmp):
	out_path = os.path.join(out_dir, "output." + target)

	if target == "txt":
		doc = _open_pdf(in_path)
		try:
			text = "\n\f\n".join(page.get_text() for page in doc)
		finally:
			doc.close()
		with open(out_path, "w", encoding="utf-8") as fh:
			fh.write(text)
		return out_path

	if _pymupdf():
		doc = _open_pdf(in_path)
		try:
			if not doc.page_count:
				frappe.throw(_("This PDF has no pages"))
			pix = doc[0].get_pixmap(dpi=150)
			pix.save(out_path)
		finally:
			doc.close()
		return out_path

	prefix = os.path.join(out_dir, "page")
	flag = "-png" if target == "png" else "-jpeg"
	_exec(["pdftoppm", flag, "-singlefile", "-r", "150", in_path, prefix])
	return prefix + "." + target


def _read_text(in_path):
	with open(in_path, "rb") as fh:
		return fh.read().decode("utf-8-sig", errors="replace")


def _write_text(out_dir, target, text):
	out_path = os.path.join(out_dir, "output." + target)
	with open(out_path, "w", encoding="utf-8", newline="") as fh:
		fh.write(text)
	return out_path


def _rows_from_csv(in_path):
	return list(csv.reader(io.StringIO(_read_text(in_path))))


def _rows_from_xlsx(in_path):
	from openpyxl import load_workbook

	wb = load_workbook(in_path, read_only=True, data_only=True)
	try:
		ws = wb.worksheets[0]
		return [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)]
	finally:
		wb.close()


def _rows_from_xls(in_path):
	import xlrd

	book = xlrd.open_workbook(in_path)
	sheet = book.sheet_by_index(0)
	return [sheet.row_values(i) for i in range(sheet.nrows)]


def _rows_from_json(in_path):
	data = json.loads(_read_text(in_path))
	if isinstance(data, dict):
		data = [data]
	if not isinstance(data, list):
		frappe.throw(_("JSON must be a list of objects or a single object"))
	keys = []
	for item in data:
		if isinstance(item, dict):
			for k in item:
				if k not in keys:
					keys.append(k)
	if not keys:
		return [[json.dumps(x, ensure_ascii=False)] for x in data]
	rows = [keys]
	for item in data:
		item = item if isinstance(item, dict) else {}
		rows.append(
			[
				json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else v)
				for v in (item.get(k) for k in keys)
			]
		)
	return rows


def _rows_to_csv(rows, out_dir):
	buf = io.StringIO()
	csv.writer(buf).writerows(rows)
	return _write_text(out_dir, "csv", buf.getvalue())


def _rows_to_xlsx(rows, out_dir):
	from openpyxl import Workbook

	wb = Workbook(write_only=False)
	ws = wb.active
	for row in rows:
		ws.append(list(row))
	# Never let imported text turn into a live formula.
	for row in ws.iter_rows():
		for cell in row:
			if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
				cell.data_type = "s"
	out_path = os.path.join(out_dir, "output.xlsx")
	wb.save(out_path)
	return out_path


def _rows_to_json(rows, out_dir):
	if not rows:
		return _write_text(out_dir, "json", "[]")
	header = [str(h) if h != "" else f"column_{i + 1}" for i, h in enumerate(rows[0])]
	records = [dict(zip(header, row)) for row in rows[1:]]
	return _write_text(out_dir, "json", json.dumps(records, ensure_ascii=False, indent=2, default=str))


def _convert_data(src, target, in_path, out_dir, tmp):
	import yaml

	if src == "json" and target == "yaml":
		data = json.loads(_read_text(in_path))
		return _write_text(
			out_dir, "yaml", yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
		)
	if src == "yaml" and target == "json":
		data = yaml.safe_load(_read_text(in_path))
		return _write_text(out_dir, "json", json.dumps(data, ensure_ascii=False, indent=2, default=str))

	readers = {"csv": _rows_from_csv, "xlsx": _rows_from_xlsx, "xls": _rows_from_xls, "json": _rows_from_json}
	rows = readers[src](in_path)
	if target == "csv":
		return _rows_to_csv(rows, out_dir)
	if target == "xlsx":
		return _rows_to_xlsx(rows, out_dir)
	if target == "json":
		return _rows_to_json(rows, out_dir)
	frappe.throw(_("Unsupported data conversion"))


_PDF_CSS = (
	"body{font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;margin:0}"
	"pre{white-space:pre-wrap;word-wrap:break-word;font-family:monospace}"
	"table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px}"
	"img{max-width:100%}"
)


def _sanitize_html(html):
	"""Strip anything that could make wkhtmltopdf read local files or call
	internal URLs (SSRF) when rendering user-supplied HTML."""
	from bs4 import BeautifulSoup

	soup = BeautifulSoup(html, "html.parser")
	for tag in soup(["script", "iframe", "object", "embed", "link", "meta", "base", "style", "form", "frame"]):
		tag.decompose()
	for tag in soup.find_all(True):
		for attr in list(tag.attrs):
			value = tag.attrs[attr]
			value = " ".join(value) if isinstance(value, list) else str(value)
			if attr.lower().startswith("on") or attr.lower() == "style":
				del tag.attrs[attr]
			elif attr.lower() in ("src", "srcset", "href", "poster", "background", "data", "action", "formaction"):
				v = value.strip().lower()
				if tag.name == "a" and attr.lower() == "href":
					continue  # links are inert in a PDF
				if not v.startswith("data:image/"):
					del tag.attrs[attr]
	return str(soup)


def _html_to_pdf(html, out_dir):
	html = (
		"<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
		+ _PDF_CSS
		+ "</style></head><body>"
		+ _sanitize_html(html)
		+ "</body></html>"
	)
	out_path = os.path.join(out_dir, "output.pdf")
	try:
		subprocess.run(
			[
				"wkhtmltopdf",
				"--quiet",
				"--disable-local-file-access",
				"--disable-javascript",
				"--encoding",
				"utf-8",
				"-",
				out_path,
			],
			input=html.encode("utf-8"),
			check=True,
			capture_output=True,
			timeout=TIMEOUT_SECONDS,
		)
	except subprocess.TimeoutExpired:
		frappe.throw(_("Conversion timed out"))
	except subprocess.CalledProcessError as e:
		# wkhtmltopdf exits non-zero for harmless network warnings; accept output if present.
		if not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
			frappe.log_error(
				title="File Converter failure",
				message=f"wkhtmltopdf stderr:\n{e.stderr.decode(errors='replace')[-2000:]}",
			)
			frappe.throw(_("Conversion failed. See Error Log for details."))
	return out_path


def _md_to_html(text):
	import markdown2

	return markdown2.markdown(
		text, safe_mode="escape", extras=["tables", "fenced-code-blocks", "strike", "task_list"]
	)


def _convert_text(src, target, in_path, out_dir, tmp):
	text = _read_text(in_path)

	if src == "md":
		html = _md_to_html(text)
	elif src == "html":
		html = text
	else:  # txt
		html = "<pre>" + frappe.utils.escape_html(text) + "</pre>"

	if target == "pdf":
		return _html_to_pdf(html, out_dir)
	if target == "html":
		if src == "html":
			return _write_text(out_dir, "html", text)
		return _write_text(
			out_dir,
			"html",
			"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>" + html + "</body></html>",
		)
	if target == "txt":
		from bs4 import BeautifulSoup

		return _write_text(out_dir, "txt", BeautifulSoup(html, "html.parser").get_text("\n").strip())
	frappe.throw(_("Unsupported text conversion"))


def _convert_libreoffice(src, target, in_path, out_dir, tmp):
	profile = os.path.join(tmp, "lo_profile")
	os.makedirs(profile, exist_ok=True)
	_exec(
		[
			_soffice(),
			"--headless",
			"--norestore",
			"-env:UserInstallation=file://" + profile,
			"--convert-to",
			target,
			"--outdir",
			out_dir,
			in_path,
		]
	)
	out_path = os.path.join(out_dir, "input." + target)
	if not os.path.exists(out_path):
		frappe.throw(_("Conversion produced no output"))
	return out_path


AUDIO_IN = ["mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"]
AUDIO_OUT = ["mp3", "wav", "flac", "ogg", "m4a"]
VIDEO_IN = ["mp4", "mov", "mkv", "webm", "avi"]


def _convert_ffmpeg(src, target, in_path, out_dir, tmp):
	out_path = os.path.join(out_dir, "output." + target)
	cmd = ["ffmpeg", "-y", "-i", in_path]
	if src in VIDEO_IN and target in AUDIO_OUT:
		cmd.append("-vn")
	if target == "mp4":
		cmd += ["-movflags", "+faststart"]
	cmd.append(out_path)
	_exec(cmd)
	return out_path


def _exec(cmd):
	try:
		subprocess.run(
			cmd,
			check=True,
			capture_output=True,
			timeout=TIMEOUT_SECONDS,
			stdin=subprocess.DEVNULL,
		)
	except subprocess.TimeoutExpired:
		frappe.throw(_("Conversion timed out"))
	except subprocess.CalledProcessError as e:
		frappe.log_error(
			title="File Converter failure",
			message=f"cmd: {cmd}\n\nstderr:\n{e.stderr.decode(errors='replace')[-2000:]}",
		)
		frappe.throw(_("Conversion failed. See Error Log for details."))


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

IMAGE_IN = ["jpg", "png", "webp", "bmp", "gif", "tiff", "ico"]
IMAGE_OUT = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "pdf"]

REGISTRY = [
	{"in": IMAGE_IN, "out": IMAGE_OUT, "ok": _always, "fn": _convert_image},
	{"in": ["heic"], "out": IMAGE_OUT, "ok": _heif, "fn": _convert_image},
	{"in": ["pdf"], "out": ["png", "jpg"], "ok": _pdf_raster, "fn": _convert_pdf},
	{"in": ["pdf"], "out": ["txt"], "ok": _pymupdf, "fn": _convert_pdf},
	{"in": ["csv"], "out": ["xlsx", "json"], "ok": _always, "fn": _convert_data},
	{"in": ["xlsx", "xls"], "out": ["csv", "json"], "ok": _always, "fn": _convert_data},
	{"in": ["xls"], "out": ["xlsx"], "ok": _always, "fn": _convert_data},
	{"in": ["json"], "out": ["csv", "xlsx", "yaml"], "ok": _always, "fn": _convert_data},
	{"in": ["yaml"], "out": ["json"], "ok": _always, "fn": _convert_data},
	{"in": ["md"], "out": ["html", "txt"], "ok": _always, "fn": _convert_text},
	{"in": ["html"], "out": ["txt"], "ok": _always, "fn": _convert_text},
	{"in": ["md", "html", "txt"], "out": ["pdf"], "ok": _wkhtml, "fn": _convert_text},
	{"in": ["txt"], "out": ["html"], "ok": _always, "fn": _convert_text},
	{"in": ["doc", "docx", "odt", "rtf", "txt", "html"], "out": ["pdf", "docx", "odt"], "ok": _soffice, "fn": _convert_libreoffice},
	{"in": ["xls", "xlsx", "ods", "csv"], "out": ["pdf", "xlsx", "ods", "csv"], "ok": _soffice, "fn": _convert_libreoffice},
	{"in": ["ppt", "pptx", "odp"], "out": ["pdf", "pptx", "odp"], "ok": _soffice, "fn": _convert_libreoffice},
	{"in": AUDIO_IN, "out": AUDIO_OUT, "ok": _ffmpeg, "fn": _convert_ffmpeg},
	{"in": VIDEO_IN, "out": ["mp4", "webm", "mkv", "mp3"], "ok": _ffmpeg, "fn": _convert_ffmpeg},
]


def _targets_for(ext):
	src = _norm(ext)
	seen, result = set(), []
	for entry in REGISTRY:
		if src in entry["in"] and entry["ok"]():
			for t in entry["out"]:
				if t != src and t not in seen:
					seen.add(t)
					result.append(t)
	return result


def _find_converter(src, target):
	for entry in REGISTRY:
		if src in entry["in"] and target in entry["out"] and entry["ok"]():
			return entry["fn"]
	return None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)


@frappe.whitelist()
def get_targets(ext=None):
	"""Return the output formats available for a source extension."""
	_require_login()
	return _targets_for(ext)


def _produce(original_name, target, save_input):
	"""Run one conversion. ``save_input(path)`` must write the source bytes.

	Returns ``(output_file_name, content_bytes)``.
	"""
	original = os.path.basename(original_name)
	stem, dot_ext = os.path.splitext(original)
	src = _norm(dot_ext)
	target = _norm(target)

	fn = _find_converter(src, target)
	if not fn:
		frappe.throw(_("Cannot convert .{0} to .{1} on this server").format(src or "?", target or "?"))

	max_mb = cint(frappe.conf.get("file_converter_max_mb")) or DEFAULT_MAX_MB
	if src in DATA_EXTS:
		max_mb = min(max_mb, DATA_MAX_MB)

	with tempfile.TemporaryDirectory(prefix="fconv_") as tmp:
		in_path = os.path.join(tmp, "input." + src)
		save_input(in_path)
		if os.path.getsize(in_path) > max_mb * 1024 * 1024:
			frappe.throw(_("File is larger than the {0} MB limit for this type").format(max_mb))

		out_dir = os.path.join(tmp, "out")
		os.makedirs(out_dir)
		try:
			out_path = fn(src, target, in_path, out_dir, tmp)
		except frappe.ValidationError:
			raise
		except Exception:
			frappe.log_error(title="File Converter failure", message=frappe.get_traceback())
			frappe.throw(_("This file could not be converted. It may be damaged or unsupported."))

		with open(out_path, "rb") as fh:
			content = fh.read()

	safe_stem = re.sub(r"[^\w\-. ]", "_", stem).strip() or "converted"
	return f"{safe_stem}.{target}", content


def _insert_file(out_name, content, attach_dt, attach_dn):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": out_name,
			"content": content,
			"is_private": 1,
			"attached_to_doctype": attach_dt,
			"attached_to_name": attach_dn,
		}
	).insert()


@frappe.whitelist(methods=["POST"])
def convert(target=None, doctype=None, docname=None):
	"""Convert the uploaded file (multipart field ``file``) to ``target``.

	If ``doctype``/``docname`` point to a document the user can write to, the
	result is attached to it. Otherwise it is saved as a private, unattached
	File owned by the user.
	"""
	_require_login()

	upload = frappe.request.files.get("file") if frappe.request else None
	if not upload or not upload.filename:
		frappe.throw(_("No file received"))

	out_name, content = _produce(upload.filename, target, upload.save)

	attach_dt = attach_dn = None
	if doctype and docname and frappe.db.exists(doctype, docname):
		if frappe.has_permission(doctype, "write", docname):
			attach_dt, attach_dn = doctype, docname

	file_doc = _insert_file(out_name, content, attach_dt, attach_dn)
	return {
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
		"attached": bool(attach_dn),
	}


@frappe.whitelist(methods=["POST"])
def convert_attachment(file=None, target=None, replace=0):
	"""Convert a file that is already attached to a document.

	``file`` is the File doc name. The result is attached to the same document
	(if the user can write to it). With ``replace`` the original File is deleted
	afterwards, but only if the user is allowed to delete it; the new file is
	always saved first so a failure never loses the original.
	"""
	_require_login()
	if not file:
		frappe.throw(_("No attachment given"))

	file_doc = frappe.get_doc("File", file)
	file_doc.check_permission("read")
	if file_doc.is_folder:
		frappe.throw(_("Folders cannot be converted"))

	def save_input(path):
		try:
			data = file_doc.get_content()
		except Exception:
			frappe.throw(_("Could not read the attachment"))
		if isinstance(data, str):
			data = data.encode("utf-8")
		with open(path, "wb") as fh:
			fh.write(data)

	out_name, content = _produce(file_doc.file_name or "", target, save_input)

	attach_dt, attach_dn = file_doc.attached_to_doctype, file_doc.attached_to_name
	if not (attach_dt and attach_dn and frappe.has_permission(attach_dt, "write", attach_dn)):
		attach_dt = attach_dn = None

	new_doc = _insert_file(out_name, content, attach_dt, attach_dn)

	replaced = False
	if cint(replace) and attach_dn and file_doc.has_permission("delete"):
		frappe.delete_doc("File", file_doc.name)
		replaced = True

	return {
		"file_url": new_doc.file_url,
		"file_name": new_doc.file_name,
		"attached": bool(attach_dn),
		"replaced": replaced,
	}


# --------------------------------------------------------------------------
# merge several images / PDFs into one PDF
# --------------------------------------------------------------------------

MERGE_SRC = {"pdf", "jpg", "png", "webp", "bmp", "gif", "tiff", "heic"}
MAX_MERGE_FILES = 30
MAX_MERGE_TOTAL_MB = 100
MAX_MERGE_PAGES = 300
A4 = (595, 842)  # points
PAGE_MARGIN = 18
IMAGE_MAX_PX = 2600  # longest side; keeps photo PDFs small


def _fitz():
	try:
		import pymupdf as fitz
	except Exception:
		import fitz
	return fitz


def _merge_to_pdf(items, tmp):
	"""Combine ``items`` [(display_name, ext, path), ...] in order into one PDF.

	PDFs are appended page by page. Each image becomes one A4 page (portrait or
	landscape to suit the picture), scaled to fit inside the margins. Phone
	photos are rotated by their EXIF orientation and downscaled.
	"""
	from PIL import Image, ImageOps

	fitz = _fitz()
	out = fitz.open()
	try:
		for i, (name, ext, path) in enumerate(items):
			if ext == "pdf":
				src = fitz.open(path)
				try:
					if src.needs_pass:
						frappe.throw(_("{0} is password protected").format(name))
					out.insert_pdf(src)
				finally:
					src.close()
			else:
				if ext == "heic":
					import pillow_heif

					pillow_heif.register_heif_opener()
				with Image.open(path) as opened:
					img = ImageOps.exif_transpose(opened)
					if img.mode in ("RGBA", "LA", "P"):
						img = _flatten_alpha(img)
					elif img.mode != "RGB":
						img = img.convert("RGB")
					img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX))
					jpg = os.path.join(tmp, f"page_{i}.jpg")
					img.save(jpg, "JPEG", quality=88)
					landscape = img.width > img.height
				w, h = (A4[1], A4[0]) if landscape else A4
				page = out.new_page(width=w, height=h)
				page.insert_image(
					fitz.Rect(PAGE_MARGIN, PAGE_MARGIN, w - PAGE_MARGIN, h - PAGE_MARGIN), filename=jpg
				)
			if out.page_count > MAX_MERGE_PAGES:
				frappe.throw(_("Too many pages (limit {0})").format(MAX_MERGE_PAGES))
		if not out.page_count:
			frappe.throw(_("Nothing to merge"))
		out_path = os.path.join(tmp, "merged.pdf")
		out.save(out_path, garbage=3, deflate=True)
		return out_path
	finally:
		out.close()


@frappe.whitelist()
def merge_available():
	"""Whether this server can merge files into one PDF."""
	_require_login()
	return _pymupdf()


def _merge_sources(sources):
	"""Merge ``sources`` [(display_name, save_fn(path)), ...] in order.

	Applies the count, size and type limits. Returns ``(out_name, content, count)``.
	"""
	if not _pymupdf():
		frappe.throw(_("PDF merging is not available on this server"))
	if len(sources) < 2:
		frappe.throw(_("Select at least two files to merge"))
	if len(sources) > MAX_MERGE_FILES:
		frappe.throw(_("You can merge up to {0} files at once").format(MAX_MERGE_FILES))

	max_mb = cint(frappe.conf.get("file_converter_max_mb")) or DEFAULT_MAX_MB

	with tempfile.TemporaryDirectory(prefix="fmerge_") as tmp:
		items, total = [], 0
		for i, (display_name, save) in enumerate(sources):
			name = os.path.basename(display_name or "") or f"file_{i + 1}"
			stem_ext = _norm(os.path.splitext(name)[1])
			if stem_ext not in MERGE_SRC:
				frappe.throw(_("{0}: only images and PDFs can be merged").format(name))
			if stem_ext == "heic" and not _heif():
				frappe.throw(_("{0}: HEIC is not supported on this server").format(name))
			path = os.path.join(tmp, f"in_{i}.{stem_ext}")
			save(path)
			size = os.path.getsize(path)
			if size > max_mb * 1024 * 1024:
				frappe.throw(_("{0} is larger than the {1} MB limit").format(name, max_mb))
			total += size
			if total > MAX_MERGE_TOTAL_MB * 1024 * 1024:
				frappe.throw(_("Total size is over the {0} MB limit").format(MAX_MERGE_TOTAL_MB))
			items.append((name, stem_ext, path))

		try:
			out_path = _merge_to_pdf(items, tmp)
		except frappe.ValidationError:
			raise
		except Exception:
			frappe.log_error(title="File Converter merge failure", message=frappe.get_traceback())
			frappe.throw(_("These files could not be merged. One of them may be damaged."))

		with open(out_path, "rb") as fh:
			content = fh.read()

	first_stem = re.sub(r"[^\w\-. ]", "_", os.path.splitext(items[0][0])[0]).strip() or "files"
	return f"merged_{first_stem}.pdf", content, len(items)


def _attach_target(doctype, docname):
	"""(doctype, docname) if the user may attach to that document, else (None, None)."""
	if doctype and docname and frappe.db.exists(doctype, docname):
		if frappe.has_permission(doctype, "write", docname):
			return doctype, docname
	return None, None


@frappe.whitelist(methods=["POST"])
def merge_pdf(doctype=None, docname=None):
	"""Merge the uploaded images/PDFs (multipart field ``files``, in order)
	into a single PDF and attach it like :func:`convert` does."""
	_require_login()
	uploads = [u for u in (frappe.request.files.getlist("files") if frappe.request else []) if u and u.filename]
	out_name, content, count = _merge_sources([(u.filename, u.save) for u in uploads])

	attach_dt, attach_dn = _attach_target(doctype, docname)
	file_doc = _insert_file(out_name, content, attach_dt, attach_dn)
	return {
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
		"attached": bool(attach_dn),
		"files": count,
	}
