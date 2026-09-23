# 帳號與固定學習身分

先依 [工作站啟停說明](runbook/A40_FINAL_WORKSTATION.md) 準備既有本地服務。
帳號與既有資料讀取不要求模型在線；分析與出題仍保留各自執行時的模型檢查。

## Migration

先停止產品寫入；若資料需要保留，人工私下備份原 DB 與完整 artifact store（原檔、轉換後 PDF 及來源 mapping）。
保持既有 `STUDYDY_DATABASE_DSN` 和 `STUDYDY_ARTIFACT_ROOT`，不要清空或更換位置。
在已設定私有環境欄位的 shell，從 repository root 執行：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -c 'from runtime.storage.migrations import run_migrations; print(run_migrations())'
```

空 DB 依 `load_migrations()` 順序套用全部 migration；既有 DB 先核對 ledger/checksum，再套用全部未執行版本。
本文件更新基準包含 `0001`～`0014`，不是只升級到 `0004`；已全部套用時重跑回傳 `()`。
所有歷史 SQL 與 checksum 保持原樣，不刪除 ledger，也不以重新建立資料庫取代升級。

### 歷史背景：0004 Email cutover

以下是當時 `0004` 的一次性資料處理，不是每次啟動或每次 migration 都清除帳號。
`0004` 將 credential column 從 `username` 改為唯一的 `email`，不保留 username alias。
依此次 pre-release cutover 決定，舊帳號的 credentials 清除、所有尚有效的舊 sessions 撤銷；
需要重新以 Email 註冊。`learner_id`、教材、Knowledge Structure、學習與作答資料保留原 owner，
不刪除、不自動歸戶到新帳號，也不推導假的 Email。這不是長期的雙登入或相容 reader。
在正式資料上套用前先完成備份，停止舊版本的產品程序；舊程式不能搭配新的 Email schema。

## 使用

1. 開啟 Studydy，選「立即註冊」。Email 作為唯一登入 identifier，不分大小寫。
2. 密碼為 15–128 個字元，可包含空格，註冊時需再次確認；沒有密碼重設服務，請自行妥善保存。
3. 註冊成功後進入首頁，可從側邊導覽前往教材庫；右上角「登出」只撤銷本次授權，不刪除資料。
4. 新瀏覽器輸入相同帳密，後端會取得同一 learner。其他瀏覽器的有效 session 可繼續使用。
5. 後端保留 7 天 idle／最長 30 天期限；前端登入提示不延長授權，過期需重新登入。失敗的上傳／作答
   不會自動重送，請登入後明確操作。登出失敗時私有畫面仍清空，請按「再試一次」完成登出。

登入後可從[教材庫](material-library.md)找回教材；可[接續原學習並查看題目與作答](learning-resume.md)。原有直接網址仍受後端 owner 檢查保護。
不再使用未區分帳號的 localStorage 最近教材指標。切換帳號會清除頁面內私有狀態；同 origin
其他分頁會收到身分變更通知，須重新登入後再操作。

## API

所有 mutation 必須帶符合現有設定的 `Origin`，仍使用 HttpOnly、SameSite=Strict cookie；
除數字 loopback HTTP 的 local profile 外要求 Secure。API 和 PDF 回應均為 `private, no-store`。

| Method / path | 行為 |
|---|---|
| `POST /v1/accounts` | JSON `{email, password}`；201，建立帳號並登入 |
| `POST /v1/session/login` | 同樣 JSON；200，驗證帳密並登入原 learner |
| `GET /v1/session` | 200，回傳 `learner-identity/v1` 與 `learner_id`；無有效 session 為 401 |
| `POST /v1/session/refresh` | 空 body；200，延長仍有效的既有 session，並直接回傳 `learner-identity/v1` |
| `DELETE /v1/session` | 空 body；204，冪等撤銷本次 session 並移除 cookie |

舊匿名 `POST /v1/session` 已移除。帳密錯誤統一回 `INVALID_CREDENTIALS`，Email 重複回
`ACCOUNT_UNAVAILABLE`，不回傳 password hash 或 session token JSON。
Email 使用 Pydantic `EmailStr` 與 [email-validator](https://pypi.org/project/email-validator/) 驗證格式，
移除首尾空白、採用 library 的 Unicode/domain 正規化後轉小寫，再持久化或 lookup。
不做 DNS／deliverability 查詢、Email verification、OTP 或寄信；不將 Email 當成已驗證的信箱所有權。
格式錯誤回傳 `INVALID_EMAIL`，前端關聯回 Email 欄位；不暴露 validator 原始輸入或例外細節。
DB 唯一約束保護 concurrent registration；不存在 Email 與錯誤密碼都回傳相同 `INVALID_CREDENTIALS`。
Password scrypt 成本、隨機 salt、constant-time digest comparison、session entropy／期限與 owner isolation 不變。

表單使用 `noValidate` 搭配 Studydy inline errors；Email 保留 `type=email`、`autocomplete=username`，
密碼保留 current-password／new-password。原生 constraint 語意保留，但不使用 browser validation popup。

密碼使用標準函式庫 scrypt（N=2^17、r=8、p=1、隨機 16-byte salt），參數依
[OWASP Password Storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)。
僅新增 Email 格式驗證所需的 email-validator 與其依賴；未加入 OAuth、MFA、進階限流或第二套 identity system。

[本地帳號測試方式](testing.md#account-regression-local-only) 不需要雲端 pod 或模型啟動。

登入／註冊的版型與保留的功能差異見[帳號入口視覺](auth-visual.md)。

前端成功登入後只保存非憑證的 learner identity 提示。重新整理／回到頁面時直接以提示還原 application frame，依實際資料 API 的 401 清除失效狀態；不做 focus 或定時登入檢測。沒有提示時才以一次 refresh 還原仍有效的 cookie。資料內容不以這份提示授權，token 仍是 HttpOnly cookie，owner/session 檢查、期限、撤銷與跨頁登出不變。
