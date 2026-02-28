# AI Policy Vault — 運用ガイド

> 対象読者: 情シス / DX 担当（毎日の確認 + 障害時の一次対応）
> このドキュメントはコードの変更なしに維持される「事実ベース」の運用手順書です。

---

## 1. 目的

毎日 UTC 0:00（JST 9:00）に GitHub Actions が自動実行され、監視対象の変化を検知・記録します。
運用者が毎日行うべき作業は **「最新 run が成功したかを確認する」** のみです。

- **確認先**: GitHub Actions の実行ログ / Job Summary
- **正式表現**: 変化が検出されなかった場合も「変化なし」とは断定しない。
  正式表現は **「未検出（要目視確認）」**。

---

## 2. 日次チェック

以下のコマンドをターミナルで実行します（`gh` CLI が必要）。

```bash
# 最新5件の実行状況を確認（success / failure）
gh run list --repo h-sim/ai-policy-vault --limit 5

# state.json の最新 run_at（今日の日付かを確認）
jq '.[0].run_at' state.json

# 最新 Job Summary を確認
LATEST=$(gh run list --repo h-sim/ai-policy-vault --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view "$LATEST" --repo h-sim/ai-policy-vault
```

### 「正常」の定義

| 確認項目 | 正常な状態 |
|---|---|
| 最新 run のステータス | `completed / success` |
| `jq '.[0].run_at' state.json` | 今日の日付 |
| Job Summary の表示 | "採用変更: N 件" が表示（N=0 も正常） |

> N=0 は「未検出（要目視確認）」。「変化なし」と断定しないこと。

---

## 3. 手動再実行

定期実行が失敗した場合、または任意のタイミングで実行したい場合は以下の手順で行います。

```bash
# ① 手動トリガー
gh workflow run "AI Policy Vault" --repo h-sim/ai-policy-vault

# ② 実行開始を確認
gh run list --repo h-sim/ai-policy-vault --limit 3

# ③ 進捗をリアルタイム確認（Ctrl-C で抜けられる）
gh run watch --repo h-sim/ai-policy-vault

# ④ 完了後に Job Summary 確認
LATEST=$(gh run list --repo h-sim/ai-policy-vault --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view "$LATEST" --repo h-sim/ai-policy-vault

# ⑤ 失敗時の詳細ログ確認
gh run view "$LATEST" --log --repo h-sim/ai-policy-vault
```

---

## 4. よくあるトラブルと対処

### 【障害 — Actions が失敗またはスキップが発生】

| # | 事象 | ログの手がかり | 対処 |
|---|---|---|---|
| 1 | fetch 失敗 | `[High] ... : 取得失敗（今回はスキップ）` | 手動再実行（上記3）。複数日続く場合は URL の生死を確認 |
| 2 | OpenAI API 失敗 | `要約生成に失敗（空のまま継続）` | OPENAI_API_KEY の有効性を確認。変化記録自体は state.json に保存済み |
| 3 | selftest FAIL | `[SELFTEST] RESULT: FAIL` / Actions step が赤 | ローカルで `--selftest --verbose` を実行して FAIL ケースを確認 |

**パターン1・2 の確認コマンド:**

```bash
# Actions ログから fetch 失敗 / 要約生成失敗のターゲットを確認
LATEST=$(gh run list --repo h-sim/ai-policy-vault --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view "$LATEST" --log --repo h-sim/ai-policy-vault | grep "取得失敗\|要約生成に失敗"

# OpenAI API key の存在確認
gh secret list --repo h-sim/ai-policy-vault
```

**パターン3 の確認コマンド:**

```bash
source .venv/bin/activate
python3 run_multi.py --selftest --verbose
```

---

### 【正常だが注意 — 「採用変更0件」が続く / SUPPRESS が多い】

| # | 事象 | 確認コマンド | 判断基準 |
|---|---|---|---|
| 4 | 採用変更0件が続く | `jq '.[0].run_at' state.json` | run_at が今日なら未検出（正常）。古い日付なら Actions 失敗を疑う |
| 5 | SUPPRESS 過多 | `jq '.[:10] \| .[] \| select(.impact=="Low") \| {run_at,name,reasons}' state.json` | `window_drop` / `bulk_update` は正常なノイズ抑制。reasons に高シグナルキーワードがないか目視確認 |

> 「0件 = 変化なし」と断定しない。正式表現は「**未検出（要目視確認）**」。

---

## 5. MVP 保証範囲と非保証

### 保証（CI 構造または外形的に観測可能な事実）

- 毎日 UTC 0:00（JST 9:00）に自動実行（`.github/workflows/main.yml:5`）
- selftest が失敗した場合、同一ジョブ内の後続ステップ（watcher 実行）はスキップされる
  （`.github/workflows/main.yml:36-39`、GitHub Actions デフォルトの step gating による）
- 変化を検知した場合、`state.json` に SHA-1 ID / `run_at` / `run_id` を付与して git コミット（証跡が残る）
- `snapshots/` は git 追跡対象。削除・`.gitignore` への追加は禁止

### 現状の実装での挙動（コード変更で変わりうる）

- fetch 失敗（タイムアウト・429・5xx）は当該ターゲットをスキップし、他ターゲットの処理を継続
  （`run_multi.py:1105-1107`）
- OpenAI API 失敗は空文字で継続し、`state.json` への変化記録は保全
  （`run_multi.py:872`）
- リトライは実装されていない（fetch 1回のみ）

### 非保証（MVP では対象外）

| 非保証項目 | 理由・補足 |
|---|---|
| 変化内容の正確性・完全性 | Detection のみ。Judgment は人間が実施 |
| rate limit（HTTP 429）の自動リトライ | 現状は1回のみ。再実行は手動対応 |
| Slack / Teams / Notion 等へのプッシュ通知 | Job Summary のみ。通知連携は MVP 対象外 |
| RSS / OpenAPI 以外のソース監視 | ToS ページ等は MVP 対象外 |
| 「変化の見逃しゼロ」の保証 | 正式表現は「未検出（要目視確認）」 |
