# AI Policy Vault — 監視レポート（デモサンプル）

> **このファイルは LP 掲載用のデモサンプルです。すべての値は架空の例です。**
> 実際の運用では `reports/latest.md`（生成物、git 非追跡）が毎日自動生成されます。

---

## 0. このレポートで分かること

- **規約・仕様変更を自動検知し、Breaking / High / Medium / Low の影響度に分類する。**
- **そのまま社内共有できる Markdown 提出物と証跡（snapshot hash / item ID）を自動生成する。**
- **優先確認すべき変更が一覧化され、目視確認・法務判断のトリアージ工数を削減できる。**

> 変化の検知と優先順位付けを行うツールです。
> 内容の解釈・リスク評価（Judgment）は人間が実施します。本ツールは Detection のみを行います。

---

## 1. 基本情報

| 項目 | 値（例） |
|---|---|
| レポート生成日時（UTC） | `2026-03-01T00:00:00Z` |
| 対象ソース数 | 5 ソース |
| 採用変更 合計 | 3 件（Breaking 1 / High 1 / Medium 1） |
| 通知抑制（SUPPRESS） | 2 件（Low / window_drop） |
| run_id | `a1b2c3d4e5f6789012345678901234ab` |

---

## 2. 今回の採用変更 — 変更要点テーブル

> **「理由 / リスク観点」列は一次確認の優先順位付けです。法務判断・リスク評価ではありません。**
> **変化の解釈・最終判断は必ず人間が実施してください。**

| Impact | ソース（例） | 代表エントリ（例） | diff（例） | 理由 / リスク観点（例） |
|---|---|---|---|---|
| **Breaking** | OpenAI OpenAPI Spec | `security` スキーム変更を検知 | +3 / -5 | API認証スキームの変更。本番連携への影響要確認。最終判断は担当者が実施 |
| **High** | OpenAI Developer Changelog (RSS) | Pricing update for GPT-4o mini | +8 / -2 | 価格改定キーワードを検知。予算・契約への影響は担当者が目視で判断 |
| **Medium** | OpenAI News (RSS) | New fine-tuning capabilities announced | +12 / -0 | 新機能キーワードを検知。採用可否は担当者が判断 |

---

## 3. Target 別ステータス

| ソース名（例） | 状態 | Impact | 備考 |
|---|---|---|---|
| OpenAI OpenAPI Spec | 変更検知 | Breaking | Section 2 参照 |
| OpenAI Developer Changelog (RSS) | 変更検知 | High | Section 2 参照 |
| OpenAI News (RSS) | 変更検知 | Medium | Section 2 参照 |
| OpenAI Usage Policies (RSS) | 変更未検出 | — | 未検出（要目視確認） |
| OpenAI Platform Status (RSS) | 変更未検出 | — | 未検出（要目視確認） |

> 「変更未検出」は「変化なし」を断定しません。正式表現は **「未検出（要目視確認）」** です。

---

## 4. Evidence（証跡）

実際のレポートでは以下の証跡が自動付与されます。

### item ID（SHA-1）（例）

| item ID（例） | Impact | ソース（例） |
|---|---|---|
| `3a7f2c1d8e4b9f0a6c5d2e1b7a4f3c8d9e2a1b04` | Breaking | OpenAI OpenAPI Spec |
| `b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2cd` | High | OpenAI Developer Changelog (RSS) |
| `c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0` | Medium | OpenAI News (RSS) |

> item ID = SHA-1（url + "\n" + 圧縮前のフル diff snippet、UTF-8 エンコード）。同一内容の重複挿入は自動排除（冪等）。

### スナップショット hash（SHA-256）（例）

| ソース（例） | スナップショットファイル（例） | SHA-256（例） |
|---|---|---|
| OpenAI OpenAPI Spec | `snapshots/openai_openapi_spec.txt` | `e3b0c44298fc1c149afbf4c8996fb924...` |
| OpenAI Developer Changelog (RSS) | `snapshots/openai_developer_changelog_rss.txt` | `d8e8fca2dc0f896fd7cb4cb0031ba249...` |

> スナップショットは正規化済みテキストを git 追跡。hash でレポート生成時点のファイル内容を検証可能。

### run_at / run_id（例）

| フィールド | 値（例） | 意味 |
|---|---|---|
| `run_at` | `2026-03-01T00:00:00Z` | 検知実行時刻（ISO-8601 UTC） |
| `run_id` | `a1b2c3d4e5f6789012345678901234ab` | 実行識別子（uuid4().hex、32 桁 hex）。同一実行の全 item に共通 |

---

## 5. 免責（断定禁止）

- 本レポートは変化の **検知記録** です。「変化なし」「安全」とは断定しません。
- 変化が検出されなかった場合も、**「未検出（要目視確認）」** であり、変化がなかったことの保証ではありません。
- 変化内容の正確性・完全性は保証しません。必ず一次情報（公式発表・原文）で目視確認してください。
- 内容の解釈・リスク評価（Judgment）は人間が実施します。本ツールは Detection のみを行います。
- MVP フェーズでは rate limit の自動リトライ・Slack 等へのプッシュ通知は対象外です。

---

## 6. 次アクション（運用）

- **自動実行**: 毎日 UTC 0:00（JST 9:00）に GitHub Actions が自動実行します。
- **日次確認**: 最新 run のステータスと Job Summary を確認してください。
- **採用変更 0 件も正常**: 「0 件 = 変化なし」とは断定しません。正式表現は「未検出（要目視確認）」です。
- 手動再実行・トラブル対処・保証範囲の詳細は **[docs/OPERATIONS.md](./OPERATIONS.md)** を参照してください。
