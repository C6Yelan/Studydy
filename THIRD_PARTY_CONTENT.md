# 素材與第三方內容

本文件描述 repo 目前包含的內容，不替第三方作品指定或擴張授權。

## AI 生成介面素材

本專案角色與介面插圖為 AI 生成素材，生成工具**暫列為 OpenAI，待專案成員確認**。素材位於：

- [frontend/public/assets/Studydy_角色素材/](frontend/public/assets/Studydy_角色素材/)
- [frontend/public/assets/studydy/](frontend/public/assets/studydy/)

上述工具歸屬為暫定資訊，並非所有圖片均已通過來源或隱藏浮水印驗證。素材的對外使用授權尚未指定，待來源與適用條款確認後另行說明。

## 測試教材

[backend/tests/fixtures/](backend/tests/fixtures/) 使用自行建立的合成文字與文件；來源及生成工具見該目錄 README。測試資料不是可代表真實教材品質的 benchmark。

## 程式與依賴

容器使用的 [seccomp 規則](ops/docker/bubblewrap-seccomp.json) 改自 [Moby profiles](https://github.com/moby/profiles/blob/65adc7e022c97f55e45c054ff012988027733b87/seccomp/default.json)，保留預設規則並增加 bubblewrap 所需的 namespace 操作；原作採 [Apache License 2.0](ops/docker/MOBY-LICENSE)。

Python、npm 套件及外部模型的授權各自由其發行者提供；版本以 repo 的 lock 檔為準，不由本文件重新授權。

本 repo 尚未附上專案 LICENSE。素材權屬與專案授權需由維護者確認，不能由程式測試通過推導。
