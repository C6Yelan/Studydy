正式 DB 可能仍存著歷史格式的資料，這三個既有測試保護現行程式讀得懂這些資料。僅保留它們實際使用的 `0016_voice_podcast_context.sql`、`0031_remove_topic_level.sql` 與 `0032_remove_topic_exclusions.sql` 作為測試輸入；這些 SQL 不屬於產品的 migration 鏈，產品初始化與啟動不會執行。
