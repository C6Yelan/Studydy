# 素材與第三方內容

## 介面素材

角色與插圖為 AI 生成素材，位於 [角色素材](frontend/public/assets/Studydy_角色素材/) 與 [介面素材](frontend/public/assets/studydy/)。生成工具、適用條款及對外使用授權仍待維護者確認。

## 測試文件

[合成文件](backend/tests/fixtures/README.md) 由專案自行建立，用於轉檔、內容保留與來源定位測試。

## 沙箱設定

[Bubblewrap seccomp 設定](ops/docker/bubblewrap-seccomp.json) 改自 [Moby profiles](https://github.com/moby/profiles/blob/65adc7e022c97f55e45c054ff012988027733b87/seccomp/default.json)，增加沙箱所需的 namespace 操作。原作採 Apache License 2.0，授權文字保留於 [MOBY-LICENSE](ops/docker/MOBY-LICENSE)。

## 套件、模型與專案授權

依賴版本見 [Python lock](backend/uv.lock)、[npm lock](frontend/package-lock.json)；模型設定見 [runtime lock](local_ai/runtime-lock.json)。各套件與模型適用其發行者條款。

Studydy 尚未提供專案 LICENSE；第三方元件的授權不代表整個專案或介面素材採用相同授權。
