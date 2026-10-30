# Changelog

All notable changes to Keepstack are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
semantic versioning.

## [Unreleased]

### Fixed

- Allow slash-containing tag names to be removed through the existing detail
  page, keeping keyword queries and CSV exports in sync after removal.
- Validate collection IDs against SQLite's signed 64-bit integer range for
  both asset listing and CSV export, returning 422 for oversized values.

### Added

- Combined keyword, collection, media type, all-required tags and excluded tags
  in the existing library, with removable filters and 24-item pagination.
- Authenticated CSV export of every matching asset in list order, including
  UTF-8 BOM, JSON tag arrays and proper CSV escaping.
- Repeatable `python -m keepstack seed-quiz` acceptance data, API tests and
  optional Playwright browser checks (also run in CI).

- Parse common XMP RDF fields into structured metadata, catalog fields, and
  full-text search.
- OCR images at ingest through the Tesseract binary when it is installed, so
  text inside scans and photos becomes searchable. No new Python dependencies;
  disable with `KEEPSTACK_OCR_ENABLED=false`.
- On-device CLIP vision model (optional `pip install onnxruntime`): real
  semantic search over image content, visual similarity, and zero-shot
  auto-tagging with no API keys, plus a `python -m keepstack reindex` command
  to re-embed an existing catalog.
- Advertise IIIF Image API level 2 and support region cropping, percent
  regions, best-fit sizing, and rotation for IIIF image requests.
- Added `python -m keepstack gc` to remove blob files no longer referenced by
  active assets or their version history.

- IIIF Presentation API 3.0 manifest at `GET /iiif/3/{uuid}/manifest.json`, so
  viewers like Mirador and Universal Viewer open assets directly with their
  label and metadata.
- Login rate limiting: repeated failed attempts for a username and IP are
  locked out with a 429 and `Retry-After`, and the lockout is audit-logged.
  Tunable with `KEEPSTACK_LOGIN_MAX_ATTEMPTS` and `KEEPSTACK_LOGIN_LOCKOUT_SECONDS`.

### Changed

- Keyword search now respects the selected sort, with asset ID tie-breaking.
- Search and export share bound filter SQL; invalid pagination is rejected.
- Library requests are cancelled and versioned on condition changes, with
  loading, retry, empty and download-failure states; semantic UI is hidden.
- JSON requests default to POST, fixing browser login and tag-add operations.

- Switched the web interface to a light theme: white and soft-gray surfaces
  with an indigo accent, applied across the app, placeholders, and share pages.
- Centered the captions in generated seed images so square thumbnails no
  longer crop them.
- Tidied the chrome: background scrolling now locks while the drawer or upload
  modal is open (one scrollbar instead of two), scrollbars are slim and themed,
  and the dropdown controls got a custom chevron with roomier padding.

## [0.1.0] - 2026-06-28

The first working release. A complete DAM core, built to sit in the gap between
standards-rich heritage tools and modern AI photo libraries.

### Added

- Content-addressable blob storage with automatic deduplication and SHA-256 fixity
- Ingest pipeline with EXIF / IPTC / XMP extraction and thumbnail generation
- Search: SQLite FTS5 full-text, faceted filtering, and embedding-based semantic
  search with a "more like this" mode
- Collections, tags (manual and AI-suggested), and per-asset version history
- Four-role access control (admin, editor, contributor, viewer), an audit log on
  every state change, and tokenised expiring share links
- Open standards: Dublin Core mapping, an OAI-PMH 2.0 endpoint, and an IIIF
  Image API endpoint for every asset
- A repository-wide fixity check for digital-preservation integrity
- Optional AI enrichment (Groq vision, Cohere embeddings) with deterministic
  offline fallbacks, so the product is fully useful with zero API keys
- A zero-build two-tone web client and a REST API covering every resource
- A passing test suite, synthetic seed data, and a Docker deployment

### Known limitations

Tracked in [ROADMAP.md](ROADMAP.md) and graded honestly in
[COMPARISON.md](COMPARISON.md): AI is heuristic without a provider key,
preservation is fixity rather than format migration, IIIF is level 1, and there
is no OCR, SSO, or published accessibility conformance report yet.
