````md
# API（前端串接必看）

本文件提供 Studydy 後端目前可用 API、請求/回應範例、錯誤碼與前端 `fetch` 範例。

---

## Base URL
本機（WSL）預設：
- `http://127.0.0.1:8000`

Swagger（OpenAPI）：
- `http://127.0.0.1:8000/docs`

---

## Demo：驗證碼寄送方式（目前為 Console Stub）
目前後端不會真的寄信。當你呼叫「寄送驗證碼」API 時，後端會把驗證碼印在後端 console/stdout，格式類似：
- `[EmailService] Sending code <code> to <email>`

前端測試流程（建議）：
1) 呼叫 `/auth/register/request-code` 或 `/auth/password-reset/request-code`
2) 到後端 console 取得 `code`
3) 呼叫 confirm API 完成註冊或重設密碼

---

## 共通規則與格式
- Request/Response 皆為 JSON
- 失敗時通常為：
  - `{"detail": "<error message>"}`

### 密碼規則
- 最小長度：8

### learning_preference 可用值
- `visual`
- `text`
- `ai_assisted`

---

## Endpoints

### GET /
用途：確認服務可用  
Response `200`：
```json
{ "message": "Studydy backend running" }
````

---

### GET /health

用途：健康檢查
Response `200`：

```json
{ "status": "ok" }
```

---

### GET /auth/learning-preferences

用途：取得可用的 learning_preference 清單
Response `200`（示例）：

```json
["visual", "text", "ai_assisted"]
```

---

## Auth

### POST /auth/register/request-code

用途：註冊前，寄送 email 驗證碼（demo：印在 console）

Request：

```json
{ "email": "user@example.com", "password": "supersecretpw" }
```

Response `200`：

```json
{ "detail": "Verification code sent" }
```

常見錯誤：

* `422`：欄位格式錯誤（例如 email 格式不合法）
* `400`：Email 已被註冊

---

### POST /auth/register/confirm

用途：輸入驗證碼完成註冊（建立帳號）

Request（learning_preference 可選）：

```json
{
  "email": "user@example.com",
  "password": "supersecretpw",
  "code": "123456",
  "learning_preference": "visual"
}
```

Response `201`（示例）：

```json
{
  "id": 1,
  "email": "user@example.com",
  "learning_preference": "visual",
  "created_at": "2025-12-14T07:30:00+00:00"
}
```

常見錯誤：

* `400`：驗證碼錯誤或過期

  ```json
  { "detail": "Invalid or expired verification code" }
  ```
* `400`：Email 已被註冊（若後端有做重複檢查）
* `422`：欄位格式錯誤 / 密碼太短

---

### POST /auth/login

用途：登入並取得 access token（JWT）

Request：

```json
{ "email": "user@example.com", "password": "supersecretpw" }
```

Response `200`：

```json
{ "access_token": "<JWT>", "token_type": "bearer" }
```

常見錯誤：

* `401`：登入失敗（固定訊息）

  ```json
  { "detail": "Incorrect email or password" }
  ```

---

## Password Reset

### POST /auth/password-reset/request-code

用途：忘記密碼，寄送重設驗證碼（demo：印在 console）
注意：為避免帳號枚舉（email 是否存在被推測），通常會固定回成功訊息。

Request：

```json
{ "email": "user@example.com" }
```

Response `200`（固定訊息）：

```json
{ "detail": "If the email exists, a verification code has been sent" }
```

常見錯誤：

* `422`：email 格式錯誤

---

### POST /auth/password-reset/confirm

用途：輸入驗證碼並更新密碼

Request：

```json
{
  "email": "user@example.com",
  "code": "123456",
  "new_password": "newsecretpw"
}
```

Response `200`：

```json
{ "detail": "Password has been reset" }
```

常見錯誤：

* `400`：驗證碼錯誤或過期

  ```json
  { "detail": "Invalid or expired verification code" }
  ```
* `422`：欄位格式錯誤 / 新密碼太短

---

## curl 範例

### 註冊（request code）

```bash
curl -X POST "http://127.0.0.1:8000/auth/register/request-code" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"supersecretpw"}'
```

### 註冊（confirm）

```bash
curl -X POST "http://127.0.0.1:8000/auth/register/confirm" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"supersecretpw","code":"123456","learning_preference":"visual"}'
```

### 登入

```bash
curl -X POST "http://127.0.0.1:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"supersecretpw"}'
```

### 忘記密碼（request code）

```bash
curl -X POST "http://127.0.0.1:8000/auth/password-reset/request-code" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com"}'
```

### 忘記密碼（confirm）

```bash
curl -X POST "http://127.0.0.1:8000/auth/password-reset/confirm" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","code":"123456","new_password":"newsecretpw"}'
```

---

## 前端 fetch 範例（已修正 JS typo，可直接複製）

```js
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}

export function requestRegisterCode(email, password) {
  return apiFetch("/auth/register/request-code", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function confirmRegister(email, password, code, learning_preference) {
  return apiFetch("/auth/register/confirm", {
    method: "POST",
    body: JSON.stringify({ email, password, code, learning_preference }),
  });
}

export function login(email, password) {
  return apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function requestPasswordResetCode(email) {
  return apiFetch("/auth/password-reset/request-code", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function confirmPasswordReset(email, code, new_password) {
  return apiFetch("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ email, code, new_password }),
  });
}

export function getLearningPreferences() {
  return apiFetch("/auth/learning-preferences", { method: "GET" });
}
```
