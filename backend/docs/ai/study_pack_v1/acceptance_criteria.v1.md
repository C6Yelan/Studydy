# Acceptance Criteria — Study Pack v1

## 結構（硬性）
1. 輸出必須通過 `study_pack.schema.v1.json`（Draft 2020-12）驗證。
2. 所有 object 都不允許額外欄位（`additionalProperties=false`）。
3. Root 必填欄位：`schema_version`、`outline`、`key_points`、`glossary`、`quiz`、`story_nodes`。
4. `default` 不會在 validation 階段自動補值；它是 schema annotation。
5. Schema Gate 以 `python-jsonschema` 的 `Draft202012Validator` + `pytest` 驗證固定 golden samples。

## 非空 Gate（硬性）
1. Root 非空 Gate 使用 `anyOf`。
2. `key_points` / `glossary` / `quiz` / `story_nodes` 至少一個陣列要滿足 `minItems: 1`。
3. `outline` 不納入非空 Gate 判定。
4. 以上四個陣列全部為空時，視為不合格輸出。

## 陣列上限（硬性）
1. `outline.maxItems: 20`
2. `OutlineNode.children.maxItems: 10`
3. `key_points.maxItems: 20`
4. `glossary.maxItems: 40`
5. `GlossaryEntry.examples.maxItems: 5`
6. `quiz.maxItems: 20`
7. `story_nodes.maxItems: 20`
8. `StoryNode.choices.minItems: 2`、`StoryNode.choices.maxItems: 4`
9. `QuizItem.options.minItems: 4`、`QuizItem.options.maxItems: 4`、`QuizItem.options.uniqueItems: true`

## 文字欄位上限（硬性）
1. `language.maxLength: 16`
2. `OutlineNode.title.maxLength: 120`
3. `key_points[*].maxLength: 280`
4. `GlossaryEntry.term.maxLength: 80`
5. `GlossaryEntry.definition.maxLength: 500`
6. `GlossaryEntry.examples[*].maxLength: 280`
7. `QuizItem.question.maxLength: 300`
8. `QuizItem.options[*].maxLength: 120`
9. `QuizItem.explanation.maxLength: 500`
10. `StoryNode.scene.maxLength: 500`
11. `StoryNode.prompt.maxLength: 200`
12. `StoryNode.choices[*].maxLength: 120`

## Quiz 規則（硬性）
1. `QuizItem` 使用 `answer_index`（integer）取代 `answer`（string）。
2. `answer_index` 必須介於 `0..3`（含邊界）。
3. `options` 固定 4 個且不可重複（`uniqueItems: true`）。
4. `explanation` 若存在，必須同時存在 `answer_index`（`dependentRequired`）。
