TARGETS = [
  # [一時停止 2026-02-26〜: HTTP 404 が4日以上継続。新URL確認後に復元すること]
  # 旧URL: https://developers.openai.com/changelog/rss.xml
  # {"impact": "High", "name": "OpenAI Developer Changelog (RSS)", "url": "https://developers.openai.com/changelog/rss.xml", "normalize": "rss_min"},
  # OpenAI API Changelog（HTML, RSS なし）。旧 RSS（404）の代替として追加。
  # classify_impact の changelog ブランチ（"changelog" in name）で処理。
  # normalize 未指定 → extract_text() フォールバック。ノイズが3日以上続く場合は normalizer 追加を別タスクで検討。
  {"impact": "High", "name": "OpenAI API Changelog (HTML)", "url": "https://developers.openai.com/api/docs/changelog"},
  {"impact": "Medium", "name": "OpenAI News (RSS)", "url": "https://openai.com/news/rss.xml", "normalize": "rss_min"},
  {"impact": "Breaking", "name": "OpenAI OpenAPI Spec (YAML)", "url": "https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml", "normalize": "openapi_c14n_v1"},
  # Anthropic: 公式 Platform Changelog（HTML, RSS なし）。
  # classify_impact の changelog ブランチ（"changelog" in name）で処理。
  # 初回はスナップショットのみ保存。
  {"impact": "High", "name": "Claude Platform Changelog", "url": "https://platform.claude.com/docs/en/release-notes/overview"},
  # Google: Vertex AI 公式リリースノート（Atom feed）。
  # cloud.google.com → docs.cloud.google.com へ 301 リダイレクト（requests が自動追従）。
  # classify_impact は else → default_impact（High）。
  # "deprecat"/"sunset"/"removed" キーワードがあれば Breaking に escalate される。
  {"impact": "High", "name": "Google Vertex AI Release Notes (RSS)", "url": "https://cloud.google.com/feeds/vertex-ai-release-notes.xml", "normalize": "rss_min"},
]
