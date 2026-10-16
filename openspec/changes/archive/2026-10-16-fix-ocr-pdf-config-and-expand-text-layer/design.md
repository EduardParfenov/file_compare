## Context

See `proposal.md` — Why. Current state: `app/__init__.py` loads `OCR_TIMEOUT`, `OCR_CONCURRENCY`, `OCR_PROMPT_VERSION`, `PDF_RENDER_DPI` and `PDF_CROPS_MAX_BYTES` into Flask config, but the values do not reach their call sites:

- `conversion.py:90-91` hardcodes `crops_max_bytes = 4 * 1024 * 1024` in `convert_pdf_pair`.
- `page_image_diff.load_pages` / `render_page` use `DEFAULT_DPI = 200` instead of `PDF_RENDER_DPI`.
- `vision_ocr._reader_for` hardcodes `PROMPT_VERSION = "v2"` instead of `OCR_PROMPT_VERSION`.
- `pdf_converter.convert_pdf_by_reading` reads pages sequentially via dict comprehension; `vision_ocr.read_pages` (with `ThreadPoolExecutor` and `concurrency` parameter) is never called from the pipeline.
- `conversion.py:162` `read_page_range` always reads through the vision model without checking `has_text_layer`, even for native PDF with a text layer.

## Goals / Non-Goals

**Goals:**

- `PDF_CROPS_MAX_BYTES`, `OCR_CONCURRENCY`, `OCR_PROMPT_VERSION`, `OCR_TIMEOUT`, `PDF_RENDER_DPI` from Flask config reach their call sites.
- `read_page_range` checks `has_text_layer` before reading: native PDF → text-layer branch without the model; scan → vision path as today.
- Tests cover "config → call", not only "env → config".

**Non-Goals:**

- `temperature=0` for vision reading (deferred to a separate change; see proposal.md What Changes).
- TTL / cache eviction for `_JOBS`, `_CACHE`, `uploads/` cleanup.
- Magic-bytes validation, rate limiting, client-side tests.

## Decisions

1. `convert_pdf_pair` keeps its `crops_max_bytes` parameter (`None → 4 MB` default preserved); `jobs.convert_pair` passes `current_app.config["PDF_CROPS_MAX_BYTES"]`.
2. `load_pages(path, dpi=dpi)` uses `PDF_RENDER_DPI` from config; `render_page` already does `int(dpi) / 72`, so string config values are handled.
3. `_reader_for(vision_chat, prompt_version)` accepts a prompt-version parameter; callers pass `OCR_PROMPT_VERSION` from config instead of the module constant.
4. `convert_pdf_by_reading` accepts a `concurrency` parameter; when greater than 1 it uses `vision_ocr.read_pages` (ThreadPoolExecutor), otherwise the existing sequential path is kept.
5. `read_page_range` calls `pdf_converter.has_text_layer(path)` first. If true, it converts via `convert_pdf_text_layer` and filters resulting blocks to the requested page numbers. If false, the vision path is used with `OCR_TIMEOUT` for the reading chat.

## Risks / Trade-offs

- [Config value type] `PDF_RENDER_DPI` and `OCR_TIMEOUT` are already parsed to `int` in `app/__init__.py`; no additional coercion needed at call sites.
- [Partial text layer] `has_text_layer` returns true if any page has extractable text; per spec, a partially filled layer counts as present, so the deterministic text-layer branch is used.
- [read_page_range text-layer filtering] `convert_pdf_text_layer` converts the whole document; only blocks whose `page` is in the requested range are returned, so unrequested pages are not leaked into the expansion result.

## Open Questions

- `temperature=0` for vision reading — deferred to a separate change; not decided here.
