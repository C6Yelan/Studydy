# Field Dictionary — Study Pack v1

> 先放骨架；下一步會把每個欄位的語意/格式/允許值/缺值規則補齊。

- schema_version (string, required): 固定為 "1.0"
- language (string, optional): 預設 zh-TW
- outline (array, required): 章節/小節結構
- key_points (array[string], required): 重點條列
- glossary (array[object], required): 詞彙表（term/definition/…）
- quiz (array[object], required): 題目（type/question/options/answer/…）
- story_nodes (array[object], required): 故事節點（scene/prompt/choices/…）
