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

        # MVP(方針2): 高シグナルが無い大量更新は『並び替え/入替/再配信』の可能性が高いので通知を抑制
        if churn >= 30 and not has_high_signal:
            reasons.append("News: 大量更新（入替/並び替えの可能性）→通知抑制")
            return "Low", score, reasons

        # 大量の削除/入替は誤検知が多いので弱めに扱う
        if removed_lines >= 20 or added_lines >= 20:
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
    ]

    ok = True
    print("[SELFTEST] classify_impact rules (MVP: 方針2=ノイズ最小)")

    for t in tests:
        impact, score, reasons = classify_impact(t["name"], t["url"], t["snippet"], t["default"])
        st = snippet_stats(t["snippet"])

        exp_impact = t.get("expect_impact")
        exp_score = t.get("expect_score")
        exp_score_min = t.get("expect_score_min")
        need_reasons = t.get("expect_reason_contains") or []

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

        if fail_reasons:
            ok = False
            print(f"[FAIL] {t['id']}: " + "; ".join(fail_reasons))
            if verbose:
                print("       reasons=", reasons)
                print("       snippet=", t["snippet"])
        else:
            if verbose:
                print(f"[PASS] {t['id']}: impact={impact} score={score} reasons={reasons} (+{st['added']}/-{st['removed']}, churn={st['churn']})")
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

        # スナップショット更新（次回比較用）
        with open(snap_file, "w", encoding="utf-8") as f:
            f.write(new_text)

        if not old_text:
            print(f"[{impact}] {name} : 初回")
            continue

        snippet = diff_snippet(old_text, new_text)
        if snippet:
            # state.json には常に diff 統計を保存する（ログ出力有無と独立）
            stats_for_state = diff_stats(old_text, new_text)

            impact2, score, reasons = classify_impact(name, url, snippet, impact)

            # Important（Breaking/High）の変更だけ日本語3行要約（API失敗時は空で継続）
            summary_ja = ""
            if impact2 in ("Breaking", "High"):
                summary_ja = summarize_ja_3lines(name, url, snippet, impact2)
                if not summary_ja:
                    print(f"[{impact2}] {name} : 要約生成に失敗（空のまま継続）")

            item_id = make_item_id(url, snippet)
            if item_id not in existing_ids:
                state.insert(
                    0,
                    {
                        "id": item_id,
                        "impact": impact2,
                        "name": name,
                        "url": url,
                        "snippet": snippet,
                        "diff": stats_for_state,
                        "score": score,
                        "reasons": reasons,
                        "summary_ja": summary_ja,
                        "pubDate": utc_now_rfc822(),
                    },
                )
                existing_ids.add(item_id)

            if log_diff_stats:
                print(
                    f"[{impact2}] {name} : 変更あり (score={score}, +{stats_for_state['added']}/-{stats_for_state['removed']}, churn={stats_for_state['churn']})"
                )
            else:
                print(f"[{impact2}] {name} : 変更あり (score={score})")
        else:
            if log_diff_stats:
                print(f"[{impact}] {name} : 変更なし (+0/-0, churn=0)")
            else:
                print(f"[{impact}] {name} : 変更なし")

    # 履歴は上限で刈る
    state = state[:MAX_ITEMS]
    save_state(state)


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
