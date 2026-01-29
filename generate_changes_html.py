import json
import os
import html
from datetime import datetime, timezone


def guess_base_url() -> str:
    site = (os.environ.get("SITE_URL") or "").strip()
    if site:
        return site.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "http://localhost/"


def iso_to_human(s: str) -> str:
    if not s:
        return ""
    try:
        ss = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ss)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return s


def main() -> None:
    base_url = guess_base_url()
    with open("state.json", "r", encoding="utf-8") as f:
        state = json.load(f)

    # state.json は通常 list だが、将来の形式変更に備えて dict も吸収する
    if isinstance(state, dict):
        for k in ("items", "history", "events", "entries"):
            v = state.get(k)
            if isinstance(v, list):
                state = v
                break
        else:
            state = []
    elif not isinstance(state, list):
        state = []

    items = []
    for it in (state or []):
        if not isinstance(it, dict):
            continue
        impact = str(it.get("impact") or it.get("impact2") or "")
        source = str(it.get("name") or it.get("source") or "")
        url = str(it.get("url") or "")
        title = str(it.get("title") or it.get("item_title") or "")
        ts = str(it.get("ts") or it.get("time") or it.get("created") or it.get("created_at") or "")
        snippet = str(it.get("snippet") or "")
        snippet_full = str(
            it.get("snippet_full")
            or it.get("snippet_full_for_state")
            or it.get("snippet_full_for_id")
            or ""
        )
        reasons = it.get("reasons")
        if isinstance(reasons, list):
            reasons_s = " / ".join([str(x) for x in reasons if x])
        else:
            reasons_s = str(reasons or "")

        summary = it.get("summary")
        if not summary:
            summary = it.get("summary_ja")
        if not summary:
            summary = it.get("summary3")
        if not summary:
            summary = it.get("summary_3")
        summary_s = str(summary or "")

        items.append(
            {
                "impact": impact,
                "source": source,
                "url": url,
                "title": title,
                "ts": ts,
                "ts_h": iso_to_human(ts),
                "snippet": snippet,
                "snippet_full": snippet_full,
                "reasons": reasons_s,
                "summary": summary_s,
            }
        )

    # 新しい順（ts 降順）
    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    sources = sorted({x.get("source") for x in items if x.get("source")})

    def esc(s: str) -> str:
        return html.escape(s or "", quote=True)

    rows = []
    for it in items:
        title = it.get("title") or (it.get("snippet") or "").split("\n")[0] or "(no title)"
        src = it.get("source") or ""
        url = it.get("url") or ""
        ts = it.get("ts_h") or it.get("ts") or ""
        impact_txt = it.get("impact") or "—"
        reasons = it.get("reasons") or ""
        summary = it.get("summary") or ""
        diff_body = it.get("snippet_full") or it.get("snippet") or ""

        link_html = f'<a href="{esc(url)}" target="_blank" rel="noopener">公式/原文</a>' if url else ""
        summary_html = (
            f'<div class="small">要約: {esc(summary).replace("\n", "<br>")}</div>'
            if summary
            else ""
        )
        reasons_html = f'<div class="small">理由: {esc(reasons)}</div>' if reasons else ""
        diff_html = f'<details><summary class="small">差分（snippet）</summary><pre class="mono">{esc(diff_body)}</pre></details>' if diff_body else ""

        rows.append(
            "\n".join(
                [
                    '<div class="row">',
                    '  <div class="top">',
                    f'    <div class="meta">{esc(ts)}<br><span class="badge" data-impact="{esc(impact_txt)}">{esc(impact_txt)}</span></div>',
                    f'    <div class="meta">{esc(src)}</div>',
                    '    <div>',
                    f'      <p class="title">{esc(title)}</p>',
                    f'      <div class="links small">{link_html}</div>',
                    f'      {summary_html}' if summary_html else '',
                    f'      {reasons_html}' if reasons_html else '',
                    f'      {diff_html}' if diff_html else '',
                    '    </div>',
                    '  </div>',
                    '</div>',
                ]
            )
        )

    rows_html = "\n".join([r for r in rows if r.strip()])
    debug_static = f"debug_static: items={len(items)}, sources={len(sources)}"

    data_json = json.dumps(
        {"items": items, "sources": sources, "base_url": base_url}, ensure_ascii=False
    )
    # HTMLエスケープするとJSONが壊れて JSON.parse が落ちる。
    # <script>内に埋めるので、終了タグだけ潰して安全化。
    data_json_safe = data_json.replace("</", "<\\/")

    # NOTE: f-string にすると JS の `${...}` と衝突するので、プレーン文字列 + 置換で埋め込む
    out_html = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Change Watcher - Changes</title>
  <meta name="description" content="AIプラットフォーム変更の一覧（検索・フィルタ）" />
  <link rel="alternate" type="application/rss+xml" title="AI Change Watcher (Important)" href="feed.xml" />
  <link rel="alternate" type="application/rss+xml" title="AI Change Watcher (All)" href="feed_all.xml" />
  <style>
    :root { color-scheme: light dark; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "Hiragino Sans", "Noto Sans JP", sans-serif; margin: 0; line-height: 1.55; }
    .wrap { max-width: 1060px; margin: 0 auto; padding: 22px 16px; }
    header { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    h1 { margin: 0; font-size: 26px; }
    .sub { opacity: .8; margin: 0; }
    .bar { display: grid; grid-template-columns: 1fr; gap: 10px; margin: 14px 0 10px; }
    @media (min-width: 860px) { .bar { grid-template-columns: 1.2fr .6fr .6fr .6fr; } }
    input, select { font: inherit; padding: 10px 10px; border-radius: 10px; border: 1px solid rgba(127,127,127,.35); background: transparent; }
    .row { border: 1px solid rgba(127,127,127,.25); border-radius: 12px; padding: 12px; margin: 10px 0; }
    .top { display: grid; grid-template-columns: 1fr; gap: 6px; }
    @media (min-width: 860px) { .top { grid-template-columns: 160px 160px 1fr; align-items: start; } }
    .meta { opacity: .85; font-size: 13px; }
    .badge { display: inline-block; padding: 4px 10px; border-radius: 999px; border: 1px solid rgba(127,127,127,.35); font-size: 12px; font-weight: 700; }
    .badge[data-impact="Breaking"] { border-color: rgba(255, 59, 48, .65); background: rgba(255, 59, 48, .14); color: rgb(255, 59, 48); }
    .badge[data-impact="High"] { border-color: rgba(255, 149, 0, .65); background: rgba(255, 149, 0, .14); color: rgb(255, 149, 0); }
    .badge[data-impact="Medium"] { border-color: rgba(10, 132, 255, .65); background: rgba(10, 132, 255, .14); color: rgb(10, 132, 255); }
    .badge[data-impact="Low"] { border-color: rgba(142, 142, 147, .65); background: rgba(142, 142, 147, .14); color: rgb(142, 142, 147); }
    .title { font-weight: 650; margin: 0; }
    .links a { margin-right: 10px; }
    details pre { white-space: pre-wrap; overflow-wrap: anywhere; }
    .small { font-size: 13px; opacity: .85; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .count { font-size: 13px; opacity: .85; margin: 6px 0 0; }
    #debug_static, #debug { display: none; }
    #debug_static.show, #debug.show { display: block; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Changes</h1>
      <p class="sub">AI Change Watcher の変更一覧（検索・フィルタ）</p>
      <p class="sub"><a href="./">Home</a> · <a href="feed.xml">Important RSS</a> · <a href="feed_all.xml">All RSS</a></p>
    </header>

    <div class="bar">
      <input id="q" placeholder="検索（タイトル/差分/理由）" />
      <select id="impact">
        <option value="">Impact: All</option>
        <option value="Breaking">Breaking</option>
        <option value="High">High</option>
        <option value="Medium">Medium</option>
        <option value="Low">Low</option>
      </select>
      <select id="source">
        <option value="">Source: All</option>
      </select>
      <select id="limit">
        <option value="50">Latest 50</option>
        <option value="100" selected>Latest 100</option>
        <option value="200">Latest 200</option>
        <option value="500">Latest 500</option>
        <option value="1000">Latest 1000</option>
      </select>
    </div>

    <label class="small"><input id="hideLow" type="checkbox" checked /> 初期表示は Low を非表示（ノイズ最小）</label>
    <div class="count" id="count"></div>

    <div class="small mono" id="debug_static">__DEBUG_STATIC__</div>
    <div id="list">__ROWS__</div>
    <div id="empty" class="small" style="margin-top:10px;"></div>
    <div id="debug" class="small mono" style="margin-top:10px;"></div>

    <footer class="small" style="margin-top:18px; padding-top:12px; border-top:1px solid rgba(127,127,127,.25);">
      <div>※重要判断は一次情報（公式）を確認してください。</div>
    </footer>
  </div>

    <script id="data" type="application/json">__DATA_JSON__</script>
  <script>
    const elEmpty = document.getElementById('empty');
    const elDebug = document.getElementById('debug');
    const elDebugStatic = document.getElementById('debug_static');
    const isDebug = new URLSearchParams(location.search).get('debug') === '1';
    if (isDebug) {
      if (elDebug) elDebug.classList.add('show');
      if (elDebugStatic) elDebugStatic.classList.add('show');
    }
    window.addEventListener('error', (e) => {
      if (elEmpty) elEmpty.textContent = 'ERROR: ' + (e && e.message ? e.message : String(e));
    });
    window.addEventListener('unhandledrejection', (e) => {
      if (elEmpty) elEmpty.textContent = 'ERROR: ' + (e && e.reason ? String(e.reason) : String(e));
    });
    let data = {};
    try {
      data = JSON.parse(document.getElementById('data').textContent);
    } catch (e) {
      if (elEmpty) {
        elEmpty.textContent = 'ERROR: JSON parse に失敗（changes.html を再生成してください）';
      }
      console.error(e);
      data = { items: [], sources: [] };
    }
    const items = data.items || [];
    const sources = data.sources || [];
    if (isDebug && elDebug) {
      elDebug.textContent = `debug: items=${items.length}, sources=${sources.length}`;
    }

    const elQ = document.getElementById('q');
    const elImpact = document.getElementById('impact');
    const elSource = document.getElementById('source');
    const elLimit = document.getElementById('limit');
    const elHideLow = document.getElementById('hideLow');
    const elList = document.getElementById('list');
    const elCount = document.getElementById('count');

    // Populate sources
    for (const s of sources) {
      const opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s;
      elSource.appendChild(opt);
    }

    function esc(s) {
      return (s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }

    function match(it, q) {
      if (!q) return true;
      const hay = ((it.title||'') + '\n' + (it.summary||'') + '\n' + (it.snippet||'') + '\n' + (it.snippet_full||'') + '\n' + (it.reasons||'')).toLowerCase();
      return hay.includes(q);
    }

    function render() {
      const q = (elQ.value || '').trim().toLowerCase();
      const impact = elImpact.value || '';
      const source = elSource.value || '';
      const limit = parseInt(elLimit.value || '100', 10);
      const hideLow = !!elHideLow.checked;

      const filtered = [];
      for (const it of items) {
        if (hideLow && it.impact === 'Low') continue;
        if (impact && it.impact !== impact) continue;
        if (source && it.source !== source) continue;
        if (!match(it, q)) continue;
        filtered.push(it);
        if (filtered.length >= limit) break;
      }

      elCount.textContent = `表示: ${filtered.length} 件 / 全体: ${items.length} 件`;
      if (items.length === 0) {
        if (elEmpty) elEmpty.textContent = 'データが0件です。state.json の内容が changes.html に埋め込まれていない可能性があります（Actions生成物/コミットを確認）。';
        return;
      }

      const parts = [];
      for (const it of filtered) {
        const title = it.title ? esc(it.title) : esc((it.snippet||'').split('\n')[0] || '(no title)');
        const src = esc(it.source || '');
        const url = esc(it.url || '');
        const ts = esc(it.ts_h || it.ts || '');
        const impactTxt = esc(it.impact || '');
        const summary = it.summary ? esc(it.summary) : '';
        const reasons = it.reasons ? esc(it.reasons) : '';
        const diffBody = it.snippet_full || it.snippet || '';

        parts.push(`
<div class="row">
  <div class="top">
    <div class="meta">${ts}<br><span class="badge" data-impact="${impactTxt || ''}">${impactTxt || '—'}</span></div>
    <div class="meta">${src}</div>
    <div>
      <p class="title">${title}</p>
      <div class="links small">
        ${url ? `<a href="${url}" target="_blank" rel="noopener">公式/原文</a>` : ''}
      </div>
      ${summary ? `<div class="small">要約: ${summary.replace(/\n/g,'<br>')}</div>` : ''}
      ${reasons ? `<div class="small">理由: ${reasons}</div>` : ''}
      ${diffBody ? `<details><summary class="small">差分（snippet）</summary><pre class="mono">${esc(diffBody)}</pre></details>` : ''}
    </div>
  </div>
</div>`);
      }

      elList.innerHTML = parts.join('\n');
      if (filtered.length === 0) {
        const tips = [];
        if (hideLow) tips.push('「Low を非表示」をOFFにすると表示される場合があります。');
        tips.push('「Latest」を増やす（Latest 200/500）と過去分が出る場合があります。');
        if (elEmpty) elEmpty.innerHTML = '表示できる項目がありません。<br>' + tips.map(t => '・' + t).join('<br>');
      } else {
        if (elEmpty) elEmpty.textContent = '';
      }
    }

    elQ.addEventListener('input', render);
    elImpact.addEventListener('change', render);
    elSource.addEventListener('change', render);
    elLimit.addEventListener('change', render);
    elHideLow.addEventListener('change', render);

    render();
  </script>
</body>
</html>
"""

    out_html = out_html.replace("__DATA_JSON__", data_json_safe)
    out_html = out_html.replace("__ROWS__", rows_html)
    out_html = out_html.replace("__DEBUG_STATIC__", html.escape(debug_static, quote=True))

    with open("changes.html", "w", encoding="utf-8") as f:
        f.write(out_html)

    print("[SUMMARY] Wrote changes.html")


if __name__ == "__main__":
    main()
