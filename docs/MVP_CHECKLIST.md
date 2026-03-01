# AI Policy Vault — MVP Checklist（進捗メーター）

このチェックリストは「今どこまで出来ているか」を可視化するためのものです。
各項目は **Done/Not yet** の2択で埋めます。

---

## 0. スコアの付け方（%）
- 全項目数 = ここにあるチェック数
- Done の数 / 全項目数 = 進捗%
- 迷ったら「運用で困らない状態か？」で判断（ドキュメントでもOK）

---

## 1. 箱（全体骨格）
- [ ] ARCHITECTURE があり、Evidence Store 仕様が明文化されている（docs/ARCHITECTURE.md）
- [ ] OPERATIONS があり、日次運用の手順がコピペで完結する（docs/OPERATIONS.md）
- [ ] REQUESTS があり、面談なしで依頼できる導線がある（docs/REQUESTS.md）
- [ ] README から REQUESTS へリンクがある（README.md）

---

## 2. 監視パイプライン（壊れにくさ）
- [ ] GitHub Actions が毎日 UTC 0:00 に動く（main.yml）
- [ ] selftest が PASS した場合のみ watcher が動く（運用上の回帰防止）
- [ ] 失敗しても他ターゲットが継続される（fetch失敗はスキップ）
- [ ] Summarizer 失敗でも空文字で継続し、記録は残る

---

## 3. 証跡（Evidence）
- [ ] snapshots/ が git 追跡され、証跡として残る
- [ ] state.json に item ID（SHA-1）が残る
- [ ] run_at / run_id が state.json に残る
- [ ] reports/latest.md は生成物として扱い、git 追跡しない（.gitignore）

---

## 4. 出力（見た目の最低ライン）
- [ ] Job Summary に実行サマリが出る（件数/内訳）
- [ ] 変更があるとき、テーブルが崩れない（セルsanitize）
- [ ] 健全性（ターゲット別）が Summary に出て、FAILの切り分けができる
- [ ] DEMO_REPORT があり、LP掲載用のサンプルが用意されている（docs/DEMO_REPORT.md）

---

## 5. "需要確認" の最低ライン（LPは作らない前提でもOK）
- [ ] README か（将来の）LPに「何ができる/できない」が明記されている
- [ ] "依頼（REQUESTS）→次のアクション"が面談なしで完結する
- [ ] 連絡先/受付窓口が明示されている（GitHub Issue / フォーム等、実装は任意）

---

## 6. 次に強化する候補（MVP外・今は未チェックでOK）
- [ ] HTML監視用 normalizer の追加（ノイズ抑制）
- [ ] リトライ/429対応
- [ ] Slack/Teams 通知
- [ ] 要約の品質向上（LLMはPro機能として後付け）
