# AI Policy Vault — Architecture

> このファイルは「箱の仕様書」です。コンポーネント境界・Evidence Store 仕様・拡張の差し込み口を
> 一枚で確定します。断片的な指示よりも CLAUDE.md および本ファイルを優先してください。

---

## 1. Overview

**目的**: 社内報告・監査対応用の提出物（Markdown レポート）と証跡（snapshot/hash）を生成する。
OpenAI プラットフォームの更新（OpenAPI spec、Developer Changelog、News RSS）を毎日監視し、
変化を検知・記録する。

**非目標**:
- リアルタイム通知（Slack/email/webhook）— Delivery は別スクリプトに差し込む
- 変化の解釈・リスク評価（Judgment）— Detection のみ。評価はしない
- PDF/Word 生成 — MVP フェーズでは対象外
- RSS/OpenAPI 以外のスクレイピング — 当面は公式フィードのみ対象

**断定禁止**: 「変化なし」「安全」とは書かない。必ず「未検出（要目視確認）」と表現する。

---

## 2. Pipeline

```
┌───────────┐    ┌────────────┐    ┌──────┐    ┌────────────┐
│ Collector │ -> │ Normalizer │ -> │ Diff │ -> │ Classifier │
└───────────┘    └────────────┘    └──────┘    └────────────┘
                                                      |
    ┌────────────────┐    ┌─────────────┐    ┌────────────────┐
    │ Report/Deliver │ <- │    Persist  │ <- │   Summarizer   │
    └────────────────┘    └─────────────┘    └────────────────┘
                                  |
                         ┌────────────────┐
                         │ Evidence Store │
                         │ state.json     │
                         │ snapshots/     │
                         └────────────────┘
```

各ステージは独立した変換または I/O 操作。ステージは別プロセスではなく、
`run_multi.py` の `main()` ループ内でターゲットごとに順次実行される。

---

## 3. Components

### 3.1 Collector

| 項目 | 内容 |
|------|------|
| **実装** | `fetch()` in `run_multi.py` |
| **入力** | URL 文字列 |
| **出力** | 生レスポンステキスト（str） |
| **責務** | HTTP GET のみ。ブラウザ相当の UA ヘッダ、30s タイムアウト。非 2xx で例外 raise |
| **境界** | 生テキストを返すだけ。パースなし。キャッシュなし |

### 3.2 Normalizer

| 項目 | 内容 |
|------|------|
| **実装** | `normalizers.py` + `NORMALIZERS` dict in `run_multi.py` |
| **入力** | 生テキスト + `targets.py` の `"normalize"` キー |
| **出力** | 安定した正規化テキスト（diff 用） |
| **責務** | `rss_min`: title/link/id/date/body を #ITEM 単位で抽出し link+id+title でソート。<br>`openapi_c14n_v1`: YAML をパースして sort_keys=True の JSON に変換。<br>どちらも feed メタデータ（lastBuildDate 等）を除去してノイズを抑制する |
| **境界** | 純粋関数（テキスト in → テキスト out）。ネットワーク不可。状態なし。<br>失敗時は生テキストを返し、クラッシュしない |

### 3.3 Differ

| 項目 | 内容 |
|------|------|
| **実装** | `diff_snippet()`, `diff_stats()` in `run_multi.py` |
| **入力** | old_text（スナップショット）、new_text（今回の正規化テキスト） |
| **出力** | diff 抜粋文字列 + `{"added": int, "removed": int, "churn": int}` |
| **責務** | unified diff を生成。`IGNORE_DIFF_SUBSTRINGS`（lastBuildDate/generator/self-link）を除去してメタデータノイズを抑制する |
| **境界** | 純粋関数。ファイル I/O なし。状態変更なし |

### 3.4 Classifier

| 項目 | 内容 |
|------|------|
| **実装** | `classify_impact()` in `run_multi.py` |
| **入力** | name, url, snippet（diff）, default_impact（targets.py） |
| **出力** | `(impact: str, score: int, reasons: list[str])` |
| **責務** | キーワード/スコアリングで Breaking/High/Medium/Low を判定。<br>OpenAPI / Changelog / News の 3 ブランチ + Fallback。<br>ノイズ抑制: 大量更新（churn≥30 + シグナルなし）と RSS ウィンドウ脱落（削除のみ、追加なし、churn≤10）を Low+「通知抑制」として返す |
| **境界** | 純粋関数。I/O なし。`run_selftests()` で回帰防止 |

スコア閾値（変更時は全 selftest の期待値も更新すること）:

| スコア | Impact |
|--------|--------|
| ≥ 80 | Breaking |
| ≥ 50 | High |
| ≥ 20 | Medium |
| < 20  | Low |

### 3.5 Summarizer

| 項目 | 内容 |
|------|------|
| **実装** | `summarize_ja_3lines()` in `run_multi.py` |
| **入力** | name, url, snippet, impact |
| **出力** | 日本語 3 行サマリ文字列、または `""` |
| **責務** | Breaking/High のみ OpenAI `gpt-4.1-mini` を呼び出し、日本語 3 行サマリを生成する。<br>API 失敗・キー未設定時は `""` を返し、パイプラインを止めない |
| **境界** | ネットワーク I/O（OpenAI API）。`OPENAI_API_KEY` 環境変数が必要。フェールオープン設計 |

### 3.6 Evidence Store

| 項目 | 内容 |
|------|------|
| **実装** | `make_item_id()`, `load_state()`, `save_state()` in `run_multi.py`;<br>`snapshots/` ディレクトリ |
| **責務** | 変化記録（state.json）と正規化スナップショット（snapshots/）を維持する。<br>証跡の完全性を保証する（タイムスタンプ・ハッシュ付き） |
| **境界** | state.json と snapshots/ のみが 1 実行の可変出力。両方 git 追跡対象。<br>reports/latest.md と run_multi.log は生成物（非追跡）。<br>詳細仕様は Section 4 参照 |

### 3.7 Report Generator

| 項目 | 内容 |
|------|------|
| **実装** | `generate_markdown_report()` in `run_multi.py` |
| **入力** | new_items（今回採用した変更リスト）, run_at（ISO-8601 タイムスタンプ） |
| **出力** | `reports/latest.md`（採用変更が 1 件以上の場合のみ生成） |
| **責務** | ソース別にグループ化し Markdown で整形。スナップショットの SHA-256 ハッシュを証跡として埋め込む。Low と通知抑制を除外する。「断定禁止」ルールを適用する |
| **境界** | `reports/latest.md` のみを書き込む。state.json や snapshots/ は変更しない |

### 3.8 Delivery

| 項目 | 内容 |
|------|------|
| **実装** | `scripts/write_summary.py` + `.github/workflows/main.yml` |
| **責務** | `write_summary.py` が `run_multi.log` と `reports/latest.md` を読み、Markdown テーブルを生成して `$GITHUB_STEP_SUMMARY` に書き込む。<br>現在の配信先は GitHub Actions Job Summary のみ |
| **境界** | ログとレポートへの読み取りのみ。state.json/snapshots/ は変更しない。<br>Slack/Teams/Notion への追加は Section 5.4 参照 |

### 3.9 Config

| 項目 | 内容 |
|------|------|
| **実装** | `targets.py`、`.github/workflows/main.yml` |
| **責務** | `targets.py`：監視対象（url/name/default_impact/normalizer）を宣言する唯一のファイル。<br>`main.yml`：スケジュール（UTC 0:00 日次）・secrets・ステップ順序を定義する |
| **境界** | 監視対象の追加・変更は `targets.py` のみ行う。拡張手順は Section 5.1 参照 |

**現在の監視対象（`targets.py`）:**

| name | URL | impact | normalizer |
|------|-----|--------|------------|
| OpenAI Developer Changelog (RSS) | https://developers.openai.com/changelog/rss.xml | High | `rss_min` |
| OpenAI News (RSS) | https://openai.com/news/rss.xml | Medium | `rss_min` |
| OpenAI OpenAPI Spec (YAML) | https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml | Breaking | `openapi_c14n_v1` |
| Claude Platform Changelog | https://platform.claude.com/docs/en/release-notes/overview | High | なし（HTML → `extract_text()` フォールバック） |

> `Claude Platform Changelog` は Anthropic の公式 Platform Changelog（API 変更・モデル更新・Deprecation 等）。
> RSS が存在しないため HTML を直接取得し、`extract_text()` でテキスト変換する（`normalize` キー省略）。

### 3.10 Selftest

| 項目 | 内容 |
|------|------|
| **実装** | `run_selftests()` in `run_multi.py` |
| **責務** | 分類ルール・Evidence Store 仕様の回帰を防止する。state.json/snapshots/ には触れない |
| **実行** | `python3 run_multi.py --selftest --verbose` |
| **ルール** | Classifier（`classify_impact`）・Evidence Store（`make_item_id`）の変更後は必ず PASS させること |

---

## 4. Evidence Store Specification

Evidence Store は **state.json**（変化履歴）と **snapshots/**（ターゲットごとの正規化スナップショット）の 2 つで構成される。どちらも git 追跡対象。

### 4.1 state.json スキーマ（確定版）

最大 50 件（`MAX_ITEMS`）。古い item から順に削除される。
SHA-1 重複排除: 同じ id の item は二度挿入されない（冪等）。

```json
{
  "id":           "SHA-1 hex 40文字（Section 4.2 参照）",
  "impact":       "Breaking | High | Medium | Low",
  "name":         "ターゲット名（targets.py の name）",
  "url":          "フェッチ URL（targets.py の url）",
  "snippet":      "圧縮済み diff 抜粋（News は高シグナル行優先圧縮の場合あり）",
  "snippet_full": "圧縮前フル diff（圧縮した場合のみ）、それ以外は空文字",
  "diff":         {"added": 0, "removed": 0, "churn": 0},
  "score":        0,
  "reasons":      ["理由文字列"],
  "summary_ja":   "日本語 3 行サマリまたは空文字",
  "pubDate":      "検知実行時刻（RFC-822 UTC、後方互換フィールド）",
  "run_at":       "検知実行時刻（ISO-8601 UTC、例: 2026-03-01T00:00:00Z）",
  "run_id":       "実行識別子（uuid4().hex = 32 桁小文字 hex）"
}
```

**フィールド補足**:
- `snippet` は表示用圧縮済み。News で churn ≥ 20 の場合、`compact_news_snippet()` で高シグナル行が優先される
- `snippet_full` は圧縮が発生した場合のみ設定。それ以外は空文字
- `pubDate` と `run_at` は同一時刻を指す（Section 4.5 参照）
- `run_id` により同一実行で検知された変更をグループ化できる

### 4.2 item ID ハッシュ

```
アルゴリズム : SHA-1
入力        : url + "\n" + raw_snippet  （UTF-8 エンコード）
              raw_snippet = 圧縮前のフル diff snippet
出力        : 40 文字小文字 hex
```

```python
def make_item_id(url: str, snippet: str) -> str:
    h = hashlib.sha1()
    h.update((url + "\n" + snippet).encode("utf-8"))
    return h.hexdigest()
```

**重要**: `snippet_full` が存在する場合でも、`make_item_id()` には常に圧縮前 snippet を渡すこと。
これにより `compact_news_snippet()` のルール変更が item の重複挿入を引き起こさない。

**衝突ポリシー**: 算出した id が state.json に既に存在する場合、新規 item は破棄される（冪等）。

### 4.3 スナップショット ハッシュ（レポート内証跡）

```
アルゴリズム : SHA-256
入力        : スナップショットファイル（snapshots/<slug>.txt）のバイト列
出力        : reports/latest.md の各ソースセクションに埋め込み
              "スナップショット hash（SHA-256, new）: <hex>"
```

目的: レポートとリポジトリのスナップショットを照合して、生成時点のファイル内容を検証できるようにする。

### 4.4 スナップショット内容

**場所**: `snapshots/<slugified-name>.txt`
  - slugify: lowercase、スペース→アンダースコア、英数字以外を除去
  - 例: `"OpenAI News (RSS)"` → `openai_news_rss.txt`

**内容**: Normalizer の出力（正規化済みテキスト）。生 HTTP レスポンスは保存しない。

**更新タイミング**:
- 採用された変化（impact が Low 以外 or 通知抑制なし）: スナップショット更新 + state.json 更新
- 抑制された変化（Low + 通知抑制）: スナップショット更新のみ（同じノイズが次回再検知されないよう）
- 変化なし（diff が空）: スナップショット更新なし

### 4.5 run metadata フィールドの区別

| フィールド | フォーマット | 意味 |
|------------|-------------|------|
| `pubDate`  | RFC-822 UTC | 検知実行時刻（後方互換。RSS 標準フォーマット） |
| `run_at`   | ISO-8601 UTC | 検知実行時刻（機械可読。YYYY-MM-DDTHH:MM:SSZ） |
| `run_id`   | 32 桁小文字 hex | 実行識別子（`uuid.uuid4().hex`） |

- `pubDate` と `run_at` は同一時刻。記事の公開日ではない
- 記事公開日は `snippet` 内の `date:` 行に埋め込まれている（rss_min 正規化の出力）
- `run_id` は `main()` 冒頭で 1 回だけ生成。同一実行の全 item が同じ `run_id` を持つ

---

## 5. Extension Points

### 5.1 新しい監視対象を追加する

`targets.py` の `TARGETS` リストに 1 dict を追加するだけ:

```python
{
    "name": "表示名（レポートとログに使用）",  # slugify でスナップショットファイル名になる
    "url":  "https://example.com/feed.xml",
    "impact": "High",                           # デフォルト impact: Breaking|High|Medium|Low
    "normalize": "rss_min",                     # 省略可。Section 5.2 参照
}
```

**制約**:
- `name` はユニークにすること（スナップショットのファイル名が衝突する）
- 追加後に `python3 run_multi.py --selftest` を PASS させること
- 新ターゲット用に新しい normalizer が必要な場合は Section 5.2 を先に実施する

### 5.2 新しい Normalizer を追加する

1. `normalizers.py` に関数を実装する:
   ```python
   def normalize_my_format(text: str) -> str:
       # 純粋関数。ネットワーク不可。失敗時は text を返してクラッシュしない
       ...
   ```

2. `run_multi.py` の `NORMALIZERS` dict に登録する:
   ```python
   NORMALIZERS = {
       "rss_min":          lambda text: normalize_rss_min(text, body_limit=0),
       "openapi_c14n_v1":  normalize_openapi_c14n_v1,
       "my_format":        normalize_my_format,   # ← 追加
   }
   ```

3. `targets.py` から `"normalize": "my_format"` で参照する

4. `run_multi.py --selftest` を PASS させること

### 5.3 分類ルールを拡張する（`classify_impact()`）

`classify_impact()` in `run_multi.py` は OpenAPI / Changelog / News の 3 ブランチを持つ。

ルール追加手順:
1. 対象ブランチ（またはの新ブランチ）にキーワードリストとスコア増分を追加する
2. `run_selftests()` の `tests` リストに新ルールをカバーするテストケースを追加する
3. `python3 run_multi.py --selftest --verbose` を PASS させてからコミットする

**スコア閾値は変更禁止**（Breaking≥80 / High≥50 / Medium≥20 / Low<20）。閾値を変える場合は
全 selftest の `expect_score` / `expect_score_min` を同時に更新すること。

### 5.4 配信チャネルを追加する（Slack/Teams/Notion — 差し込み口）

現在の配信: GitHub Actions Job Summary のみ（`scripts/write_summary.py`）。

追加手順:
1. `scripts/notify_<channel>.py` を新規作成する
2. `state.json`（最新 N 件）または `reports/latest.md` を読み込む
3. `.github/workflows/main.yml` の "Run watcher" ステップの後に新ステップを追加する:
   ```yaml
   - name: Notify Slack
     if: env.SLACK_WEBHOOK_URL != ''
     env:
       SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
     run: python3 scripts/notify_slack.py
   ```

**制約**: `run_multi.py` に配信ロジックを追加しない。配信は `scripts/` 以下に分離すること。
`generate_markdown_report()` 内にネットワーク呼び出しを追加しない。

---

## 6. Immutable Constraints

CLAUDE.md で定義され、断片的な指示よりも優先される不変条件。

| 条件 | 内容 |
|------|------|
| **snapshots/ を追跡** | `snapshots/` と `state.json` は git 追跡対象。`.gitignore` に入れない |
| **生成物を追跡しない** | `reports/latest.md`、`run_multi.log` は生成物。`.gitignore` 済み。コミット対象外 |
| **断定禁止** | 「変化なし」「安全」は使わない。必ず「未検出（要目視確認）」と表現する |
| **Detection のみ** | 変化の有無を検出・記録するのみ。内容の解釈・評価（Judgment）はしない |
| **selftest 維持** | 分類ロジックまたは Evidence Store 仕様変更後は必ず `python3 run_multi.py --selftest` を PASS させる |
| **モジュール分割禁止** | `run_multi.py` は意図的にモノリシック。別アーキテクチャ決定なしに分割しない |
| **targets.py が唯一の設定** | 監視対象の追加・変更は `targets.py` のみで行う |
