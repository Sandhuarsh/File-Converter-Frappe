/* File Converter
 * Drag files onto ANY form while holding Ctrl (Cmd on Mac). A donut of
 * target formats opens at the cursor; drop onto a segment and the converted
 * file is attached to the document (or offered as a download on unsaved
 * forms). Without the modifier key, ERPNext's normal attachment drop is
 * untouched. The form's "..." menu also has "Convert File..." as a fallback. */
(function () {
	"use strict";

	const API = "file_converter.api.";
	const REQUIRE_MODIFIER = true;
	const MAX_SEGMENTS = 12;

	/* Browsers expose only the MIME type (not the name) while dragging. */
	const MIME_EXT = {
		"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/bmp": "bmp",
		"image/gif": "gif", "image/tiff": "tiff", "image/heic": "heic", "image/heif": "heic",
		"image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
		"application/pdf": "pdf",
		"application/msword": "doc",
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
		"application/vnd.ms-excel": "xls",
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
		"application/vnd.ms-powerpoint": "ppt",
		"application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
		"text/csv": "csv", "application/json": "json", "text/plain": "txt",
		"text/markdown": "md", "text/html": "html",
		"application/x-yaml": "yaml", "text/yaml": "yaml",
		"audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac",
		"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac",
		"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
		"video/x-matroska": "mkv", "video/x-msvideo": "avi",
	};

	let donut = null; // { el, mode }
	let hint = null;
	let token = 0; // invalidates in-flight async work when a drag ends
	let starting = false;
	let lastX = 0;
	let lastY = 0;
	const targetCache = {};

	const esc = (s) => frappe.utils.escape_html(String(s));
	const getExt = (name) => {
		const m = /\.([^.]+)$/.exec(name || "");
		return m ? m[1].toLowerCase() : "";
	};
	const onForm = () =>
		window.cur_frm && cur_frm.doc && frappe.get_route && frappe.get_route()[0] === "Form";
	const hasFiles = (e) =>
		e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
	const modifier = (e) => !REQUIRE_MODIFIER || e.ctrlKey || e.metaKey;

	/* ---------- targets ---------- */
	async function targetsFor(ext) {
		if (!targetCache[ext]) {
			targetCache[ext] = frappe.xcall(API + "get_targets", { ext }).catch((err) => {
				delete targetCache[ext];
				throw err;
			});
		}
		return targetCache[ext];
	}

	async function commonTargets(exts) {
		const unique = Array.from(new Set(exts));
		const lists = await Promise.all(unique.map(targetsFor));
		return lists.reduce((a, b) => a.filter((t) => b.includes(t))).slice(0, MAX_SEGMENTS);
	}

	/* Several images / PDFs can also be merged into one PDF. */
	const MERGE_EXTS = new Set([
		"pdf", "jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif",
	]);
	let mergeOk = null;
	async function mergeAvailable() {
		if (mergeOk === null) {
			try {
				mergeOk = !!(await frappe.xcall(API + "merge_available"));
			} catch (e) {
				mergeOk = false;
			}
		}
		return mergeOk;
	}

	/* Donut segments for these extensions (one entry per dropped file). */
	async function donutTargets(exts) {
		const targets = await commonTargets(exts);
		if (exts.length >= 2 && exts.every((e) => MERGE_EXTS.has(e)) && (await mergeAvailable())) {
			targets.unshift("merge");
		}
		return targets.slice(0, MAX_SEGMENTS);
	}

	function extsFromItems(items) {
		const exts = [];
		for (const it of Array.from(items || [])) {
			if (it.kind !== "file") continue;
			const x = MIME_EXT[(it.type || "").toLowerCase()];
			if (!x) return null; // unknown type: fall back to choosing after the drop
			exts.push(x);
		}
		return exts.length ? exts : null;
	}

	/* ---------- hint pill ---------- */
	function showHint(active) {
		if (!hint) {
			hint = document.createElement("div");
			hint.className = "fc-hint";
			document.body.appendChild(hint);
		}
		hint.textContent = active
			? __("Drop to convert")
			: __("Hold Ctrl and drop to convert (release Ctrl to attach normally)");
		hint.classList.toggle("fc-hint-idle", !active);
		hint.style.display = "block";
	}
	function hideHint() {
		if (hint) hint.style.display = "none";
	}

	/* ---------- donut ---------- */
	const SIZE = 260;
	const R = 118; // outer radius
	const r = 50; // inner radius
	const C = SIZE / 2;

	function pt(rad, ang) {
		return [C + rad * Math.cos(ang), C + rad * Math.sin(ang)];
	}

	function segmentPath(i, n) {
		if (n === 1) {
			return (
				`M${C - R} ${C}A${R} ${R} 0 1 1 ${C + R} ${C}A${R} ${R} 0 1 1 ${C - R} ${C}Z` +
				`M${C - r} ${C}A${r} ${r} 0 1 0 ${C + r} ${C}A${r} ${r} 0 1 0 ${C - r} ${C}Z`
			);
		}
		const step = (2 * Math.PI) / n;
		const gap = 0.045;
		const a0 = -Math.PI / 2 + i * step + gap / 2;
		const a1 = -Math.PI / 2 + (i + 1) * step - gap / 2;
		const large = a1 - a0 > Math.PI ? 1 : 0;
		const [x0, y0] = pt(R, a0);
		const [x1, y1] = pt(R, a1);
		const [x2, y2] = pt(r, a1);
		const [x3, y3] = pt(r, a0);
		return `M${x0} ${y0}A${R} ${R} 0 ${large} 1 ${x1} ${y1}L${x2} ${y2}A${r} ${r} 0 ${large} 0 ${x3} ${y3}Z`;
	}

	function buildDonut(targets, label, mode) {
		const n = targets.length;
		const step = (2 * Math.PI) / n;
		const mid = (R + r) / 2;
		let segs = "";
		targets.forEach((t, i) => {
			const [tx, ty] = n === 1 ? [C, C - mid] : pt(mid, -Math.PI / 2 + (i + 0.5) * step);
			const isMerge = t === "merge";
			const hue = isMerge ? 150 : (210 + (i * 360) / n) % 360;
			const label = isMerge
				? `<text x="${tx}" y="${ty - 8}">${__("Merge")}</text>` +
				  `<text class="fc-sub" x="${tx}" y="${ty + 8}">${__("to 1 PDF")}</text>`
				: `<text x="${tx}" y="${ty}">.${esc(t)}</text>`;
			segs +=
				`<g class="fc-seg" data-t="${esc(t)}" style="--fc-h:${hue}">` +
				`<path d="${segmentPath(i, n)}" fill-rule="evenodd"></path>` +
				label +
				`</g>`;
		});
		const short = label.length > 14 ? label.slice(0, 13) + "…" : label;
		const wrap = document.createElement("div");
		wrap.className = "fc-donut fc-donut-" + mode;
		wrap.innerHTML =
			`<svg width="${SIZE}" height="${SIZE}" viewBox="0 0 ${SIZE} ${SIZE}" role="menu">` +
			segs +
			`<circle class="fc-hole" cx="${C}" cy="${C}" r="${r - 4}"></circle>` +
			`<text class="fc-center" x="${C}" y="${C - 7}">${mode === "drag" ? __("Drop on") : __("Convert")}</text>` +
			`<text class="fc-center fc-center-sub" x="${C}" y="${C + 10}">${esc(mode === "drag" ? __("a format") : short)}</text>` +
			`</svg>`;
		return wrap;
	}

	function placeDonut(el, x, y) {
		document.body.appendChild(el);
		const half = SIZE / 2;
		const h = el.offsetHeight || SIZE;
		el.style.left = Math.max(4, Math.min(x - half, window.innerWidth - SIZE - 4)) + "px";
		el.style.top = Math.max(4, Math.min(y - half, window.innerHeight - h - 4)) + "px";
	}

	function closeDonut() {
		token++;
		starting = false;
		if (donut) {
			donut.el.remove();
			donut = null;
		}
		document.removeEventListener("keydown", onKey, true);
		document.removeEventListener("mousedown", onOutside, true);
	}
	const onKey = (e) => {
		if (e.key === "Escape") closeDonut();
	};
	const onOutside = (e) => {
		if (donut && !donut.el.contains(e.target)) closeDonut();
	};

	function setActive(seg) {
		if (!donut) return;
		donut.el.querySelectorAll(".fc-seg.active").forEach((s) => {
			if (s !== seg) s.classList.remove("active");
		});
		if (seg) seg.classList.add("active");
	}

	/* Drag mode: opens while dragging, user drops onto a segment. */
	async function startDragDonut(e) {
		const exts = extsFromItems(e.dataTransfer.items);
		if (!exts) return;
		starting = true;
		const my = ++token;
		let targets = [];
		try {
			targets = await donutTargets(exts);
		} catch (err) {
			starting = false;
			return;
		}
		if (my !== token) return; // drag ended meanwhile
		starting = false;
		if (!targets.length) return;
		const label =
			exts.length === 1 ? "." + exts[0] : __("{0} files", [exts.length]);
		const el = buildDonut(targets, label, "drag");
		donut = { el, mode: "drag" };
		placeDonut(el, lastX, lastY);
		hideHint();
	}

	/* Click mode: opened after a drop outside a segment, or from the menu. */
	const REPLACE_KEY = "file_converter_replace_original";
	const getReplacePref = () => {
		try {
			return localStorage.getItem(REPLACE_KEY) === "1";
		} catch (e) {
			return false;
		}
	};
	const setReplacePref = (v) => {
		try {
			localStorage.setItem(REPLACE_KEY, v ? "1" : "0");
		} catch (e) {
			/* ignore */
		}
	};

	/* `attachment` ({id, file_name}) is set when converting an existing attachment. */
	async function openClickDonut(files, x, y, attachment) {
		closeDonut();
		if (!files.length) return;
		let targets = [];
		try {
			targets = await donutTargets(files.map((f) => getExt(f.name)));
		} catch (err) {
			return;
		}
		if (!targets.length) {
			frappe.msgprint(
				__("No conversion is available for the dropped file(s) on this server.")
			);
			return;
		}
		const label = files.length === 1 ? files[0].name : __("{0} files", [files.length]);
		const el = buildDonut(targets, label, "click");
		donut = { el, mode: "click" };
		if (attachment) {
			const label = document.createElement("label");
			label.className = "fc-replace";
			label.innerHTML = `<input type="checkbox"> ${__("Replace original")}`;
			const box = label.querySelector("input");
			box.checked = getReplacePref();
			box.addEventListener("change", () => setReplacePref(box.checked));
			el.appendChild(label);
		}
		placeDonut(el, x, y);
		el.addEventListener("click", (ev) => {
			const seg = ev.target.closest(".fc-seg");
			if (!seg) return;
			const t = seg.dataset.t;
			const replace = attachment && el.querySelector(".fc-replace input").checked;
			closeDonut();
			if (attachment) runAttachmentConversion(attachment, t, replace);
			else if (t === "merge") runMerge(files);
			else runConversions(files, t);
		});
		document.addEventListener("keydown", onKey, true);
		document.addEventListener("mousedown", onOutside, true);
	}

	/* ---------- global drag handlers (capture phase) ---------- */
	document.addEventListener(
		"dragover",
		(e) => {
			if (!(onForm() && hasFiles(e))) {
				hideHint();
				return;
			}
			lastX = e.clientX;
			lastY = e.clientY;
			if (modifier(e)) {
				e.preventDefault();
				e.dataTransfer.dropEffect = "copy";
				if (donut && donut.mode === "drag") {
					setActive(e.target.closest && e.target.closest(".fc-seg"));
				} else if (!donut) {
					showHint(true);
					if (!starting) startDragDonut(e);
				}
			} else {
				if (donut && donut.mode === "drag") closeDonut();
				showHint(false);
			}
		},
		true
	);

	document.addEventListener(
		"dragleave",
		(e) => {
			if (e.relatedTarget === null) {
				hideHint();
				if (donut && donut.mode === "drag") closeDonut();
				else token++;
			}
		},
		true
	);

	document.addEventListener(
		"drop",
		(e) => {
			hideHint();
			if (!(onForm() && hasFiles(e) && modifier(e))) {
				if (donut && donut.mode === "drag") closeDonut();
				return;
			}
			e.preventDefault();
			e.stopPropagation(); // keep ERPNext's own upload dialog out of the way
			const files = Array.from(e.dataTransfer.files);
			const seg = e.target.closest && e.target.closest(".fc-seg");
			const x = e.clientX;
			const y = e.clientY;
			if (seg && donut) {
				const t = seg.dataset.t;
				closeDonut();
				if (t === "merge") runMerge(files);
				else runConversions(files, t);
			} else {
				closeDonut();
				openClickDonut(files, x, y);
			}
		},
		true
	);

	/* ---------- upload + convert ---------- */
	function serverError(data, status) {
		try {
			if (data._server_messages) {
				const msgs = JSON.parse(data._server_messages).map((m) => JSON.parse(m).message);
				return msgs.join("<br>");
			}
		} catch (e) {
			/* fall through */
		}
		return data.exception || data.exc_type || `HTTP ${status}`;
	}

	async function convertOne(file, target, frm) {
		const fd = new FormData();
		fd.append("file", file, file.name);
		fd.append("target", target);
		if (!frm.is_new()) {
			fd.append("doctype", frm.doctype);
			fd.append("docname", frm.docname);
		}
		const res = await fetch("/api/method/" + API + "convert", {
			method: "POST",
			body: fd,
			credentials: "same-origin",
			headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) throw new Error(serverError(data, res.status));
		return data.message;
	}

	async function runConversions(files, target) {
		const frm = window.cur_frm;
		const done = [];
		const failed = [];

		for (let i = 0; i < files.length; i++) {
			frappe.show_progress(
				__("Converting to .{0}", [target]),
				i,
				files.length,
				esc(files[i].name),
				true
			);
			try {
				done.push(await convertOne(files[i], target, frm));
			} catch (err) {
				failed.push({ name: files[i].name, msg: err.message });
			}
		}
		frappe.hide_progress();

		const attached = done.filter((d) => d.attached);
		const loose = done.filter((d) => !d.attached);

		if (attached.length) {
			frappe.show_alert(
				{
					message: __("{0} converted file(s) attached", [attached.length]),
					indicator: "green",
				},
				7
			);
			if (!frm.is_new()) frm.reload_doc();
		}

		let html = "";
		if (loose.length) {
			html +=
				`<p>${__("Converted (not attached to this document):")}</p><ul>` +
				loose
					.map((d) => `<li><a href="${esc(d.file_url)}" target="_blank">${esc(d.file_name)}</a></li>`)
					.join("") +
				`</ul>`;
		}
		if (failed.length) {
			html +=
				`<p class="text-danger">${__("Failed:")}</p><ul>` +
				failed.map((f) => `<li>${esc(f.name)}: ${f.msg}</li>`).join("") +
				`</ul>`;
		}
		if (html) frappe.msgprint({ title: __("File Converter"), message: html });
	}

	/* ---------- merge into one PDF ---------- */
	async function runMerge(files) {
		const frm = window.cur_frm;
		frappe.show_progress(
			__("Merging {0} files into one PDF", [files.length]),
			0,
			1,
			__("Files are added in the order they were dropped"),
			true
		);
		try {
			const fd = new FormData();
			files.forEach((f) => fd.append("files", f, f.name));
			if (!frm.is_new()) {
				fd.append("doctype", frm.doctype);
				fd.append("docname", frm.docname);
			}
			const res = await fetch("/api/method/" + API + "merge_pdf", {
				method: "POST",
				body: fd,
				credentials: "same-origin",
				headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
			});
			const data = await res.json().catch(() => ({}));
			frappe.hide_progress();
			if (!res.ok) {
				frappe.msgprint({
					title: __("File Converter"),
					indicator: "red",
					message: serverError(data, res.status),
				});
				return;
			}
			const r = data.message;
			if (r.attached) {
				frappe.show_alert(
					{ message: __("Merged PDF attached ({0} files)", [r.files]), indicator: "green" },
					7
				);
				if (!frm.is_new()) frm.reload_doc();
			} else {
				frappe.msgprint({
					title: __("File Converter"),
					message:
						`<p>${__("Merged (not attached to this document):")}</p>` +
						`<a href="${esc(r.file_url)}" target="_blank">${esc(r.file_name)}</a>`,
				});
			}
		} catch (err) {
			frappe.hide_progress();
			frappe.msgprint({ title: __("File Converter"), indicator: "red", message: esc(err.message || err) });
		}
	}

	/* ---------- convert an existing attachment ---------- */
	async function runAttachmentConversion(att, target, replace) {
		const frm = window.cur_frm;
		frappe.show_progress(__("Converting to .{0}", [target]), 0, 1, esc(att.file_name), true);
		try {
			const r = await frappe.xcall(API + "convert_attachment", {
				file: att.id,
				target: target,
				replace: replace ? 1 : 0,
			});
			frappe.hide_progress();
			frappe.show_alert(
				{
					message: r.replaced
						? __("Converted. Original attachment replaced.")
						: __("Converted file attached."),
					indicator: "green",
				},
				7
			);
			if (!frm.is_new()) frm.reload_doc();
			if (!r.attached) {
				frappe.msgprint({
					title: __("File Converter"),
					message: `<a href="${esc(r.file_url)}" target="_blank">${esc(r.file_name)}</a>`,
				});
			}
		} catch (err) {
			frappe.hide_progress(); // the server message is shown by frappe.xcall
		}
	}

	function convertAttachmentClick(frm, id, x, y) {
		const att = ((frm.attachments && frm.attachments.get_attachments()) || []).find(
			(a) => a.name === id
		);
		if (!att || !att.file_name) return;
		openClickDonut([{ name: att.file_name }], x, y, { id: att.name, file_name: att.file_name });
	}

	/* ---------- merge several existing attachments ---------- */
	const MERGE_REPLACE_KEY = "file_converter_merge_replace";
	let selected = []; // File ids, in the order they were ticked
	let selectedDoc = null;

	const attachmentNames = (frm) => {
		const m = {};
		((frm.attachments && frm.attachments.get_attachments()) || []).forEach((a) => {
			m[a.name] = a.file_name || "";
		});
		return m;
	};
	const mergeable = (name) => MERGE_EXTS.has(getExt(name));

	function toggleSelected(id, on) {
		selected = selected.filter((x) => x !== id);
		if (on) selected.push(id);
		decorateAttachments();
	}

	async function runMergeAttachments(replace) {
		const frm = window.cur_frm;
		const ids = selected.slice();
		if (ids.length < 2 || !frm) return;
		if (!(await mergeAvailable())) {
			frappe.msgprint(__("PDF merging is not available on this server."));
			return;
		}
		frappe.show_progress(__("Merging {0} attachments into one PDF", [ids.length]), 0, 1, "", true);
		try {
			const r = await frappe.xcall(API + "merge_attachments", {
				files: JSON.stringify(ids),
				doctype: frm.doctype,
				docname: frm.docname,
				replace: replace ? 1 : 0,
			});
			frappe.hide_progress();
			selected = [];
			frappe.show_alert(
				{
					message: r.deleted
						? __("Merged {0} files into one PDF. Originals removed.", [r.files])
						: __("Merged {0} files into one PDF.", [r.files]),
					indicator: "green",
				},
				7
			);
			if (!frm.is_new()) frm.reload_doc();
			if (!r.attached) {
				frappe.msgprint({
					title: __("File Converter"),
					message: `<a href="${esc(r.file_url)}" target="_blank">${esc(r.file_name)}</a>`,
				});
			}
		} catch (err) {
			frappe.hide_progress(); // the server message is shown by frappe.xcall
		}
	}

	function refreshMergeBar(frm, rows) {
		let bar = document.querySelector(".fc-merge-bar");
		const last = rows[rows.length - 1];
		if (rows.length < 2 || !last) {
			if (bar) bar.remove();
			return;
		}
		if (!bar) {
			bar = document.createElement("li");
			bar.className = "fc-merge-bar";
			bar.innerHTML =
				`<div class="fc-merge-title"></div>` +
				`<div class="fc-merge-order"></div>` +
				`<label class="fc-merge-replace"><input type="checkbox"> ${__("Replace originals")}</label>` +
				`<button type="button" class="btn btn-xs btn-primary fc-merge-go">${__("Merge to 1 PDF")}</button>`;
			const box = bar.querySelector("input");
			try {
				box.checked = localStorage.getItem(MERGE_REPLACE_KEY) === "1";
			} catch (e) {
				/* ignore */
			}
			box.addEventListener("change", () => {
				try {
					localStorage.setItem(MERGE_REPLACE_KEY, box.checked ? "1" : "0");
				} catch (e) {
					/* ignore */
				}
			});
			bar.querySelector(".fc-merge-go").addEventListener("click", (ev) => {
				ev.preventDefault();
				runMergeAttachments(box.checked);
			});
		}
		if (bar.previousElementSibling !== last) last.after(bar);

		const names = attachmentNames(frm);
		const show = selected.length >= 2;
		const title = show
			? __("{0} files selected", [selected.length])
			: __("Tick 2 or more files (images / PDFs) to merge them");
		const order = show ? selected.map((id, i) => `${i + 1}. ${names[id] || id}`).join("  →  ") : "";
		const set = (sel, text) => {
			const el = bar.querySelector(sel);
			if (el.textContent !== text) el.textContent = text;
		};
		set(".fc-merge-title", title);
		set(".fc-merge-order", order);
		bar.querySelector(".fc-merge-order").title = order;
		bar.classList.toggle("fc-merge-ready", show);
	}

	function decorateAttachments() {
		if (!onForm()) return;
		const frm = window.cur_frm;
		if (selectedDoc !== frm.docname) {
			selected = [];
			selectedDoc = frm.docname;
		}
		const names = attachmentNames(frm);
		const rows = Array.from(document.querySelectorAll(".attachment-row"));
		rows.forEach((row) => {
			const lock = row.querySelector('a[href^="/app/file/"]');
			if (!lock) return;
			const id = decodeURIComponent(lock.getAttribute("href").split("/app/file/")[1] || "");
			row.dataset.fcId = id;

			if (!row.querySelector(".fc-attach-btn")) {
				const btn = document.createElement("a");
				btn.className = "fc-attach-btn";
				btn.href = "#";
				btn.title = __("Convert this file");
				btn.textContent = "⇄";
				btn.addEventListener("click", (ev) => {
					ev.preventDefault();
					ev.stopPropagation();
					convertAttachmentClick(window.cur_frm, id, ev.clientX, ev.clientY);
				});
				lock.insertAdjacentElement("afterend", btn);
			}

			let chk = row.querySelector(".fc-attach-check");
			if (!chk && rows.length >= 2 && mergeable(names[id])) {
				chk = document.createElement("input");
				chk.type = "checkbox";
				chk.className = "fc-attach-check";
				chk.title = __("Select to merge into one PDF");
				chk.addEventListener("click", (ev) => ev.stopPropagation());
				chk.addEventListener("change", () => toggleSelected(id, chk.checked));
				lock.insertAdjacentElement("beforebegin", chk);
			}
			if (chk && rows.length < 2) chk.remove();
			else if (chk) chk.checked = selected.includes(id);
		});
		selected = selected.filter((id) => rows.some((r) => r.dataset.fcId === id));
		refreshMergeBar(frm, rows);
	}

	/* Frappe rebuilds the attachment list (and sometimes the whole sidebar) after
	 * add/remove/reload, so watch the whole page instead of one sidebar node.
	 * decorateAttachments is idempotent, so its own edits cause no loop. */
	let decoQueued = false;
	new MutationObserver(() => {
		if (decoQueued) return;
		decoQueued = true;
		setTimeout(() => {
			decoQueued = false;
			decorateAttachments();
		}, 60);
	}).observe(document.body, { childList: true, subtree: true });

	/* ---------- menu item fallback on every form ---------- */
	function pickFiles() {
		const input = document.createElement("input");
		input.type = "file";
		input.multiple = true;
		input.onchange = () =>
			openClickDonut(
				Array.from(input.files),
				window.innerWidth / 2,
				Math.max(window.innerHeight / 2, SIZE / 2 + 80)
			);
		input.click();
	}

	$(document).on("form-refresh", function (e, frm) {
		decorateAttachments();
		if (!frm || frm.__fc_menu || !frm.page || !frm.page.add_menu_item) return;
		frm.__fc_menu = true;
		frm.page.add_menu_item(__("Convert File..."), pickFiles);
	});
})();
