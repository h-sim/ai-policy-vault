import os
import re
import json
import hashlib
from datetime import datetime, timezone
from difflib import unified_diff
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from targets import TARGETS



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
            dt = _first_child_text(ent, [f"{ns_atom}updated", f"{ns_atom}published", "updated", "published", "{*}updated", "{*}published"])
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

def classify_impact(name: str, url: str, snippet: str, default_impact: str) -> str:
    """
    重要度の自動判定（LLMなし）
    - ノイズ削減
    - 重要な変更の取りこぼし低減
    """
    n = (name or "").lower()
    u = (url or "").lower()
    s = (snippet or "").lower()

    # OpenAPI Spec: 差分が出たらBreaking固定（仕様変更の可能性が高い）
    if "openapi" in n or u.endswith((".yml", ".yaml")):
        return "Breaking"

    # Developer Changelog: 破壊的っぽい語があればBreakingに昇格
    if "changelog" in n:
        breaking_kw = [
            "breaking", "deprecat", "remove", "removed", "will be removed",
            "sunset", "sunsetting", "migration required", "end of life", "eol"
        ]
        if any(k in s for k in breaking_kw):
            return "Breaking"
        return "High"

    # News: 原則Medium、ただし重要語があればHighに昇格
    if "news" in n:
        high_kw = [
            "policy", "pricing", "price", "security", "terms", "compliance",
            "privacy", "trust", "safety", "enterprise"
        ]
        if any(k in s for k in high_kw):
            return "High"
        return "Medium"

    return default_impact


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


def main():
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
            new_text = "\n".join(
                line.rstrip() for line in new_text.replace("\r\n", "\n").splitlines()
            )

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
            impact2 = classify_impact(name, url, snippet, impact)

            # Important（Breaking/High）の変更だけ日本語3行要約（API失敗時は空で継続）
            summary_ja = ""
            if impact2 in ("Breaking", "High"):
                summary_ja = summarize_ja_3lines(name, url, snippet, impact2)
                if not summary_ja:
                    print(f"[{impact2}] {name} : 要約生成に失敗（空のまま継続）")

            item_id = make_item_id(url, snippet)
            if item_id not in existing_ids:
                state.insert(0, {
                    "id": item_id,
                    "impact": impact2,
                    "name": name,
                    "url": url,
                    "snippet": snippet,
                    "summary_ja": summary_ja,
                    "pubDate": utc_now_rfc822(),
                })
                existing_ids.add(item_id)

            print(f"[{impact2}] {name} : 変更あり")
        else:
            print(f"[{impact}] {name} : 変更なし")


    # 履歴は上限で刈る
    state = state[:MAX_ITEMS]
    save_state(state)


if __name__ == "__main__":
    main()
