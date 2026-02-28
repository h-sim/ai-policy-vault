# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Policy Vault** is a Python-only change detection system that monitors AI platform updates (currently OpenAI sources) and publishes curated, low-noise RSS feeds and an HTML changes page. A Markdown report output for internal reporting is planned/being added. The pipeline runs via GitHub Actions (see `.github/workflows/main.yml` for the schedule), with outputs hosted on GitHub Pages.

## Repository Policies

These principles govern all design decisions and code changes in this repository.

1. **目的** — 情シス/DX担当の「社内報告・監査対応」工数を最小化する。通知サービスではなく、提出物生成ツールである。

2. **提供価値** — 社内提出物（Markdown）と証跡（snapshot/hash）のエビデンスを生成する。

3. **Detectionに徹する** — Judgmentはしない。変化の有無を検出・記録するのみ。内容の解釈・評価はしない。

4. **断定禁止** — 「変化なし」「安全」という表現を使わない。必ず「未検出（要目視確認）」と表現する。

5. **MVP入力** — OpenAPI spec と公式 Changelog を中心とする。ToS/規約の監視は後回し。

6. **ノイズ最小優先** — 大量入替・ウィンドウ更新など実質的な変化のないケースは抑制する。

7. **証跡** — 全ての変化記録に timestamp と hash を付与する。

8. **出力形式** — Markdown 最優先。PDF/Word は MVP フェーズでは作らない。

9. **品質保証** — `--selftest` で回帰を防止する。要約（OpenAI API）失敗時は空文字で継続し、処理を止めない。

10. **対象追加** — 運用可能性を優先する。新しい監視対象を追加する前にプラグイン化・テスト化すること。

## Operational Notes

- "`snapshots/` and `state.json` are tracked/committed (do not add them to `.gitignore`)."
- "After any rule/logic change, run `python3 run_multi.py --selftest` and keep it passing."

## Development Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Validate classification rules (no state changes, safe to run)
python3 run_multi.py --selftest --verbose

# Run watcher locally (requires OPENAI_API_KEY env var for JP summaries)
python3 run_multi.py --log-diff-stats

# Generate output files
python3 generate_rss.py
python3 generate_changes_html.py
```

Required environment variables for full functionality:
- `OPENAI_API_KEY` — needed to generate Japanese 3-line summaries (Breaking/High only)
- `SITE_URL` — base URL for RSS links; defaults to GitHub Pages URL derived from `GITHUB_REPOSITORY`

## Architecture

### Pipeline: Fetch → Normalize → Diff → Classify → Summarize → Persist

**`run_multi.py`** — Main orchestrator (~1,000 lines). For each target in `targets.py`:
1. Fetch current content
2. Normalize via `normalizers.py` (`rss_min` or `openapi_c14n_v1`)
3. Diff against snapshot in `snapshots/`
4. `classify_impact()` — assigns Breaking/High/Medium/Low with reasons
5. Low-impact changes with suppression reasons are skipped (noise avoidance)
6. Breaking/High items call OpenAI API for Japanese 3-line summary
7. Item inserted into `state.json` (deduplicated by SHA1 of `url + "\n" + raw_snippet`, max 50 items)
8. Snapshot updated

**`normalizers.py`** — Reduces diff noise. `normalize_rss_min()` sorts feed entries by (link, id, title) so reordering doesn't trigger false changes. `normalize_openapi_c14n_v1()` canonicalizes YAML key ordering.

**`generate_rss.py`** — Reads `state.json` → writes `feed.xml` (Breaking+High only) and `feed_all.xml` (all items).

**`generate_changes_html.py`** — Reads `state.json` → renders `changes.html` with color-coded impact badges, diff viewers, and Japanese summaries.

**`targets.py`** — Defines monitored sources (URL, default impact level, normalizer).

### State Files

- `state.json` — Array of up to 50 change items, sorted newest-first. Each item has: `id`, `impact`, `name`, `url`, `snippet` (and optional `snippet_full`), `diff` stats, `reasons`, `summary_ja`, `pubDate`.
- `snapshots/` — One `.txt` file per target; stores the normalized content from the last run for diffing.
- `feed.xml` / `feed_all.xml` — Generated RSS outputs.
- `changes.html` / `index.html` — Generated HTML outputs.

### Adding a New Monitoring Target

Edit `targets.py` and add an entry:
```python
{"impact": "High", "name": "Source Name", "url": "https://...", "normalize": "rss_min"}
```
Normalizer choices: `rss_min` (RSS/Atom feeds), `openapi_c14n_v1` (OpenAPI YAML specs).

Before adding, per Policy 10: implement as a plugin and add selftest cases.

### Impact Classification Logic

`classify_impact()` in `run_multi.py` determines severity:
- **Breaking** — OpenAPI version/server/security changes; changelog keywords (deprecat*, breaking, sunset, removed)
- **High** — pricing, security, major feature keywords in changelog/news
- **Medium** — default for News RSS targets
- **Low** — auto-downgraded when no strong signals detected; suppressed from state if suppression reason applies (e.g., window_drop, bulk_rewrite)

The `--selftest` flag runs validation against hardcoded test cases for these rules — run this after modifying classification logic.
