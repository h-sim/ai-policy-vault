# AI Policy Vault — Master Spec

> このファイルは Claude Code の動作方針を定義する **Master Spec** です。
> 断片的な指示よりもこのファイルを優先してください。

---

## 1. プロダクト定義

**AI Policy Vault** は Python-only の変化検知システム。OpenAI を中心とした AI プラットフォームの更新を監視し、社内報告・監査対応用の Markdown レポートと証跡（snapshot/hash）を生成する。

- **目的**: 情シス/DX担当の「社内報告・監査対応」工数を最小化する。通知サービスではなく、提出物生成ツール。
- **出力**: Markdown レポート（`reports/latest.md`）、`state.json`（変化記録）、GitHub Actions Job Summary
- **実行**: GitHub Actions（`.github/workflows/main.yml`）で毎日 UTC 0:00 に自動実行

---

## 2. 不変条件

これらは変更・削除禁止。断片的な指示があっても守ること。

| 条件 | 内容 |
|---|---|
| **snapshots/ を追跡** | `snapshots/` と `state.json` は git 追跡対象。`.gitignore` に入れない。 |
| **生成物を追跡しない** | `reports/latest.md`、`run_multi.log` は生成物。`.gitignore` 済み。コミット対象外。 |
| **断定禁止** | 「変化なし」「安全」は使わない。必ず「未検出（要目視確認）」と表現する。 |
| **Detection のみ** | 変化の有無を検出・記録するのみ。内容の解釈・評価（Judgment）はしない。 |
| **selftest 維持** | 分類ロジック変更後は必ず `python3 run_multi.py --selftest` を PASS させる。 |

---

## 3. リポジトリ方針

1. **提供価値** — 社内提出物（Markdown）と証跡（snapshot/hash）のエビデンスを生成する。
2. **ノイズ最小優先** — 大量入替・ウィンドウ更新など実質的な変化のないケースは抑制する。
3. **証跡** — 全ての変化記録に timestamp と hash を付与する。
4. **出力形式** — Markdown 最優先。PDF/Word は MVP フェーズでは作らない。
5. **品質保証** — `--selftest` で回帰を防止。要約（OpenAI API）失敗時は空文字で継続し処理を止めない。
6. **対象追加** — 新しい監視対象を追加する前にプラグイン化・テスト化すること。
7. **MVP入力** — OpenAPI spec と公式 Changelog を中心とする。ToS/規約の監視は後回し。

---

## 4. Claude 作業ルール（不変）

**モデル**
- 基本: Sonnet。複雑な設計・判断が必要な場合は Opus への切替を提案する。

**作業順序**
```
まず plan（影響範囲と触るファイルを列挙）
→ 実装
→ git diff 提示
→ コミット（目的ごとに分割）
```

**コミット前チェックリスト（毎回必須）**

1. secrets 混入チェックを実行し、結果を報告する:
   ```
   rg -n "OPENAI_API_KEY|api[_-]?key|sk-[A-Za-z0-9]{10,}|BEGIN PRIVATE KEY" .
   ```
2. `git diff --cached --name-status` でステージ内容を確認し、無関係ファイルが混ざっていないことを確認する。
3. 分類ロジックに触った場合は `python3 run_multi.py --selftest` が PASS することを確認する。

**報告**
- done 条件がある指示は、完了後に表で各条件の達成状況を報告する。

---

## 5. アーキテクチャ

### パイプライン

```
Fetch → Normalize → Diff → Classify → Summarize → Persist → Report
```

**`run_multi.py`** — メインオーケストレーター。各ターゲットに対して:
1. Fetch → `normalizers.py` で正規化（`rss_min` or `openapi_c14n_v1`）
2. `snapshots/` のスナップショットと diff
3. `classify_impact()` で Breaking/High/Medium/Low を判定
4. Low・抑制対象はスキップ（ノイズ回避）
5. Breaking/High は OpenAI API で日本語3行サマリ生成
6. `state.json` に追加（SHA1 重複排除、最大50件）
7. スナップショット更新
8. 採用変更があれば `reports/latest.md` を生成

**`scripts/write_summary.py`** — `run_multi.log` と `reports/latest.md` を読み、GitHub Actions Job Summary に Markdown テーブルを書く。

**`targets.py`** — 監視対象の定義（URL、デフォルト impact、normalizer）。

### 状態ファイル

| ファイル | 追跡 | 説明 |
|---|---|---|
| `state.json` | ✅ | 最大50件の変化記録（id/impact/name/url/snippet/diff/reasons/summary_ja/pubDate） |
| `snapshots/*.txt` | ✅ | ターゲットごとの前回スナップショット |
| `reports/latest.md` | ❌ | 今回実行の採用変更レポート（生成物） |
| `run_multi.log` | ❌ | 実行ログ（生成物） |
| `feed.xml` / `feed_all.xml` | ❌ | RSS生成物（legacy/凍結中） |

### Impact 判定

| Level | 条件 |
|---|---|
| **Breaking** | OpenAPI version/server/security 変更、deprecat*/breaking/sunset/removed キーワード |
| **High** | pricing/security/major feature キーワード（Changelog/News） |
| **Medium** | News デフォルト、Changelog でシグナルあり |
| **Low** | シグナルなし → state には入るが reports から除外 |

---

## 6. 開発コマンド

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# 分類ルール検証（state 変更なし・安全）
python3 run_multi.py --selftest --verbose

# ローカル実行（OPENAI_API_KEY 要）
python3 run_multi.py --log-diff-stats

# Job Summary プレビュー
python3 scripts/write_summary.py
```

環境変数:
- `OPENAI_API_KEY` — 日本語3行サマリ生成（Breaking/High のみ）
- `SITE_URL` — RSS リンクのベース URL（未設定時は `GITHUB_REPOSITORY` から自動生成）
