import os
import re
import json
import hashlib
import argparse
import sys
from datetime import datetime, timezone
from difflib import unified_diff
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from targets import TARGETS

from normalizers import normalize_rss_min, normalize_openapi_c14n_v1

# targets.py の "normalize" キーで指定された正規化を適用する
NORMALIZERS = {
    # RSS/Atom: まずは本文を比較対象に入れず、メタ更新ノイズを最小化（ROI優先）
    "rss_min": lambda text: normalize_rss_min(text, body_limit=0),

    # OpenAPI: YAMLの整形/キー順の揺れによるノイズを削減（意味は保持）
    "openapi_c14n_v1": normalize_openapi_c14n_v1,
}


SNAPSHOT_DIR = "snapshots"
STATE_FILE = "state.json"
MAX_ITEMS = 50  # RSSに残す履歴数（多すぎると読まれない）

# RSS/XML でノイズになりやすいメタデータ差分は無視（価値が低い通知を減らす）
IGNORE_DIFF_SUBSTRINGS = [
    "lastbuilddate",
    "<lastbuilddate>",
    "</lastbuilddate>",
    "<generator>",
    "</generator>",
    "rel=\"self\"",
    "type=\"application/rss+xml\"",
]


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = s.replace(" ", "_")
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    return s or "unnamed"


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # スクリプト・スタイル等はテキスト化のノイズになりやすいので除去
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _xml_text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _first_child_text(parent, names) -> str:
    if parent is None:
        return ""
    for nm in names:
        child = parent.find(nm)
        if child is not None:
            t = _xml_text(child)
            if t:
                return t
    return ""


def _first_link(parent) -> str:
    if parent is None:
        return ""

    # Atom: <link href="..." rel="alternate" />
    for lk in parent.findall("{http://www.w3.org/2005/Atom}link"):
        href = (lk.attrib.get("href") or "").strip()
        rel = (lk.attrib.get("rel") or "").strip().lower()
        if href and (rel in ("", "alternate")):
            return href

    # RSS: <link>https://...</link>
    lk = parent.find("link")
    if lk is not None:
        t = _xml_text(lk)
        if t:
            return t

    # 名前空間付きRSS互換
    for lk in parent.findall("{*}link"):
        t = _xml_text(lk)
        if t:
            return t
        href = (lk.attrib.get("href") or "").strip()
        if href:
            return href

    return ""


def normalize_feed_xml(xml_text: str, max_items: int = 80) -> str:
    """RSS/AtomのXMLを『更新検知に必要な最小情報』に正規化してノイズを削る。"""
    xml_text = (xml_text or "").strip()
    if not xml_text:
        return ""

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        # XMLとしてパースできない場合はそのまま比較（壊さない）
        return xml_text

    tag = (root.tag or "").lower()
    lines = []

    # RSS 2.0
    if tag.endswith("rss") or "rss" in tag:
        channel = root.find("channel") or root.find("{*}channel")
        items = []
        if channel is not None:
            items = channel.findall("item") or channel.findall("{*}item")

        for it in items[:max_items]:
            title = _first_child_text(it, ["title", "{*}title"]) or "(no title)"
            link = _first_link(it)
            dt = _first_child_text(it, ["pubDate", "{*}pubDate"]) or _first_child_text(it, ["date", "{*}date"])
            gid = _first_child_text(it, ["guid", "{*}guid"]) or _first_child_text(it, ["id", "{*}id"])
            lines.append(f"{dt}\t{title}\t{link}\t{gid}")

        return "\n".join(lines).strip() or xml_text

    # Atom
    if tag.endswith("feed") or "feed" in tag:
        ns_atom = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(f"{ns_atom}entry")
        for ent in entries[:max_items]:
            title = _first_child_text(ent, [f"{ns_atom}title", "title", "{*}title"]) or "(no title)"
            link = _first_link(ent)
            dt = _first_child_text(
                ent,
                [f"{ns_atom}updated", f"{ns_atom}published", "updated", "published", "{*}updated", "{*}published"],
            )
            gid = _first_child_text(ent, [f"{ns_atom}id", "id", "{*}id"])
            lines.append(f"{dt}\t{title}\t{link}\t{gid}")

        return "\n".join(lines).strip() or xml_text

    return xml_text


def diff_snippet(old_text: str, new_text: str, max_lines: int = 40) -> str:
    old_lines = old_text.splitlines(keepends=False)
    new_lines = new_text.splitlines(keepends=False)

    diff = unified_diff(old_lines, new_lines, lineterm="")
    snippet_lines = []
    for line in diff:
        # ヘッダは除外
        if line.startswith(("---", "+++", "@@")):
            continue
        # 変更行のみ収集（±）
        if line.startswith(("-", "+")) and not line.startswith(("--", "++")):
            # ノイズ差分は落とす（lastBuildDate等）
            low = line.lower()
            if any(s in low for s in IGNORE_DIFF_SUBSTRINGS):
                continue
            # 長すぎる行は切る
            snippet_lines.append(line[:200])
        if len(snippet_lines) >= max_lines:
            break

    return "\n".join(snippet_lines).strip()

def diff_stats(old_text: str, new_text: str) -> dict:
    """diff_snippet と同じフィルタ方針で、追加/削除行数を集計する。"""
    old_lines = old_text.splitlines(keepends=False)
    new_lines = new_text.splitlines(keepends=False)

    added = 0
    removed = 0

    diff = unified_diff(old_lines, new_lines, lineterm="")
    for line in diff:
        # ヘッダは除外
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith(("-", "+")) and not line.startswith(("--", "++")):
            low = line.lower()
            if any(s in low for s in IGNORE_DIFF_SUBSTRINGS):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1

    return {"added": added, "removed": removed, "churn": added + removed}


def snippet_stats(snippet: str) -> dict:
    """snippet（+/-行）から、追加/削除/総量(churn)を集計する。"""
    added = 0
    removed = 0
    for line in (snippet or "").splitlines():
        if not line:
            continue
        if line.startswith(("+", "-")) and not line.startswith(("++", "--")):
            low = line.lower()
            if any(s in low for s in IGNORE_DIFF_SUBSTRINGS):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
    return {"added": added, "removed": removed, "churn": added + removed}


# News(RSS)向けにdiff snippetを圧縮して可読性を高める
def compact_news_snippet(snippet: str, max_lines: int = 12, prefer_keywords: list[str] | None = None) -> str:
    """News(RSS)向けに diff snippet を読みやすく圧縮する。

    - 大量入替が起きた時、40行の +/- だと読まれないため、
      "規約/料金/安全" などのキーワード行を優先して残す。
    - それでも足りない場合は先頭から補完する。

    返り値は `diff_snippet()` と同じく +/- 行のみ。
    """
    lines = [ln for ln in (snippet or "").splitlines() if ln.strip()]
    if not lines:
        return ""

    kws = [k.lower() for k in (prefer_keywords or []) if k]

    # 1) キーワードを含む行を優先
    picked: list[str] = []
    seen: set[str] = set()

    def _add(line: str) -> None:
        if line in seen:
            return
        seen.add(line)
        picked.append(line)

    if kws:
        for ln in lines:
            low = ln.lower()
            if any(k in low for k in kws):
                _add(ln)
                if len(picked) >= max_lines:
                    break

    # 2) 足りなければ先頭から補完
    if len(picked) < max_lines:
        for ln in lines:
            _add(ln)
            if len(picked) >= max_lines:
                break

    return "\n".join(picked[:max_lines]).strip()

def classify_impact(name: str, url: str, snippet: str, default_impact: str):
    """重要度の自動判定（MVP: 方針2=ノイズ最小優先）

    - diff（snippet）から強いシグナルがある時だけ High/Breaking に昇格
    - それ以外は Medium/Low に落として Important を汚さない

    Returns:
        (impact, score, reasons)
    """
    n = (name or "").lower()
    u = (url or "").lower()
    s = (snippet or "").lower()

    score = 0
    reasons = []

    # --- OpenAPI (YAML) ---
    is_openapi = ("openapi" in n) or u.endswith((".yml", ".yaml"))
    if is_openapi:
        # 重要フィールドの変更は強いシグナル
        if re.search(r"^[+-]\s*version:\s*.+$", snippet, flags=re.MULTILINE):
            score += 60
            reasons.append("OpenAPI: version変更")

        if "termsofservice" in s:
            score += 50
            reasons.append("OpenAPI: termsOfService変更")

        # servers / base url
        if re.search(r"^[+-]\s*servers:\s*$", snippet, flags=re.MULTILINE) or "https://api.openai.com" in s:
            score += 40
            reasons.append("OpenAPI: servers.url変更")

        # security scheme / auth
        if re.search(r"^[+-]\s*security:\s*$", snippet, flags=re.MULTILINE) or "apikeyauth" in s:
            score += 40
            reasons.append("OpenAPI: security変更")

        # tags の増減は軽微扱い（MVP: 方針2=ノイズ最小のため加点しない）
        if re.search(r"^[+-]\s*-\s*name:\s*.+$", snippet, flags=re.MULTILINE):
            score += 0
            reasons.append("OpenAPI: tags増減（軽微）")

        # しきい値で impact を決める（MVPはノイズ最小）
        if score >= 80:
            return "Breaking", score, reasons
        if score >= 50:
            return "High", score, reasons
        if score >= 20:
            return "Medium", score, reasons
        return "Low", score, reasons

    # --- Developer Changelog (RSS) ---
    is_changelog = "changelog" in n
    if is_changelog:
        # 破壊的/移行必須/提供終了系
        breaking_kw = [
            "breaking",
            "deprecat",
            "removed",
            "remove ",
            "will be removed",
            "sunset",
            "sunsetting",
            "migration",
            "end of life",
            "eol",
        ]
        if any(k in s for k in breaking_kw):
            score += 80
            reasons.append("Changelog: breaking/deprecate/removed")

        # セキュリティ・認証・権限
        sec_kw = ["security", "auth", "authentication", "authorization", "permission", "scope", "policy"]
        if any(k in s for k in sec_kw):
            score += 30
            reasons.append("Changelog: security/auth/policy")

        # 価格・課金・制限（運用影響が出やすい）
        price_kw = ["pricing", "price", "billing", "quota", "rate limit", "limit"]
        if any(k in s for k in price_kw):
            score += 30
            reasons.append("Changelog: pricing/quota")

        # MVP(方針2): RSSの「直近N件」ウィンドウ更新で古い項目が落ちただけの差分は通知しない
        st = snippet_stats(snippet or "")
        if score == 0 and st.get("added", 0) == 0 and st.get("removed", 0) > 0 and st.get("churn", 0) <= 10:
            reasons.append("Changelog: 古い項目の脱落（ウィンドウ更新）→通知抑制")
            return "Low", score, reasons
        if score >= 80:
            return "Breaking", score, reasons
        if score >= 50:
            return "High", score, reasons
        if score >= 20:
            return "Medium", score, reasons
        # 既定は High だが、MVPはノイズ最小のため Medium へ落とす
        return "Medium", score, reasons

    # --- News (RSS) ---
    is_news = "news" in n
    if is_news:
        # ニュースは一般にMediumだが、規約/安全/料金などはHigh候補
        high_kw = [
            "policy",
            "terms",
            "pricing",
            "price",
            "billing",
            "security",
            "compliance",
            "privacy",
            "trust",
            "safety",
        ]

        has_high_signal = any(k in s for k in high_kw)
        if has_high_signal:
            score += 50
            reasons.append("News: policy/terms/pricing/security")

        removed_lines = sum(1 for ln in (snippet or "").splitlines() if ln.startswith("-"))
        added_lines = sum(1 for ln in (snippet or "").splitlines() if ln.startswith("+"))
        churn = removed_lines + added_lines

        # 高シグナルがある上で大量更新なら、重要だが確認コストも高い（理由として明示）
        if churn >= 30 and has_high_signal:
            reasons.append("News: 高シグナル+大量更新（要確認）")

        # MVP(方針2): 高シグナルが無い大量更新は『並び替え/入替/再配信』の可能性が高いので通知を抑制
        if churn >= 30 and not has_high_signal:
            reasons.append("News: 大量更新（入替/並び替えの可能性）→通知抑制")
            return "Low", score, reasons

        # 大量の削除/入替は誤検知が多いので弱めに扱う
        # ただし「規約/安全/料金などの高シグナル」が既に出ている場合は、理由が冗長になりやすいので付けない
        if (removed_lines >= 20 or added_lines >= 20) and (not has_high_signal):
            score += 10
            reasons.append("News: 大量更新（入替の可能性）")

        if score >= 80:
            return "Breaking", score, reasons
        if score >= 50:
            return "High", score, reasons
        if score >= 20:
            return "Medium", score, reasons
        return "Low", score, reasons

    # --- Fallback ---
    # 未知ターゲットは既定impactを尊重しつつ、強いシグナルがないなら落とす
    if default_impact in ("Breaking", "High"):
        return "Medium", score, reasons
    return default_impact, score, reasons


def run_selftests(verbose: bool = False) -> bool:
    """重要度判定（方針2）の自己テスト。

    - 外部サイトの更新を待たない
    - snapshots/state.json を書き換えない
    - ルール変更の回帰を即座に検知する

    実行: `python3 run_multi.py --selftest`
    """

    tests = [
        {
            "id": "openapi_major",
            "name": "OpenAI OpenAPI Spec (YAML)",
            "url": "https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml",
            "default": "Breaking",
            "snippet": "\n".join(
                [
                    "+openapi: 3.1.0",
                    "+ version: 2.3.0",
                    "+ termsOfService: https://openai.com/policies/terms-of-use",
                    "+servers:",
                    "+ - url: https://api.openai.com/v1",
                    "+security:",
                    "+ - ApiKeyAuth: []",
                    "+tags:",
                    "+ - name: Assistants",
                ]
            ),
            "expect_impact": "Breaking",
            "expect_score": 190,
            "expect_reason_contains": ["OpenAPI: version変更", "OpenAPI: security変更", "OpenAPI: tags増減（軽微）"],
        },
        {
            "id": "openapi_tags_only_low",
            "name": "OpenAI OpenAPI Spec (YAML)",
            "url": "https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml",
            "default": "Breaking",
            "snippet": "\n".join([
                "+tags:",
                "+ - name: Assistants",
                "-tags:",
                "- - name: Chat",
            ]),
            "expect_impact": "Low",
            "expect_score": 0,
            "expect_reason_contains": ["OpenAPI: tags増減（軽微）"],
        },
        {
            "id": "news_churn_suppressed",
            "name": "OpenAI News (RSS)",
            "url": "https://openai.com/news/rss.xml",
            "default": "Medium",
            "snippet": "\n".join(["- old" for _ in range(20)] + ["+ new" for _ in range(20)]),
            "expect_impact": "Low",
            "expect_score_min": 0,
            "expect_reason_contains": ["News: 大量更新（入替/並び替えの可能性）→通知抑制"],
        },
        {
            "id": "news_policy_high",
            "name": "OpenAI News (RSS)",
            "url": "https://openai.com/news/rss.xml",
            "default": "Medium",
            "snippet": "\n".join([
                "+ OpenAI Policy Update",
                "+ terms of use",
            ]),
            "expect_impact": "High",
            "expect_score_min": 50,
            "expect_reason_contains": ["News: policy/terms/pricing/security"],
        },
        {
            "id": "news_policy_high_with_churn",
            "name": "OpenAI News (RSS)",
            "url": "https://openai.com/news/rss.xml",
            "default": "Medium",
            "snippet": "\n".join(["- old" for _ in range(20)] + ["+ new" for _ in range(19)] + ["+ Terms of Use update" ]),
            "expect_impact": "High",
            "expect_score_min": 50,
            "expect_reason_contains": ["News: policy/terms/pricing/security", "News: 高シグナル+大量更新（要確認）"],
            "expect_reason_not_contains": ["News: 大量更新（入替の可能性）"],
        },
        {
            "id": "changelog_breaking",
            "name": "OpenAI Developer Changelog (RSS)",
            "url": "https://developers.openai.com/changelog/rss.xml",
            "default": "High",
            "snippet": "\n".join([
                "+ Breaking change: This feature will be removed",
                "+ Migration required",
            ]),
            "expect_impact": "Breaking",
            "expect_score_min": 80,
            "expect_reason_contains": ["Changelog: breaking/deprecate/removed"],
        },
        {
            "id": "changelog_window_drop_suppressed",
            "name": "OpenAI Developer Changelog (RSS)",
            "url": "https://developers.openai.com/changelog/rss.xml",
            "default": "High",
            "snippet": "\n".join([
                "-title: Codex CLI Release: 0.73.0",
                "-link: https://developers.openai.com/changelog/#github-release-270562118",
                "-id: https://developers.openai.com/changelog/#github-release-270562118",
                "-date: Mon, 15 Dec 2025 00:00:00 GMT",
                "-body:",
                "-#ITEM",
            ]),
            "expect_impact": "Low",
            "expect_score": 0,
            "expect_reason_contains": ["Changelog: 古い項目の脱落（ウィンドウ更新）→通知抑制"],
        },
        {
            "id": "diff_snippet_ignores_rss_meta",
            "name": "RSS meta noise only",
            "url": "https://example.com/rss.xml",
            "default": "Medium",
            # diff_snippet/diff_stats を検証する（classify_impact は呼ばない）
            "snippet": None,
            "old_text": "<rss><channel><lastBuildDate>Mon, 01 Jan 2026 00:00:00 GMT</lastBuildDate></channel></rss>",
            "new_text": "<rss><channel><lastBuildDate>Tue, 02 Jan 2026 00:00:00 GMT</lastBuildDate></channel></rss>",
            "expect_diff_snippet_empty": True,
            "expect_diff_stats": {"added": 0, "removed": 0, "churn": 0},
        },
        {
            "id": "news_compact_keeps_high_signal_line",
            "name": "OpenAI News (RSS)",
            "url": "https://openai.com/news/rss.xml",
            "default": "Medium",
            "snippet": "\n".join(["- old" for _ in range(20)] + ["+ new" for _ in range(19)] + ["+ Terms of Use update"]),
            # compact_news_snippet の回帰防止：高シグナル行が削られないこと
            "expect_compact_news": {
                "max_lines": 12,
                "prefer_keywords": [
                    "policy",
                    "terms",
                    "termsofservice",
                    "pricing",
                    "billing",
                    "security",
                    "privacy",
                    "trust",
                    "safety",
                ],
                "must_contain": ["Terms of Use"],
            },
        },
        {
            "id": "news_item_id_uses_full_snippet",
            "name": "OpenAI News (RSS)",
            "url": "https://openai.com/news/rss.xml",
            "default": "Medium",
            "snippet": "\n".join(["- old" for _ in range(20)] + ["+ new" for _ in range(19)] + ["+ Terms of Use update"]),
            # 仕様要件：state の id は「圧縮前 snippet」で生成し、圧縮方法の変更で重複 item が増えないようにする
            "expect_item_id_full_vs_compact_different": True,
        },
        {
            "id": "diff_stats_counts_real_change",
            "name": "real change should count",
            "url": "https://example.com/rss.xml",
            "default": "Medium",
            "snippet": None,
            "old_text": "a\nb\nc\n",
            "new_text": "a\nb\nX\n",
            "expect_diff_snippet_empty": False,
            "expect_diff_stats": {"added": 1, "removed": 1, "churn": 2},
        },
    ]

    ok = True
    print("[SELFTEST] classify_impact rules (MVP: 方針2=ノイズ最小)")

    for t in tests:
        # diff_snippet/diff_stats の挙動テスト（通知ノイズの回帰を防ぐ）
        if t.get("snippet") is None:
            old_text = t.get("old_text", "")
            new_text = t.get("new_text", "")

            sn = diff_snippet(old_text, new_text)
            st2 = diff_stats(old_text, new_text)

            exp_empty = bool(t.get("expect_diff_snippet_empty"))
            exp_stats = t.get("expect_diff_stats") or {}

            fail_reasons = []
            if exp_empty and sn != "":
                fail_reasons.append("diff_snippet expected empty but got non-empty")
            if (not exp_empty) and sn == "":
                fail_reasons.append("diff_snippet expected non-empty but got empty")

            for k in ("added", "removed", "churn"):
                if k in exp_stats and st2.get(k) != exp_stats.get(k):
                    fail_reasons.append(f"diff_stats {k} expected={exp_stats.get(k)} got={st2.get(k)}")

            if fail_reasons:
                ok = False
                print(f"[FAIL] {t['id']}: " + "; ".join(fail_reasons))
                if verbose:
                    print("       diff_snippet=", sn)
                    print("       diff_stats =", st2)
            else:
                if verbose:
                    print(f"[PASS] {t['id']}: diff_snippet_len={len(sn)} diff_stats={st2}")
                else:
                    print(f"[PASS] {t['id']}")
            continue

        # compact_news_snippet の回帰テスト（可読性と重要行の保持）
        exp_compact = t.get("expect_compact_news")
        if exp_compact is not None:
            compacted = compact_news_snippet(
                t.get("snippet") or "",
                max_lines=int(exp_compact.get("max_lines", 12)),
                prefer_keywords=list(exp_compact.get("prefer_keywords") or []),
            )
            fail_reasons = []
            # 行数制限
            if len([ln for ln in compacted.splitlines() if ln.strip()]) > int(exp_compact.get("max_lines", 12)):
                fail_reasons.append("compact_news_snippet exceeded max_lines")
            # 必須キーワード行が残る
            for needle in list(exp_compact.get("must_contain") or []):
                if needle not in compacted:
                    fail_reasons.append(f"compact_news_snippet missing '{needle}'")
            if fail_reasons:
                ok = False
                print(f"[FAIL] {t['id']}: " + "; ".join(fail_reasons))
                if verbose:
                    print("       compacted=", compacted)
            else:
                if verbose:
                    print(f"[PASS] {t['id']}: compact_lines={len(compacted.splitlines())}")
                else:
                    print(f"[PASS] {t['id']}")
            continue

        # item_id は圧縮前 snippet を使う要件テスト（圧縮ルール変更で重複itemを増やさない）
        if t.get("expect_item_id_full_vs_compact_different"):
            raw_sn = t.get("snippet") or ""
            compacted = compact_news_snippet(
                raw_sn,
                max_lines=12,
                prefer_keywords=[
                    "policy",
                    "terms",
                    "termsofservice",
                    "pricing",
                    "billing",
                    "security",
                    "privacy",
                    "trust",
                    "safety",
                ],
            )
            id_full = make_item_id(t.get("url") or "", raw_sn)
            id_comp = make_item_id(t.get("url") or "", compacted)
            if id_full == id_comp:
                ok = False
                print(f"[FAIL] {t['id']}: expected item_id(full) != item_id(compact) but got equal")
                if verbose:
                    print("       full=", raw_sn)
                    print("       compact=", compacted)
            else:
                if verbose:
                    print(f"[PASS] {t['id']}: item_id differs as expected")
                else:
                    print(f"[PASS] {t['id']}")
            continue

        # classify_impact のルールテスト
        impact, score, reasons = classify_impact(t["name"], t["url"], t["snippet"], t["default"])
        st = snippet_stats(t["snippet"])

        exp_impact = t.get("expect_impact")
        exp_score = t.get("expect_score")
        exp_score_min = t.get("expect_score_min")
        need_reasons = t.get("expect_reason_contains") or []
        deny_reasons = t.get("expect_reason_not_contains") or []

        fail_reasons = []
        if exp_impact is not None and impact != exp_impact:
            fail_reasons.append(f"impact expected={exp_impact} got={impact}")
        if exp_score is not None and score != exp_score:
            fail_reasons.append(f"score expected={exp_score} got={score}")
        if exp_score_min is not None and score < exp_score_min:
            fail_reasons.append(f"score expected>={exp_score_min} got={score}")
        for r in need_reasons:
            if r not in reasons:
                fail_reasons.append(f"missing reason '{r}'")
        for r in deny_reasons:
            if r in reasons:
                fail_reasons.append(f"unexpected reason '{r}'")

        if fail_reasons:
            ok = False
            print(f"[FAIL] {t['id']}: " + "; ".join(fail_reasons))
            if verbose:
                print("       reasons=", reasons)
                print("       snippet=", t["snippet"])
        else:
            if verbose:
                print(
                    f"[PASS] {t['id']}: impact={impact} score={score} reasons={reasons} (+{st['added']}/-{st['removed']}, churn={st['churn']})"
                )
            else:
                print(f"[PASS] {t['id']} (+{st['added']}/-{st['removed']}, churn={st['churn']})")
    print("[SELFTEST] RESULT:", "PASS" if ok else "FAIL")
    return ok


def summarize_ja_3lines(name: str, url: str, snippet: str, impact: str) -> str:
    """日本語3行要約（炎上しない設計）
    - 断定しない（「〜の可能性」「〜のように見える」）
    - 推測や外部知識を入れない（差分から読める範囲のみ）
    - 失敗しても運用を止めない（空文字で返す）
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return ""

    try:
        client = OpenAI(api_key=api_key)

        prompt = f"""あなたはプロダクト責任者向けの変更監視アシスタントです。
以下の差分（+/-行）だけから、日本語で『必ず3行』要約してください。

制約:
- 必ず3行（改行2つ）
- 1行は40文字程度まで（長い場合は短く）
- 断定禁止（「〜の可能性」「〜のように見える」）
- 推測や外部知識は禁止。差分から読める範囲のみ

対象:
- name: {name}
- url: {url}
- impact: {impact}

差分:
{snippet}
"""

        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
        )

        # SDK差異に備え、output_text が無い/空の場合は output 配列から拾う
        text = ""
        if hasattr(resp, "output_text") and getattr(resp, "output_text"):
            text = getattr(resp, "output_text")
        else:
            try:
                parts = []
                for item in getattr(resp, "output", []) or []:
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if t:
                            parts.append(t)
                text = "".join(parts)
            except Exception:
                text = ""

        text = (text or "").strip()
        if not text:
            return ""

        # 保険：必ず3行に整形（ただし断定しない文面に寄せる）
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        lines = lines[:3]
        while len(lines) < 3:
            lines.append("差分のみ要確認のように見える")
        return "\n".join(lines)

    except Exception:
        return ""


def utc_now_rfc822() -> str:
    # RSS向け
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def load_state() -> list:
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_state(items: list) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def make_item_id(url: str, snippet: str) -> str:
    h = hashlib.sha1()
    h.update((url + "\n" + snippet).encode("utf-8"))
    return h.hexdigest()


def ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def fetch(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text


def main(log_diff_stats: bool = False):
    ensure_dir(SNAPSHOT_DIR)

    state = load_state()
    existing_ids = {it.get("id") for it in state if "id" in it}

    # 今回の実行で「新規に追加された item 数」を集計（Actionsログだけで状況把握できるようにする）
    added_total = 0
    added_by_impact = {"Breaking": 0, "High": 0, "Medium": 0, "Low": 0}

    # 「通知抑制」した変更の件数（RSS/履歴には載せないが、snapshotは更新してノイズ再発を防ぐ）
    suppressed_total = 0
    suppressed_by_type = {"window_drop": 0, "bulk_update": 0, "other": 0}

    for t in TARGETS:
        name = t["name"]
        url = t["url"]
        impact = t["impact"]

        snap_file = os.path.join(SNAPSHOT_DIR, f"{slugify(name)}.txt")

        old_text = ""
        if os.path.exists(snap_file):
            with open(snap_file, "r", encoding="utf-8") as f:
                old_text = f.read()

        try:
            raw = fetch(url)

            # 1) targets.py の normalize 指定があれば最優先で適用
            new_text = None
            norm_key = t.get("normalize")
            if norm_key:
                fn = NORMALIZERS.get(norm_key)
                if fn:
                    try:
                        new_text = fn(raw)
                    except Exception as e:
                        if os.getenv("DEBUG_NORMALIZE", "") in ("1", "true", "TRUE"):
                            print(f"[WARN] normalize failed: {name} ({norm_key}) -> {e}")
                        new_text = None

            # 2) normalize 指定が無い / 失敗した場合は従来ロジックでフォールバック
            if new_text is None:
                # XMLはRSS/Atomなら『エントリ一覧』に正規化して比較（巨大diffのノイズ削減）
                if url.endswith(".xml"):
                    new_text = normalize_feed_xml(raw, max_items=80)

                # YAMLはそのまま（正規化は行末処理で最低限）
                elif url.endswith((".yml", ".yaml")):
                    new_text = raw

                else:
                    # HTMLっぽい場合だけテキスト抽出
                    if "<html" in raw.lower() or "<!doctype html" in raw.lower():
                        new_text = extract_text(raw)
                    else:
                        new_text = raw

            # 全形式共通の正規化（CRLF→LF + 行末空白除去）
            new_text = "\n".join(line.rstrip() for line in new_text.replace("\r\n", "\n").splitlines())

        except Exception as e:
            print(f"[{impact}] {name} : 取得失敗（今回はスキップ） -> {e}")
            continue

        if not old_text:
            # 初回は比較対象が無いので、スナップショットだけ保存して終了
            with open(snap_file, "w", encoding="utf-8") as f:
                f.write(new_text)
            print(f"[{impact}] {name} : 初回")
            continue

        snippet = diff_snippet(old_text, new_text)
        # 変更なし（=diff_snippet が空）なら、スナップショットも state も更新しない
        if not snippet:
            if log_diff_stats:
                print(f"[{impact}] {name} : 変更なし (+0/-0, churn=0)")
            else:
                print(f"[{impact}] {name} : 変更なし")
            continue

        # ここから先は「変更あり」
        # state.json には常に diff 統計を保存する（ログ出力有無と独立）
        stats_for_state = diff_stats(old_text, new_text)

        # item_id は「圧縮前の完全な diff snippet」で固定（圧縮ルール変更で重複itemが増えないようにする）
        raw_snippet = snippet
        snippet_full_for_state = ""

        # News は大量入替が起きやすいので、excerpt を短くして可読性を最優先する
        if "news" in (name or "").lower() and stats_for_state.get("churn", 0) >= 20:
            snippet = compact_news_snippet(
                snippet,
                max_lines=12,
                prefer_keywords=[
                    "policy",
                    "terms",
                    "termsofservice",
                    "pricing",
                    "billing",
                    "security",
                    "privacy",
                    "trust",
                    "safety",
                ],
            )
            snippet_full_for_state = raw_snippet

        impact2, score, reasons = classify_impact(name, url, snippet, impact)
        # 「通知抑制」扱いの Low は RSS/履歴には載せないが、snapshotは更新して同じノイズが繰り返し出ないようにする
        if impact2 == "Low" and any("通知抑制" in r for r in (reasons or [])):
            # snapshot は更新（次回以降の差分をクリーンにする）
            with open(snap_file, "w", encoding="utf-8") as f:
                f.write(new_text)

            suppressed_total += 1
            rs = " ".join(reasons or [])
            if "ウィンドウ更新" in rs:
            sup_kind = "window_drop"
                suppressed_by_type[sup_kind] += 1
            elif "大量更新" in rs:
                sup_kind = "bulk_update"
                suppressed_by_type[sup_kind] += 1
            else:
                sup_kind = "other"
                suppressed_by_type[sup_kind] += 1

            if log_diff_stats:
                print(f"[SUPPRESS] {name} : {sup_kind} (+{stats_for_state['added']}/-{stats_for_state['removed']}, churn={stats_for_state['churn']})")
            else:
                print(f"[SUPPRESS] {name} : {sup_kind}")
            continue

        # ここまで来たら「採用する変更」なので snapshot を更新（変更なし/通知抑制では汚さない）
        with open(snap_file, "w", encoding="utf-8") as f:
            f.write(new_text)

        # Important（Breaking/High）の変更だけ日本語3行要約（API失敗時は空で継続）
        summary_ja = ""
        if impact2 in ("Breaking", "High"):
            summary_ja = summarize_ja_3lines(name, url, snippet, impact2)
            if not summary_ja:
                print(f"[{impact2}] {name} : 要約生成に失敗（空のまま継続）")

        item_id = make_item_id(url, raw_snippet)
        if item_id not in existing_ids:
            state.insert(
                0,
                {
                    "id": item_id,
                    "impact": impact2,
                    "name": name,
                    "url": url,
                    "snippet": snippet,
                    "snippet_full": snippet_full_for_state,
                    "diff": stats_for_state,
                    "score": score,
                    "reasons": reasons,
                    "summary_ja": summary_ja,
                    "pubDate": utc_now_rfc822(),
                },
            )
            existing_ids.add(item_id)
            added_total += 1
            if impact2 in added_by_impact:
                added_by_impact[impact2] += 1

        if log_diff_stats:
            print(
                f"[{impact2}] {name} : 変更あり (score={score}, +{stats_for_state['added']}/-{stats_for_state['removed']}, churn={stats_for_state['churn']})"
            )
        else:
            print(f"[{impact2}] {name} : 変更あり (score={score})")

    # 履歴は上限で刈る
    state = state[:MAX_ITEMS]
    save_state(state)

    # 追加件数のサマリ（「変更なし」でも 0 件と明示する）
    if added_total == 0:
        print("[SUMMARY] Added 0 new items")
    else:
        parts = []
        for k in ("Breaking", "High", "Medium", "Low"):
            v = added_by_impact.get(k, 0)
            if v:
                parts.append(f"{k}={v}")
        tail = (" (" + ", ".join(parts) + ")") if parts else ""
        print(f"[SUMMARY] Added {added_total} new items" + tail)


    # 通知抑制件数のサマリ（ノイズは抑制しつつ、抑制した事実は可視化）
    if suppressed_total == 0:
        print("[SUMMARY] Suppressed 0 changes")
    else:
        parts = []
        if suppressed_by_type.get("window_drop", 0):
            parts.append(f"window_drop={suppressed_by_type['window_drop']}")
        if suppressed_by_type.get("bulk_update", 0):
            parts.append(f"bulk_update={suppressed_by_type['bulk_update']}")
        if suppressed_by_type.get("other", 0):
            parts.append(f"other={suppressed_by_type['other']}")
        tail = (" (" + ", ".join(parts) + ")") if parts else ""
        print(f"[SUMMARY] Suppressed {suppressed_total} changes" + tail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Change Watcher runner")
    parser.add_argument("--selftest", action="store_true", help="Run rule self-tests without touching snapshots/state.json")
    parser.add_argument("--verbose", action="store_true", help="Verbose output for selftest")
    parser.add_argument("--log-diff-stats", action="store_true", help="Print diff stats (+/-/churn) for debugging")
    args = parser.parse_args()

    if args.selftest:
        passed = run_selftests(verbose=args.verbose)
        raise SystemExit(0 if passed else 1)

    main(log_diff_stats=args.log_diff_stats)
