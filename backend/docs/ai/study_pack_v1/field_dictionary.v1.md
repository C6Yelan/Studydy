# Field Dictionary — Study Pack v1

Schema 版本：`study_pack.schema.v1.json`（JSON Schema Draft 2020-12）。

## 0. 全域規則
- 所有 object 都是 `additionalProperties: false`，不得夾帶未定義欄位。
- 所有陣列欄位若無資料請回 `[]`，不要用 `null`。
- Root 非空 Gate 使用 `anyOf`：`key_points` / `glossary` / `quiz` / `story_nodes` 至少一個陣列要有資料（`minItems: 1`，不含 `outline`）。
- 文字欄位統一使用 `minLength: 1`（若該欄位可空則可省略欄位本身），並加上 `maxLength` 上限。
- `default` 僅為 annotation；validation 時不會自動補值。

## 1. Root: StudyPack

### 1.1 `schema_version`
- 必填：是
- 型別：string
- 限制：`const: "1.0"`、`maxLength: 3`

### 1.2 `language`
- 必填：否
- 型別：string
- 限制：`minLength: 2`、`maxLength: 16`
- 預設：`"zh-TW"`

### 1.3 `outline`
- 必填：是
- 型別：array[`OutlineNode`]
- 限制：`maxItems: 20`
- 預設：`[]`

### 1.4 `key_points`
- 必填：是
- 型別：array[string]
- 限制：`maxItems: 20`；item `minLength: 1`、`maxLength: 280`
- 預設：`[]`

### 1.5 `glossary`
- 必填：是
- 型別：array[`GlossaryEntry`]
- 限制：`maxItems: 40`
- 預設：`[]`

### 1.6 `quiz`
- 必填：是
- 型別：array[`QuizItem`]
- 限制：`maxItems: 20`
- 預設：`[]`

### 1.7 `story_nodes`
- 必填：是
- 型別：array[`StoryNode`]
- 限制：`maxItems: 20`
- 預設：`[]`

## 2. OutlineNode

### 2.1 `title`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 120`

### 2.2 `children`
- 必填：否
- 型別：array[`OutlineNode`]
- 限制：`maxItems: 10`
- 預設：`[]`

## 3. GlossaryEntry

### 3.1 `term`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 80`

### 3.2 `definition`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 500`

### 3.3 `examples`
- 必填：否
- 型別：array[string]
- 限制：`maxItems: 5`；item `minLength: 1`、`maxLength: 280`
- 預設：`[]`

## 4. QuizItem（v1 僅支援 `mcq`）

### 4.1 `type`
- 必填：是
- 型別：string
- 允許值：`"mcq"`

### 4.2 `question`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 300`

### 4.3 `options`
- 必填：是
- 型別：array[string]
- 限制：`minItems: 4`、`maxItems: 4`、`uniqueItems: true`；item `minLength: 1`、`maxLength: 120`
- 缺值規則：不可省略，不可為空陣列

### 4.4 `answer_index`
- 必填：是
- 型別：integer
- 限制：`minimum: 0`、`maximum: 3`
- 語意：對應 `options` 的正確索引

### 4.5 `explanation`
- 必填：否
- 型別：string
- 限制：`minLength: 1`、`maxLength: 500`
- 依賴規則：若存在 `explanation`，必須存在 `answer_index`（`dependentRequired`）

## 5. StoryNode

### 5.1 `scene`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 500`

### 5.2 `prompt`
- 必填：是
- 型別：string
- 限制：`minLength: 1`、`maxLength: 200`

### 5.3 `choices`
- 必填：是
- 型別：array[string]
- 限制：`minItems: 2`、`maxItems: 4`；item `minLength: 1`、`maxLength: 120`
