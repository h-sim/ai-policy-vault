#!/usr/bin/env python3
"""scripts/write_summary.py

GitHub Actions Job Summary writer for AI Policy Vault.

Data sources:
  run_multi.log     -> [SUMMARY] lines (adopted count + breakdown) and [SUPPRESS] lines
  reports/latest.md -> item details (up to 5 items, parsed from Markdown)

Writing strategy:
  - If $GITHUB_STEP_SUMMARY is set, appends Markdown directly to that file.
  - Otherwise, writes to stdout (local testing).
  - No shell-level redirect (>>) is used; the caller just runs: python3 scripts/write_summary.py
"""

import os
import re
import sys
from pathlib import Path

LOG_PATH = Path("run_multi.log")
REPORT_PATH = Path("reports/latest.md")
MAX_ITEMS = 5

DISCLAIMER = (
    "> 変化の検知記録です。「変化なし」「安全」は断定しません。"
    "必ず一次情報で目視確認してください。"
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_log(log_path: Path) -> tuple[int, dict[str, int], int]:
    """Return (added_total, breakdown, suppress_count).

    breakdown: e.g. {"Breaking": 0, "High": 1, "Medium": 3, "Low": 0}
    suppress_count: number of [SUPPRESS] lines
    """
    added_total = 0
    breakdown: dict[str, int] = {}
    suppress_count = 0

    if not log_path.exists():
        return added_total, breakdown, suppress_count

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return added_total, breakdown, suppress_count

    for ln in lines:
        if ln.startswith("[SUMMARY] Added "):
            # "[SUMMARY] Added N new items" or "... (Breaking=X, High=X, Medium=X, Low=X)"
            m = re.match(r"\[SUMMARY\] Added (\d+) new items(?: \((.+)\))?", ln)
            if m:
                added_total = int(m.group(1))
                if m.group(2):
                    for part in m.group(2).split(", "):
                        k, _, v = part.partition("=")
                        try:
                            breakdown[k.strip()] = int(v.strip())
                        except ValueError:
                            pass
        elif ln.startswith("[SUPPRESS]"):
            suppress_count += 1

    return added_total, breakdown, suppress_count


def parse_health_lines(log_path: Path) -> tuple[int, int, int, list[str]]:
    """Return (ok, fail, skip, fail_details[max 5]).

    fail_details: list of short descriptions like '`fetch` TargetName: HTTP 404'.
    """
    ok_count = fail_count = skip_count = 0
    fail_details: list[str] = []

    if not log_path.exists():
        return ok_count, fail_count, skip_count, fail_details

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ok_count, fail_count, skip_count, fail_details

    pat = re.compile(
        r'^\[HEALTH\] (OK|FAIL|SKIP) name="([^"]+)" stage=(\w+)'
        r'(?:\s+(?:error|reason)="([^"]*)")?'
    )
    for ln in lines:
        m = pat.match(ln)
        if not m:
            continue
        status, name, stage, detail = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        if status == "OK":
            ok_count += 1
        elif status == "FAIL":
            fail_count += 1
            if len(fail_details) < 5:
                desc = f"`{stage}` {name}"
                if detail:
                    desc += f": {detail}"
                fail_details.append(desc)
        elif status == "SKIP":
            skip_count += 1

    return ok_count, fail_count, skip_count, fail_details


def parse_latest_md(report_path: Path, max_items: int) -> list[dict]:
    """Parse reports/latest.md, returning up to max_items change records.

    Each record: {impact, source_name, diff_added, diff_removed, entries}
    Returns [] on missing file, unreadable file, or any parse error.
    """
    if not report_path.exists():
        return []

    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    items: list[dict] = []
    current_source = ""
    current_item: dict | None = None
    in_entries = False
    in_code_block = False

    for ln in text.splitlines():
        # Track fenced code blocks — skip their contents entirely
        if ln.startswith("```"):
            in_code_block = not in_code_block
            in_entries = False
            continue
        if in_code_block:
            continue

        # H2 source heading: "## SourceName"
        # (H1 "# Title" and H3+ "### ..." do not match)
        if re.match(r"^## [^#]", ln):
            current_source = ln[3:].strip()
            in_entries = False
            continue

        # Item heading: "### 変更 N — [Impact] (score=X)"
        m_item = re.match(r"^### 変更 \d+ — \[(\w+)\]", ln)
        if m_item:
            # Commit the previous item before starting a new one
            if current_item is not None and len(items) < max_items:
                items.append(current_item)
            if len(items) >= max_items:
                break
            current_item = {
                "impact": m_item.group(1),
                "source_name": current_source,
                "diff_added": 0,
                "diff_removed": 0,
                "entries": [],
            }
            in_entries = False
            continue

        if current_item is None:
            continue

        # diff line: "- **diff**: +A / -B（churn=C）"
        m_diff = re.match(r"^- \*\*diff\*\*: \+(\d+) / -(\d+)", ln)
        if m_diff:
            current_item["diff_added"] = int(m_diff.group(1))
            current_item["diff_removed"] = int(m_diff.group(2))
            in_entries = False
            continue

        # Entry section header
        if ln.strip() == "- **検知エントリ**:":
            in_entries = True
            continue

        if in_entries:
            # Stop collecting at blank lines, next bullet-key, or headings
            if not ln or ln.startswith("- **") or ln.startswith("#"):
                in_entries = False
            else:
                # "  - Title — URL"  (em-dash U+2014)
                m_entry = re.match(r"^\s+- (.+?) — (https?://\S+)\s*$", ln)
                if m_entry:
                    current_item["entries"].append({
                        "title": m_entry.group(1).strip(),
                        "url": m_entry.group(2).strip(),
                    })

    # Commit the last item if we ran out of lines
    if current_item is not None and len(items) < max_items:
        items.append(current_item)

    return items


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _sanitize_cell(text: str) -> str:
    """Ensure a Markdown table cell value stays on one line.

    - Replaces newlines and tabs with a single space
    - Collapses consecutive spaces into one
    - Escapes pipe characters (| -> \\|) to avoid breaking table structure
    """
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.replace("|", r"\|")
    return text.strip()


def build_markdown(
    added_total: int,
    breakdown: dict[str, int],
    suppress_count: int,
    items: list[dict],
    health_ok: int = 0,
    health_fail: int = 0,
    health_skip: int = 0,
    health_fail_details: list[str] | None = None,
) -> str:
    md: list[str] = []
    md.append("## AI Policy Vault — 実行サマリ")
    md.append("")

    if added_total == 0:
        md.append("**採用変更: 0 件（未検出）**")
        md.append("")
        md.append(DISCLAIMER)
    else:
        # e.g. "Breaking: 0 / High: 1 / Medium: 3 / Low: 0"
        impact_order = ["Breaking", "High", "Medium", "Low"]
        parts = [f"{k}: {breakdown[k]}" for k in impact_order if k in breakdown]
        breakdown_str = " / ".join(parts)

        count_line = f"**採用変更: {added_total} 件**"
        if breakdown_str:
            count_line += f"（{breakdown_str}）"
        md.append(count_line)
        md.append("")
        md.append(DISCLAIMER)
        md.append("")

        if items:
            shown = len(items)
            suffix = "全件" if shown == added_total else f"上位 {shown} 件"
            md.append(f"### 変更要点（{suffix}）")
            md.append("")
            md.append("| # | Impact | ソース | 検知エントリ（代表） | diff |")
            md.append("|---|--------|--------|----------------------|------|")

            for i, item in enumerate(items, 1):
                entries = item["entries"]
                if entries:
                    title = _sanitize_cell(entries[0]["title"])
                    if len(title) > 50:
                        title = title[:47] + "..."
                    url = _sanitize_cell(entries[0]["url"])
                    entry_cell = f"[{title}]({url})"
                    if len(entries) > 1:
                        entry_cell += f" ほか{len(entries) - 1}件"
                else:
                    entry_cell = "（エントリ情報なし）"

                source_cell = _sanitize_cell(item["source_name"])
                diff_cell = f"+{item['diff_added']} / -{item['diff_removed']}"
                md.append(
                    f"| {i} | {item['impact']} | {source_cell}"
                    f" | {entry_cell} | {diff_cell} |"
                )
        else:
            # latest.md missing or parse failed — still show the count, note the detail
            md.append(
                "_詳細は `reports/latest.md` を参照してください"
                "（形式不一致または未生成）。_"
            )

    if suppress_count:
        md.append("")
        md.append(f"<details><summary>抑制（Suppress）: {suppress_count} 件</summary>")
        md.append("")
        md.append("run_multi.log の `[SUPPRESS]` 行を参照してください。")
        md.append("")
        md.append("</details>")

    if health_ok or health_fail or health_skip:
        md.append("")
        md.append("### 健全性（ターゲット別）")
        md.append("")
        parts = [f"✅ OK: {health_ok} 件"]
        if health_fail:
            parts.append(f"❌ FAIL: {health_fail} 件")
        if health_skip:
            parts.append(f"⏭ SKIP: {health_skip} 件")
        md.append(" / ".join(parts))
        if health_fail_details:
            md.append("")
            md.append(f"<details><summary>FAIL 詳細（{health_fail} 件）</summary>")
            md.append("")
            for d in health_fail_details:
                md.append(f"- {d}")
            md.append("")
            md.append("</details>")

    return "\n".join(md) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    added_total, breakdown, suppress_count = parse_log(LOG_PATH)
    h_ok, h_fail, h_skip, h_details = parse_health_lines(LOG_PATH)

    # Only parse latest.md when there are adopted changes (avoids reading stale file)
    items: list[dict] = []
    if added_total > 0:
        items = parse_latest_md(REPORT_PATH, MAX_ITEMS)

    md = build_markdown(
        added_total, breakdown, suppress_count, items,
        health_ok=h_ok, health_fail=h_fail,
        health_skip=h_skip, health_fail_details=h_details,
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
