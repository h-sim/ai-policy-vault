# Normalizer Plan — OpenAI API Changelog (HTML)

> 目的: HTML監視ターゲットで SUPPRESS が過多になった場合に、ノイズを減らすための normalizer 追加を
> 最小変更で実施できるようにする「設計メモ」。
> **このファイルは実装ではなく Plan です（このコミットではコード変更しない）。**

## 1. 発動条件（いつ実装するか）
- docs/OPERATIONS.md の「OpenAI API Changelog (HTML) ノイズ判定ルール」で
  「3日連続でSUPPRESS過多」などが成立したら、実装タスクを起票する。
- `stage=fetch FAIL` が主因のときは normalizer では解決しない（URL/取得の問題）。

## 2. 目標（何を減らすか）
- 変化検知は維持しつつ、HTMLの以下を削る：
  - ナビゲーション/ヘッダ/フッタ/サイドバー
  - 追跡/広告/埋め込み由来のノイズ
  - 「更新日時」など毎回変化する領域（あれば）

## 3. 追加する normalizer（案）
- normalizers.py に `html_openai_api_changelog_min` を追加
- 入力: HTML文字列
- 出力: 変更履歴（Changelog）本文に相当するテキスト

### 抽出方針（軽量・壊れにくさ優先）
- まず既存 extract_text() の結果をベースにする（追加依存を増やさない）
- 見出し（h1/h2/h3）と日付・箇条書き中心に残す
- 明確なセレクタがある場合のみ、最小限で使う（DOM変更に弱いので依存は控える）

## 4. 実装時の最小変更範囲（このコミットではやらない）
- normalizers.py: NORMALIZERS dict に追加
- targets.py: "OpenAI API Changelog (HTML)" に normalize 指定を追加
- selftest: 固定HTMLフィクスチャで入出力をスモーク（ネットワーク不要）

## 5. リスクとロールバック
- サイトDOM変更で抽出が壊れる可能性 → selftest + 運用観測で検知
- ロールバックは targets.py の normalize 指定を外すだけで可能
