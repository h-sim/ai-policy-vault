# AI Policy Vault

AIプラットフォーム（OpenAI など）の変更を監視し、**社内報告・監査対応向けの Markdown レポート**（開発中）を生成します。ノイズを最小化し、情シス/DX担当の報告・監査対応工数を削減します。

- 最優先：**ノイズ最小**（MVP方針：方針2）
- 目的：社内提出物（Markdown）と証跡（snapshot/hash）のエビデンスを生成する
- 主出力：Markdown レポート（開発中）

## 主機能：監査向け Markdown レポート（MVP 主役）

GitHub Actions が定期実行し、変化を検知するたびに `state.json` を更新します。
変化記録にはタイムスタンプとハッシュを付与し、社内提出物・証跡として利用できます。

> 「変化なし」「安全」とは断定しません。必ず「未検出（要目視確認）」として扱ってください（方針4）。

## 監視対象（MVP）

- OpenAI Developer Changelog RSS
- OpenAI News RSS
- OpenAI OpenAPI Spec（YAML）

## 免責

- 本ツールは「変更の検知・記録」を提供するもので、正確性を保証しません。
- 重要な判断は必ず一次情報（公式発表・原文）を確認してください。

## フィードバック / 問い合わせ

- 要望・不具合：GitHub Issues

---

## RSS 配信（legacy / optional）

凍結中（手動実行時のみ生成）。定期更新はされません。

- **Important**: https://h-sim.github.io/ai-policy-vault/feed.xml
- **All**: https://h-sim.github.io/ai-policy-vault/feed_all.xml

---

## 開発メモ

- `run_multi.py`：取得 / 正規化 / 差分検知 / `state.json` 更新（メイン処理）
- `generate_rss.py`：`state.json` → `feed.xml` / `feed_all.xml` 生成（legacy）
- `targets.py`：監視対象URL（TARGETS）

### ローカル実行（推奨：venv）

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

python run_multi.py --selftest --verbose
python run_multi.py --log-diff-stats
# RSS生成（任意・legacy）:
python generate_rss.py
```

### 運用

- GitHub Actions で定期実行し、`state.json` / スナップショットをコミット
- RSS 生成は手動実行（`workflow_dispatch`）時のみ（凍結）
- Actions Summary に Added / Suppressed / Targets を表示（運用状況が一目で分かる）

## Requests

- If you want us to monitor a specific source, file a request here: **[docs/REQUESTS.md](docs/REQUESTS.md)**
