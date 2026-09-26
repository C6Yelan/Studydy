# 素材與第三方內容

本文件整理 Studydy 收錄的介面素材、測試文件與第三方設定，說明其來源及授權狀態。

## AI 生成介面素材

Studydy 的角色與介面插圖為 AI 生成素材，檔案位於：

- [frontend/public/assets/Studydy_角色素材/](frontend/public/assets/Studydy_角色素材/)
- [frontend/public/assets/studydy/](frontend/public/assets/studydy/)

生成工具及適用使用條款尚待維護者確認。目前尚未指定這些素材的對外使用授權。

## 測試教材

[backend/tests/fixtures/](backend/tests/fixtures/) 收錄自行建立的合成文字與文件，用於文件轉換、內容保留及來源定位測試。檔案來源與覆蓋內容見 [fixture 說明](backend/tests/fixtures/README.md)。

## 第三方設定

容器的 seccomp 設定改自 Moby profiles 的預設規則，增加 Bubblewrap 建立沙箱所需的 namespace 操作。

- **專案檔案**：[bubblewrap-seccomp.json](ops/docker/bubblewrap-seccomp.json)
- **上游來源**：[Moby profiles／seccomp/default.json](https://github.com/moby/profiles/blob/65adc7e022c97f55e45c054ff012988027733b87/seccomp/default.json)
- **原作授權**：Apache License 2.0，授權文字保留於 [MOBY-LICENSE](ops/docker/MOBY-LICENSE)。

## 套件與模型

Python 與 npm 依賴版本分別記錄於 [backend/uv.lock](backend/uv.lock) 與 [frontend/package-lock.json](frontend/package-lock.json)。模型版本與執行設定見 [runtime-lock.json](local_ai/runtime-lock.json)。各套件與模型適用其發行者提供的授權條款。

## 專案授權

目前尚未提供 Studydy 的專案 LICENSE。專案程式與介面素材的授權方式仍待維護者確認；上述第三方來源與授權資訊不代表整個專案採用相同授權。
