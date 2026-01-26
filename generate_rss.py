import os
import re
import json
import html
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


STATE_FILE = "state.json"

# RSS内の Diff 抜粋が肥大化しないように上限を設ける（文字数）
EXCERPT_LIMIT = int(os.environ.get("EXCERPT_LIMIT", "2000"))


def guess_base_url() -> str:
    # GitHub Actions では GITHUB_REPOSITORY=owner/repo が入る
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "http://localhost/"


def load_items() -> list:
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def rss_escape(s: str) -> str:
    return html.escape(s, quote=True)


def sanitize_xml_10(s: str) -> str:
    """XML 1.0で不正な制御文字を除去する（RSSパーサのエラー回避）"""
    if not s:
        return ""
    # 許可: TAB(0x09), LF(0x0A), CR(0x0D)
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)


def cdata_wrap(s: str) -> str:
    """CDATAを安全に包む。本文に ']]>' が含まれるとXMLが壊れるため分割する。"""
    s = sanitize_xml_10(s or "")
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def truncate_excerpt(s: str, limit: int = EXCERPT_LIMIT) -> str:
    """RSSに埋め込む差分抜粋を上限で切る（巨大diffでRSSが読めなくなるのを防ぐ）。"""
    s = s or ""
    if limit and len(s) > limit:
        return s[:limit] + "\n...(truncated)"
    return s


def openapi_highlights(diff_text: str, max_lines: int = 25) -> str:
    """OpenAPIの巨大diffをそのままRSSに載せず、読み手に価値が高い要素だけ抜粋する。"""
    diff_text = (diff_text or "").strip()
    if not diff_text:
        return ""

    patterns = [
        r"^[-+]\s*openapi:\s*.+$",
        r"^[-+]\s*version:\s*.+$",
        r"^[-+]\s*termsOfService:\s*.+$",
        r"^[-+]\s*servers:\s*$",
        r"^[-+]\s*-\s*url:\s*.+$",
        r"^[-+]\s*security:\s*$",
        r"^[-+]\s*tags:\s*$",
        r"^[-+]\s*-\s*name:\s*.+$",
        r"^[-+]\s*contact:\s*$",
        r"^[-+]\s*license:\s*$",
        r"^[-+]\s*url:\s*https?://.+$",
    ]
    rx = re.compile("(?:" + "|".join(patterns) + ")")

    picked = []
    for line in diff_text.splitlines():
        line = line.rstrip("\r")
        if rx.match(line.strip()):
            picked.append(line)
        if len(picked) >= max_lines:
            break

    # 何も拾えない場合は先頭数行だけ（ただし巨大なJSON差分は切り捨てたいので行数限定）
    if not picked:
        picked = diff_text.splitlines()[:min(max_lines, 10)]

    return "\n".join(picked).strip()


def item_datetime(it: dict) -> datetime:
    """state.json の item から比較用の日時を取得（なるべく堅牢に）。"""
    # 1) RFC822 pubDate（RSS形式）
    pub = (it.get("pubDate") or "").strip()
    if pub:
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    # 2) epoch seconds / timestamp らしき値
    for k in ("ts", "timestamp", "time"):
        if k in it:
            try:
                return datetime.fromtimestamp(float(it[k]), tz=timezone.utc)
            except Exception:
                pass

    # 3) それ以外は現在時刻
    return datetime.now(timezone.utc)


def latest_per_target(items: list) -> list:
    """ターゲット(URL)ごとに最新1件だけ残す（RSSの可読性を優先）。"""
    best = {}
    for it in items:
        key = (it.get("url") or it.get("name") or "").strip()
        if not key:
            continue
        dt = item_datetime(it)
        if key not in best or dt > best[key][0]:
            best[key] = (dt, it)

    # 最新順で返す
    return [pair[1] for pair in sorted(best.values(), key=lambda x: x[0], reverse=True)]


def build_feed(items: list, title: str, link: str, description: str) -> str:
    header = f"""<?xml version=\"1.0\" encoding=\"UTF-8\" ?>
<rss version=\"2.0\">
<channel>
<title>{rss_escape(title)}</title>
<link>{rss_escape(link)}</link>
<description>{rss_escape(description)}</description>
"""
    footer = """
</channel>
</rss>
"""

    out = [header]
    for it in items:
        item_title = f'[{it["impact"]}] {it["name"]}'
        item_link = it["url"]
        pub_date = it.get("pubDate") or datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

        # 炎上耐性：断定しない、公式で確認を促す
        snippet = it.get("snippet", "").strip()
        is_openapi = "openapi" in (it.get("name") or "").lower()
        summary_ja = (it.get("summary_ja") or "").strip()

        score = it.get("score", None)
        reasons = it.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = []

        # 重要度の根拠を1行で表示（購読者の理解と信頼のため）
        reason_line = ""
        if reasons:
            reason_text = ", ".join(str(r) for r in reasons if r)
            # 長すぎると読みにくいので軽く制限
            if len(reason_text) > 220:
                reason_text = reason_text[:220] + "…"
            if score is None:
                reason_line = f"Reason: {reason_text}<br/><br/>"
            else:
                reason_line = f"Reason: {reason_text} (score={score})<br/><br/>"

        # Diff抜粋が巨大化するとRSSが読めなくなるため上限を設ける
        snippet_raw = snippet if snippet else "(no snippet)"

        # OpenAPIは巨大diffになりやすいので、要点だけを抽出してRSS可読性を優先
        if is_openapi:
            snippet_raw = openapi_highlights(snippet_raw) or "(no highlight)"

        snippet_raw = truncate_excerpt(snippet_raw, EXCERPT_LIMIT)

        # RSSリーダーで改行が潰れることがあるため、表示は <br/> に統一
        snippet_disp = snippet_raw.replace("\n", "<br/>")
        summary_ja_disp = summary_ja.replace("\n", "<br/>")

        if summary_ja:
            # Important向け：日本語3行要約を優先
            desc = (
                "Summary (JA, 3 lines):<br/>"
                f"{summary_ja_disp}<br/><br/>"
                "Please verify on the official page.<br/>"
                f"Source: {it.get('url','')}<br/><br/>"
                + reason_line
                + ("Highlights (excerpt):<br/>" if is_openapi else "Diff (excerpt):<br/>")
                + f"{snippet_disp}"
            )
        else:
            desc = (
                "Detected a text change. Please verify on the official page.<br/><br/>"
                + reason_line
                + ("Highlights (excerpt):<br/>" if is_openapi else "Diff (excerpt):<br/>")
                + f"{snippet_disp}"
            )

        # RSSで改行を保ちたいので CDATA に入れる（XMLとして安全）
        desc_xml = cdata_wrap(desc)
        out.append(f"""
<item>
<title>{rss_escape(item_title)}</title>
<link>{rss_escape(item_link)}</link>
<description>{desc_xml}</description>
<pubDate>{rss_escape(pub_date)}</pubDate>
<guid isPermaLink=\"false\">{rss_escape(it["id"])}</guid>
</item>
""")

    out.append(footer)
    return "".join(out)


def main():
    base = guess_base_url()
    items = load_items()

    # 2系統：
    # 1) 重要（Breaking/High）
    important_raw = [it for it in items if it.get("impact") in ("Breaking", "High")]
    important = latest_per_target(important_raw)
    rss_important = build_feed(
        important,
        title="AI Change Watcher (Important)",
        link=base,
        description="Breaking/High changes only. Verify on official sources.",
    )
    with open("feed.xml", "w", encoding="utf-8") as f:
        f.write(rss_important)

    # 2) 全部（調査用）
    rss_all = build_feed(
        items,
        title="AI Change Watcher (All)",
        link=base,
        description="All detected text changes (may include noise). Verify on official sources.",
    )
    with open("feed_all.xml", "w", encoding="utf-8") as f:
        f.write(rss_all)

    print("RSS生成完了: feed.xml / feed_all.xml")


if __name__ == "__main__":
    main()
