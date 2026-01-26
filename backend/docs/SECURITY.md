```md
# Security（安全約束）

本文件說明 Studydy 後端在「身分驗證 / 註冊 / 忘記密碼 / session cookie」相關的安全設計原則與最低要求。內容以專題階段可落地的做法為主，並標記後續可強化項目。

---

## 1) 範圍與目標

### 範圍
- 登入（Login）
- 註冊（Register）
- 忘記密碼 / 重設密碼（Password Reset）
- Session cookie
- Secrets（session secret、DB credentials 等）

### 目標
- 避免帳號枚舉（Account Enumeration）
- 降低暴力破解、憑證填充（Brute force / Credential stuffing）風險
- 避免弱密鑰與 session cookie 常見誤用（弱 secret、傳輸不安全）
- 避免 secrets 外洩（硬編碼、提交到 repo、出現在 log）

---

## 2) 避免帳號枚舉（Account Enumeration）

攻擊者常透過「回應訊息差異」或「回應時間差異」判斷 email/帳號是否存在，進一步進行暴力破解或釣魚。

### 2.1 回應訊息必須一致（Generic Error Message）
以下情境**不得**透露「email 是否存在」：
- 登入失敗（email 不存在 vs 密碼錯）
- 忘記密碼 request-code（存在 vs 不存在）
- 註冊流程（已存在 vs 不存在）

建議（專題階段最低要求）：
- **Login**：固定回 `401` + `{"detail": "Incorrect email or password"}`
- **Password reset request-code**：固定回 `200` + 通用訊息（例如 `If the email exists, ...`）

### 2.2 回應時間盡量一致（Uniform Response Time）
即使訊息一致，若「不存在的 email」提早返回而「存在的 email」多做 hash/DB 查詢才返回，仍可能被時間分析枚舉。
- 避免「quick exit」模式
- 讓不存在與存在帳號走相同或近似流程（或以非同步/固定延遲方式降低可觀測差異）

---

## 3) 忘記密碼 / 驗證碼流程安全（Verification Code / Reset Flow）

目前專題採用「寄送驗證碼 → confirm」的兩段式流程。

### 3.1 request-code 階段（發放驗證碼）
最低要求：
- 永遠回一致訊息（存在/不存在都一樣）
- 驗證碼必須：
  - 使用密碼學安全的隨機來源生成
  - 夠長（避免被暴力猜中）
  - 有有效期限（expire）
  - 單次使用（single use）
  - **安全儲存**：資料庫內只存 hash，不存明碼

防濫用（強烈建議，後續可加）：
- 加入 rate limiting（IP / email 維度）
- 避免大量 request-code 導致使用者收件匣被轟炸（email flooding）

### 3.2 confirm 階段（驗證碼確認並更新密碼）
最低要求：
- 只有在驗證碼有效且未使用、未過期時才允許更新密碼
- 驗證碼驗證失敗必須回通用錯誤（例如 `Invalid or expired verification code`），避免細分「過期 / 錯誤 / 已用」

附註（最佳實務）：
- 重設成功後通知使用者（不要在信件內包含密碼）
- 若未來導入更完整的 session 管理，重設後可選擇失效所有舊 session

---

## 4) Session Cookie 安全要求

### 4.1 Session secret 強度與管理
- secret 必須足夠長且不可猜（避免被暴力破解）
- 不得重用在不同環境（dev/staging/prod 應分離）
- 必須可輪替（rotation）

### 4.2 Cookie 使用建議
- 只透過 HTTPS 傳輸（正式環境務必）
- SameSite 與 HttpOnly 需正確設定（目前使用 server-side session cookie）
- 禁止把 session、驗證碼、密碼寫入 log

---

## 5) Secrets 管理（session secret / DB credentials / API keys）

最低要求：
- **禁止**把 secrets 寫死在程式碼或提交到 Git（包含 `.env`、連線字串、session secret、私鑰/憑證）
- secrets 來源應為環境變數（或更完整的 secrets manager）
- `.env.example` 只能放「欄位名稱與範例」，不得放真值

---

## 6) 監控與防護（建議但非專題最低門檻）

- 登入失敗與 request-code 行為應記錄（但不得記錄敏感內容）
- 異常行為偵測：
  - 同 IP 多次失敗
  - 同帳號多次失敗
  - request-code 過於頻繁
- 針對登入失敗可採用：
  - 漸進式延遲（progressive delay）
  - rate limit
  - CAPTCHA（必要時）

---

## 7) 專題階段已知限制（Known Limitations）

- EmailService 目前為 demo stub：驗證碼會輸出到 console（僅供展示，不可視為正式安全設計）
- 未實作：
  - rate limiting / CAPTCHA
  - session rotation / global logout
  - MFA
  - 完整的集中式 secrets manager（Vault / cloud KMS）

---

## 8) 參考資源（References）
- OWASP Authentication Cheat Sheet（Generic error message / time-based enumeration）
  - https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Forgot Password Cheat Sheet（一致訊息、時間一致、token/code 安全要求、rate limiting）
  - https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- OWASP Top 10:2025 A07 Authentication Failures（避免 enumeration、延遲/限制、監控）
  - https://owasp.org/Top10/2025/A07_2025-Authentication_Failures/
- OWASP WSTG：Testing for Account Enumeration（remediation：一致訊息）
  - https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/03-Identity_Management_Testing/04-Testing_for_Account_Enumeration_and_Guessable_User_Account
- OWASP WSTG：Session Management Testing（session cookie 相關測試建議）
  - https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/
- OWASP Secrets Management Cheat Sheet（避免硬編碼、集中管理與輪替）
  - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
```
