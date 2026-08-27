# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

경쟁사(신세계/현대/롯데 프리미엄 아울렛) 공개 활동을 자동 수집해 보여주는 정적 대시보드.
No backend server: a daily GitHub Actions job runs a Python collector, writes a static
`data.json`, and deploys it alongside a single-file HTML dashboard to GitHub Pages.

## Components

- **`collector.py`** — standalone Python 3 script, stdlib-only (no pip deps, no API keys).
  Scrapes Google News RSS (promotions/events/new-brands/news/foreign-trends) and public
  YouTube channel RSS (social), normalizes results, and writes `data.json`.
- **`index.html`** — the entire dashboard: markup, CSS, and vanilla JS in one file, no build
  step, no framework. Reads `data.json` at runtime via `fetch`.
- **`worker/`** — a separate Cloudflare Worker (Node project, own `package.json`) that proxies
  the dashboard's "지금 업데이트" (update now) button to a GitHub `workflow_dispatch` call, so
  the GitHub PAT never reaches the client. Deployed independently of the rest of the repo.
- **`.github/workflows/collect.yml`** — runs on a daily cron, on `workflow_dispatch`, and on
  push to `main`; runs `collector.py`, assembles `_site/` (`index.html` + generated
  `data.json`), and deploys to GitHub Pages.

## Commands

Run the collector locally (writes `data.json` in the repo root):
```bash
python3 collector.py
```
Custom output path (also settable via `OUTPUT_PATH` env var):
```bash
python3 collector.py --out path/to/data.json
```
The collector exits with code 1 if any individual source failed (used by CI to surface
partial failures in the Actions summary) — this does not block deployment.

Preview the dashboard locally: serve the directory (e.g. `python3 -m http.server`) and open
`index.html`. If `data.json` is missing or unreachable, the page falls back to the embedded
`SAMPLE` payload (search `index.html` for `const SAMPLE=`) and shows a "sample data" banner.

Cloudflare Worker (`worker/`), only needed when changing the update-trigger proxy:
```bash
cd worker && npm install
npx wrangler dev       # local dev
npx wrangler deploy    # deploy
```
Secrets (`GITHUB_TOKEN`, `APP_SECRET`) are registered via `npx wrangler secret put <NAME>` and
are never committed; see `worker/README.md` for the full one-time setup.

There is no test suite, linter, or build step in this repo.

## Architecture

**Single data contract.** The dashboard never talks to any brand's site or news API directly —
it only reads `data.json`, shaped `{ generatedAt, sources[], activities[] }`. This
collector/view separation is intentional (stated in `collector.py`'s module docstring) and
should be preserved: collection logic changes stay in `collector.py`; display logic changes
stay in `index.html`.

**Per-source isolation.** Every collector (`collect_homepage`, `collect_news`,
`collect_youtube`, `collect_tourism`) runs in its own try/except inside `run()`'s `stage()`
helper. One source failing (network, parsing) never aborts the run — it's recorded in
`sources[]` with `status: "error"` and the pipeline continues with whatever succeeded. If all
sources for a category fail, that category falls back to the hardcoded `SEED` fixture data so
the pipeline still produces a demo-able output.

**Category mode is a single source of truth duplicated in two languages.** `CATEGORY_MODE` in
`collector.py` and the `CATS` array in `index.html` both encode, per category, whether it's
`status` (has a date range: promotions, events) or `event` (single date: new-brands, social,
news, foreign-trends). Changing a category's mode or adding a category means updating both
files consistently.

**Dedup has two layers.** `normalize()` assigns each activity an `id` (category-scoped) and a
`dedupe_key` (brand + normalized title, category-agnostic — the same story appearing under both
`promotions` and `news` gets the same `dedupe_key` so the frontend can merge it).
`cluster_near_duplicate_keys()` then unions `dedupe_key`s further using two signals —
`difflib.SequenceMatcher` ratio and token-Jaccard overlap (via a small particle-stripping
tokenizer, since there's no Korean morphological analyzer dependency) — to catch reworded or
reordered headlines from different outlets. `DEDUPE_PRIORITY` in `index.html` decides which
category "wins" the display slot when a `dedupe_key` spans categories.

**No copyrighted content is stored.** Only titles, dates, types, and links are kept —
deliberate, per the module docstring, to avoid storing original article/image content.

**Atomic writes.** `write_atomic()` writes to a temp file and `os.replace()`s it into place so
the dashboard (or a concurrent Pages deploy) never reads a half-written `data.json`.

**"New" detection is diff-based.** `mark_new()` compares this run's activity `id`s against the
previous `data.json` snapshot (the file about to be overwritten) to flag `isNew`; on the very
first run (no snapshot yet) it falls back to "occurred within the last 2 days."

**Brands are fixed:** `sse` (신세계, treated as "us"/`me:true` in the dashboard), `hyundai`,
`lotte` — defined in `BRANDS` (`collector.py`) and `BRANDS` (`index.html`); YouTube channel IDs
and keyword filters per brand live in `YT_CHANNELS`/`YT_KEYWORD_FILTER`.
