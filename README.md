# AI Change Watcher

AIプラットフォーム（OpenAI など）の変更を監視し、**読む価値のある変更だけ**を優先して RSS で配信します。

- 最優先：**ノイズ最小**（MVP方針：方針2）
- 目的：仕様変更の追跡コストを下げ、意思決定を速くする

## フィード（購読URL）

- **Important（推奨）**: https://h-sim.github.io/ai-change-watcher/feed.xml
- **All（参考）**: https://h-sim.github.io/ai-change-watcher/feed_all.xml

### Important と All の違い

- **Important**：Breaking / High を中心に「読んで行動につながる」変更だけを出します（ノイズ抑制優先）。
- **All**：検知した変更を広めに出します（ノイズが混ざる可能性あり）。

> 補足：RSS のウィンドウ更新で「古い項目が落ちるだけ」など、価値が低い変化は抑制します。

## 使い方（購読手順）

1. Feedly / Inoreader / NetNewsWire などの RSS リーダーを用意
2. 上の URL を追加
3. まずは **Important** だけ購読するのがおすすめ

## 監視対象（MVP）

- OpenAI Developer Changelog RSS
- OpenAI News RSS
- OpenAI OpenAPI Spec（YAML）

## 免責

- 本フィードは「変更の検知・要約」を提供するもので、正確性を保証しません。
- 重要な判断は必ず一次情報（公式発表・原文）を確認してください。

## フィードバック / 問い合わせ

- 要望・不具合：GitHub Issues
- 「この変更はノイズ / 重要」などの調整要望も歓迎（Important の品質を最優先で改善します）

## Pro（予定）— 収益化の方向性

まずは **読者（見込み客）** を集めるため、Pro 機能の要望を募集中です。

- 例：メール通知 / Slack・Discord・Webhook / 日本語の短い影響サマリ / 週次まとめ / 過去検索

**Pro に興味がある場合**：GitHub Issues で `pro-interest` を含めて投稿してください（内容は1行でOK）。

---

## 開発メモ

- `run_multi.py`：取得 / 正規化 / 差分検知 / `state.json` 更新
- `generate_rss.py`：`state.json` → `feed.xml`（Important）/ `feed_all.xml`（All）生成
- `targets.py`：監視対象URL（TARGETS）

### ローカル実行（推奨：venv）

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

python run_multi.py --selftest --verbose
python run_multi.py --log-diff-stats
python generate_rss.py
```

### 運用

- GitHub Actions で定期実行し、GitHub Pages に配信
- Actions Summary に Added / Suppressed / Targets を表示（運用状況が一目で分かる）
