TARGETS = [
  {"impact": "High", "name": "OpenAI Developer Changelog (RSS)", "url": "https://developers.openai.com/changelog/rss.xml", "normalize": "rss_min"},
  {"impact": "Medium", "name": "OpenAI News (RSS)", "url": "https://openai.com/news/rss.xml", "normalize": "rss_min"},
  {"impact": "Breaking", "name": "OpenAI OpenAPI Spec (YAML)", "url": "https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml", "normalize": "openapi_c14n_v1"},
  # Anthropic: 公式 Platform Changelog（HTML, RSS なし）。
  # classify_impact の changelog ブランチ（"changelog" in name）で処理。
  # 初回はスナップショットのみ保存。
  {"impact": "High", "name": "Claude Platform Changelog", "url": "https://platform.claude.com/docs/en/release-notes/overview"},
]
