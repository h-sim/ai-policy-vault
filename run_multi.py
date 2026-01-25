import os
import re
import json
import hashlib
from datetime import datetime, timezone
from difflib import unified_diff

import requests
from bs4 import BeautifulSoup
from targets import TARGETS


SNAPSHOT_DIR = "snapshots"
STATE_FILE = "state.json"
MAX_ITEMS = 50  # RSSに残す履歴数（多すぎると読まれない）


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
            # 長すぎる行は切る
            snippet_lines.append(line[:200])
        if len(snippet_lines) >= max_lines:
            break

    return "\n".join(snippet_lines).strip()


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

    if url.endswith((".xml", ".yml", ".yaml")):
        new_text = raw
    else:
        if "<html" in raw.lower() or "<!doctype html" in raw.lower():
            new_text = extract_text(raw)
        else:
            new_text = raw

    # ここで全形式共通の正規化（推奨）
    new_text = "\n".join(line.rstrip() for line in new_text.replace("\r\n", "\n").splitlines())

except Exception as e:

            # 取得失敗は“変更”扱いにしない（炎上耐性・運用安定）
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
            item_id = make_item_id(url, snippet)
            if item_id not in existing_ids:
                state.insert(0, {
                    "id": item_id,
                    "impact": impact,
                    "name": name,
                    "url": url,
                    "snippet": snippet,
                    "pubDate": utc_now_rfc822(),
                })
                existing_ids.add(item_id)

            print(f"[{impact}] {name} : 変更あり")
        else:
            print(f"[{impact}] {name} : 変更なし")

    # 履歴は上限で刈る
    state = state[:MAX_ITEMS]
    save_state(state)


if __name__ == "__main__":
    main()
