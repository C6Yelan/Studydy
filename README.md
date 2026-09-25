# Studydy

開發與執行都使用這份 checkout。**本機跑 backend、frontend、持久化 PostgreSQL 與 PDF store；Pod 只提供 Gemma 4 AI。**
現行本機服務以 Linux 為執行環境；Windows／macOS 尚無原生啟動流程。換一台 Linux 主機時，須重新配置本機依賴及私密設定。

新對話或恢復環境時，先讀 [本地環境說明](docs/local-environment.md)，再從本目錄執行：

```bash
git status --short --branch
python3 ops/local/manage.py status
```

`status` 只讀本機程序、socket 與 Docker 狀態，不呼叫模型 API。

```bash
python3 ops/local/manage.py start
```

瀏覽器入口：<http://127.0.0.1:4173>。`start` 使用正式 backend 與 worker，Pod 離線仍可使用本機功能；AI 操作不可用時回報錯誤。資料留在既有 product DB，不建立 disposable DB。

- [語意模型設定（HTTP）](docs/local-environment.md#語意模型設定)
- [資料、私密設定、啟停與換 Pod](docs/local-environment.md)
- [帳號與登入](docs/accounts.md)
- [測試與模型 qualification](docs/testing.md)
