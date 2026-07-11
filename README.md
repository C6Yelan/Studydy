# Studydy

Studydy 是一個畢業專題，核心目標是讓 Agent 從教材中產生可追溯的 Concept、Relation、Knowledge Map 與 Learning Path。

目前專案採用 Agent-first、Slice-based 開發，從 S0 的工作流與 Evaluation 基準開始；S0 完成前不建立 Agent、資料庫、API 或前端功能。

## S0 Gate

```bash
python3 scripts/workflow_gate.py evaluation/s0/pass.json
python3 -m unittest discover -s tests -v
```

私人規劃、教材、Golden Set 與角色 handoff 放在本機共用的 `docs_local/`，不得提交 Git。
