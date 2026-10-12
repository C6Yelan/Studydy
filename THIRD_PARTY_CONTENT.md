# 素材與第三方內容

## 介面素材

角色與插圖為 AI 生成素材，位於 [角色素材](frontend/public/assets/Studydy_角色素材/) 與 [介面素材](frontend/public/assets/studydy/)。生成工具、適用條款及對外使用授權仍待維護者確認。

## 測試文件

[合成文件](backend/tests/fixtures/README.md) 由專案自行建立，用於轉檔、內容保留與來源定位測試。

## 沙箱設定

[Bubblewrap seccomp 設定](ops/docker/bubblewrap-seccomp.json) 改自 [Moby profiles](https://github.com/moby/profiles/blob/65adc7e022c97f55e45c054ff012988027733b87/seccomp/default.json)，增加沙箱所需的 namespace 操作。原作採 Apache License 2.0，授權文字保留於 [MOBY-LICENSE](ops/docker/MOBY-LICENSE)。

## 套件、模型與專案授權

依賴版本見 [Python lock](backend/uv.lock)、[npm lock](frontend/package-lock.json)；模型設定見 [runtime lock](local_ai/runtime-lock.json)。各套件與模型適用其發行者條款。

Studydy 原創程式碼除另有標示外採 [MIT License](LICENSE) 授權。第三方元件、模型與介面素材仍依各自適用條款，不因專案採 MIT License 而改變其原授權狀態。

## 概念卡圖示與詞彙

[Tabler Icons 3.49.0](https://github.com/tabler/tabler-icons) 幾何資料採 MIT，完整文字在 [Tabler 授權](frontend/public/licenses/tabler-icons.txt)。
[CC-CEDICT / MDBG](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) 衍生的中文詞表及別名採 CC BY-SA 4.0；不將其改稱 MIT。[署名與修改說明](frontend/public/licenses/concept-icons.txt) 隨網站發佈，SVG 下載也保留署名連結。

圖庫在概念卡開啟時載入，全程本機詞彙與語境比對，不增加模型推論。版本、來源 hash 及生成方式見 [圖庫維護](ops/icons/README.md)。
