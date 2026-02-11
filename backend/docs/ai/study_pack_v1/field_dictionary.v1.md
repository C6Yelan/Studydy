# Field Dictionary — Study Pack v1

本文件是 **Study Pack v1** 的「欄位字典」，用來讓後端入庫、前端渲染、測試驗收與資料標註都有一致的語意依據。
Schema 版本：`study_pack.schema.v1.json`（JSON Schema Draft 2020-12）。:contentReference[oaicite:1]{index=1}

---

## 0. 全域規則（適用所有 object）
- **不得出現未定義欄位**：所有 object 皆為 `additionalProperties: false`。:contentReference[oaicite:2]{index=2}
- `minLength: 1` 代表字串不得為空字串。:contentReference[oaicite:3]{index=3}
- `minItems / maxItems` 約束陣列長度。:contentReference[oaicite:4]{index=4}
- 除非特別說明，陣列欄位缺值規則皆為：**若沒有資料則給空陣列 `[]`**（不要用 `null`）。  

---

## 1. Root: StudyPack (object)

### 1.1 schema_version
- **必填**：是
- 型別：string
- 允許值：固定 `"1.0"`（const）
- 語意：用於版本控管與相容性判定

### 1.2 language
- **必填**：否
- 型別：string
- 預設：`"zh-TW"`
- 語意：輸出內容語言（主要影響文字敘述、題目語言）

### 1.3 outline
- **必填**：是
- 型別：array[OutlineNode]
- 預設：`[]`
- 語意：章節 / 小節階層結構（可遞迴巢狀）

### 1.4 key_points
- **必填**：是
- 型別：array[string]
- 每個 item 限制：`minLength=1`（不得空字串）:contentReference[oaicite:5]{index=5}
- 預設：`[]`
- 語意：可直接呈現在 UI 的重點條列

### 1.5 glossary
- **必填**：是
- 型別：array[GlossaryEntry]
- 預設：`[]`
- 語意：詞彙表（名詞解釋 / 定義）

### 1.6 quiz
- **必填**：是
- 型別：array[QuizItem]
- 預設：`[]`
- 語意：題目集合（v1 先支援單選 mcq）

### 1.7 story_nodes
- **必填**：是
- 型別：array[StoryNode]
- 預設：`[]`
- 語意：互動故事節點（情境 + 提示 + 選項）

---

## 2. OutlineNode (object)

### 2.1 title
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:6]{index=6}
- 語意：章節/小節標題

### 2.2 children
- **必填**：否
- 型別：array[OutlineNode]
- 預設：`[]`
- 語意：子節點（遞迴）

---

## 3. GlossaryEntry (object)

### 3.1 term
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:7]{index=7}
- 語意：詞彙（名詞）

### 3.2 definition
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:8]{index=8}
- 語意：詞彙定義（簡潔、可直接顯示）

### 3.3 examples
- **必填**：否
- 型別：array[string]
- 每個 item 限制：`minLength=1` :contentReference[oaicite:9]{index=9}
- 預設：`[]`
- 語意：例句/例子（可選）

---

## 4. QuizItem (object) — v1 僅支援 mcq

### 4.1 type
- **必填**：是
- 型別：string
- 允許值：`"mcq"`（enum）
- 語意：題型（v1 固定單選）

### 4.2 question
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:10]{index=10}
- 語意：題幹

### 4.3 options
- **必填**：是
- 型別：array[string]
- 限制：`minItems=2`、`maxItems=6`；每個選項 `minLength=1` :contentReference[oaicite:11]{index=11}
- 語意：選項列表（A/B/C/D… 由前端呈現方式決定）

### 4.4 answer
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:12]{index=12}
- 語意：正確答案（v1 先用「選項文字」對應；之後若要改 index，需升版）

### 4.5 explanation
- **必填**：否
- 型別：string
- 缺值規則：可省略；若存在建議非空（目前 schema 未強制）
- 語意：解題說明（可選）

---

## 5. StoryNode (object)

### 5.1 scene
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:13]{index=13}
- 語意：情境描述（引導使用者進入故事）

### 5.2 prompt
- **必填**：是
- 型別：string
- 限制：`minLength=1` :contentReference[oaicite:14]{index=14}
- 語意：當下要使用者做決策/回答的提示

### 5.3 choices
- **必填**：是
- 型別：array[string]
- 限制：`minItems=2`、`maxItems=6`；每個選項 `minLength=1` :contentReference[oaicite:15]{index=15}
- 語意：可選行動/回答（v1 不含「正確答案」，僅互動分支）

