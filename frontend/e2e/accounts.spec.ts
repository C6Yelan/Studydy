import { expect, test, type Page } from "@playwright/test";

const password = "Synthetic test password 42";
const learnerId = process.env.STUDYDY_E2E_ACCOUNT_LEARNER!;
const materialId = process.env.STUDYDY_E2E_ACCOUNT_MATERIAL!;
const runId = process.env.STUDYDY_E2E_ACCOUNT_RUN!;
const revision = process.env.STUDYDY_E2E_ACCOUNT_REVISION!;
const artifactId = process.env.STUDYDY_E2E_ACCOUNT_ARTIFACT!;
const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(revision)}`;
const origin = "http://127.0.0.1:4173";

test.skip(!learnerId, "Requires the local account API/DB fixture");

async function login(page: Page, email: string, suppliedPassword = password) {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("密碼", { exact: true }).fill(suppliedPassword);
  await page.getByRole("button", { name: "登入", exact: true }).click();
}

test("real accounts survive new browser profiles; logout and back never reveal another owner", async ({ browser }) => {
  const a = await browser.newContext();
  const page = await a.newPage();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await login(page, "learner_test@example.com", "Wrong synthetic password");
  await expect(page.getByRole("alert")).toHaveText("Email 或密碼不正確。");
  await login(page, "learner_test@example.com");
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  expect((await (await a.request.get(`${origin}/v1/session`)).json()).learner_id).toBe(learnerId);
  await expect(page.getByRole("heading", { name: "歡迎回來！", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
  const header = await page.locator(".app-header").boundingBox();
  const logout = await page.getByRole("button", { name: "登出", exact: true }).boundingBox();
  expect(logout!.width).toBeLessThan(140);
  expect(logout!.y + logout!.height).toBeLessThanOrEqual(header!.y + header!.height);

  await page.goto(mapPath);
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  const otherTab = await a.newPage();
  await otherTab.goto(mapPath);
  await expect(otherTab.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  // 只為登出失敗注入一次 transport response，重試仍使用真 API 撤銷。
  await page.route("**/v1/session", route => route.fulfill({ status: 503, json: {
    schema: "api-error/v1", request_id: learnerId, reason_code: "STORAGE_UNAVAILABLE",
    retryable: true, message: "Request could not be completed.",
  } }), { times: 1 });
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByText(/登出尚未完成/)).toBeVisible();
  await expect(page.getByText("Stack", { exact: true })).toHaveCount(0);
  await expect(otherTab.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  const loggedOut = page.waitForResponse(response => response.url().endsWith("/v1/session") && response.request().method() === "DELETE" && response.status() === 204);
  await page.getByRole("button", { name: "再試一次", exact: true }).click();
  await loggedOut;
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await otherTab.close();
  expect((await a.request.get(`${origin}/v1/session`)).status()).toBe(401);
  await page.getByRole("link", { name: "立即註冊" }).click();
  await page.getByLabel("Email", { exact: true }).fill("browser_account_b@example.com");
  await page.getByLabel("密碼", { exact: true }).fill(password);
  await page.getByLabel("確認密碼", { exact: true }).fill(password);
  await page.getByRole("button", { name: "註冊", exact: true }).click();
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  const bId = (await (await a.request.get(`${origin}/v1/session`)).json()).learner_id;
  expect(bId).not.toBe(learnerId);
  await page.goBack();
  await expect(page.getByText("Stack", { exact: true })).toHaveCount(0);
  await page.goto(mapPath);
  await expect(page.getByText("Stack", { exact: true })).toHaveCount(0);
  expect((await a.request.get(`${origin}/v1/artifacts/${artifactId}`)).status()).toBe(404);
  expect((await a.request.get(`${origin}/v1/material-processing-runs/${runId}`)).status()).toBe(404);
  await a.close();

  // 獨立 profile 重新輸入帳密，沒有複製 cookie 或 localStorage。
  const fresh = await browser.newContext();
  const freshPage = await fresh.newPage();
  await freshPage.goto("/");
  await login(freshPage, "learner_test@example.com");
  await expect(freshPage.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  expect((await (await fresh.request.get(`${origin}/v1/session`)).json()).learner_id).toBe(learnerId);
  await freshPage.goto(mapPath);
  await expect(freshPage.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  expect((await fresh.request.get(`${origin}/v1/artifacts/${artifactId}`)).status()).toBe(200);
  await fresh.request.delete(`${origin}/v1/session`, { headers: { Origin: origin } });
  await freshPage.getByRole("button", { name: "開始學習進度", exact: true }).click();
  await expect(freshPage.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await expect(freshPage.getByText("Stack", { exact: true })).toHaveCount(0);
  expect((await fresh.request.get(`${origin}/v1/session`)).status()).toBe(401);
  await fresh.close();
});

test("auth views retain the design geometry and validate confirmation before registration", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.goto('/register');
  await expect(page.getByRole('heading', { name: '建立新帳戶', exact: true })).toBeVisible();
  await expect(page.locator('.app-header')).toHaveCount(0);
  const card = await page.locator('.auth-card').boundingBox();
  expect(card!.width).toBeGreaterThanOrEqual(800);
  expect(card!.width).toBeLessThanOrEqual(820);
  expect(card!.height).toBeGreaterThanOrEqual(760);
  expect(card!.height).toBeLessThanOrEqual(820);
  expect(Math.abs(card!.x + card!.width / 2 - 768)).toBeLessThan(1);
  await expect.poll(() => page.locator('.auth-mascot').evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
  let registrations = 0;
  page.on('request', request => { if (request.url().endsWith('/v1/accounts')) registrations++; });
  await page.getByLabel('Email', { exact: true }).fill('confirmation_check@example.com');
  await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByLabel('確認密碼', { exact: true }).fill('Different synthetic password');
  await page.getByRole('button', { name: '顯示密碼', exact: true }).click();
  await expect(page.locator('#password')).toHaveAttribute('type', 'text');
  await page.getByRole('button', { name: '註冊', exact: true }).click();
  await expect(page.locator('#confirm-password-error')).toContainText('兩次輸入的密碼不一致');
  expect(registrations).toBe(0);
  await page.reload();
  await expect(page).toHaveURL(/\/register$/);
  await expect(page.getByRole('heading', { name: '建立新帳戶', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '立即登入', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('heading', { name: '登入您的帳戶', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});


for (const mode of ["login", "register"] as const) {
  test(`${mode} uses accessible inline validation without native bubbles`, async ({ page }) => {
    await page.goto(`/${mode}`);
    const form = page.locator("form.auth-form");
    const email = page.getByLabel("Email", { exact: true });
    const secret = page.getByLabel("密碼", { exact: true });
    await expect(page.getByRole("heading", { name: mode === "login" ? "登入您的帳戶" : "建立新帳戶", exact: true })).toBeVisible();
    await expect(email).toHaveAttribute("placeholder", "請輸入 Email");
    await expect(secret).toHaveAttribute("placeholder", mode === "login" ? "請輸入密碼" : "請設定密碼");
    await expect(secret).toHaveAttribute("minlength", "15");
    await expect(secret).toHaveAttribute("maxlength", "128");
    await expect(page.locator(".auth-switch")).toHaveText(mode === "login" ? "還沒有帳戶？ 立即註冊" : "已經有帳戶？ 立即登入");
    await expect(page.locator(".auth-card")).not.toContainText(/帳號|電子郵件|信箱|請填寫|可包含空格/);
    if (mode === "register") {
      await expect(page.locator("#password-hint")).toHaveText("密碼至少 15 個字元");
      await expect(page.locator("#confirm-password")).toHaveAttribute("placeholder", "請再次輸入密碼");
    }
    await expect(email).toHaveAttribute("type", "email");
    await expect(email).toHaveAttribute("id", "email");
    await expect(email).toHaveAttribute("name", "email");
    await expect(email).toHaveAttribute("autocomplete", "username");
    await expect(secret).toHaveAttribute("autocomplete", mode === "login" ? "current-password" : "new-password");
    await expect(page.locator('[aria-invalid="true"]')).toHaveCount(0);
    await expect(page.locator(".auth-divider")).toHaveCount(0);
    await expect(page.getByText("或", { exact: true })).toHaveCount(0);
    expect(await form.evaluate((element: HTMLFormElement) => element.noValidate)).toBe(true);
    await form.evaluate(element => {
      (element as HTMLFormElement).dataset.invalidEvents = "0";
      element.addEventListener("invalid", () => { (element as HTMLFormElement).dataset.invalidEvents = "1"; }, true);
    });
    let calls = 0;
    page.on("request", request => { if (request.method() === "POST" && /\/v1\/(accounts|session\/login)$/.test(request.url())) calls++; });
    await email.focus();
    await email.press("Enter");
    await expect(page.locator("#email-error")).toHaveText("請輸入 Email。");
    await expect(page.locator("#password-error")).toHaveText("請輸入密碼。");
    await expect(email).toBeFocused();
    await expect(email).toHaveAttribute("aria-invalid", "true");
    await expect(email).toHaveAttribute("aria-describedby", "email-error");
    await expect(secret).toHaveAttribute("aria-describedby", /password-error/);
    if (mode === "register") {
      await expect(page.locator("#confirm-password-error")).toHaveText("請再次輸入密碼。");
      await expect(page.getByLabel("確認密碼", { exact: true })).toHaveAttribute("autocomplete", "new-password");
    }
    await email.fill("not-an-email");
    await expect(page.locator("#email-error")).toHaveText("請輸入有效的 Email 格式。");
    await email.fill("validation@example.com");
    await expect(page.locator("#email-error")).toHaveCount(0);
    await expect(email).not.toHaveAttribute("aria-invalid", "true");
    await secret.fill("short");
    await expect(page.locator("#password-error")).toHaveText("密碼至少 15 個字元。");
    await secret.fill(password);
    await expect(page.locator("#password-error")).toHaveCount(0);
    if (mode === "register") {
      const confirm = page.getByLabel("確認密碼", { exact: true });
      await confirm.fill("Different synthetic password");
      await expect(page.locator("#confirm-password-error")).toHaveText("兩次輸入的密碼不一致，請再確認。");
      await page.getByRole("button", { name: "註冊", exact: true }).click();
      await expect(confirm).toBeFocused();
      await confirm.fill(password);
      await expect(page.locator("#confirm-password-error")).toHaveCount(0);
      await page.getByRole("button", { name: "顯示確認密碼", exact: true }).click();
      await expect(confirm).toHaveAttribute("type", "text");
      await page.getByRole("button", { name: "隱藏確認密碼", exact: true }).click();
      await expect(confirm).toHaveAttribute("type", "password");
    }
    await expect(form).toHaveAttribute("data-invalid-events", "0");
    expect(calls).toBe(0);
  });
}


test("busy submit is disabled and never duplicates the authentication request", async ({ page }) => {
  let calls = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/session/login", async route => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ email: "busy@example.com", password });
    await pending;
    await route.fulfill({ status: 401, json: { schema: "api-error/v1", request_id: learnerId, reason_code: "INVALID_CREDENTIALS", retryable: false, message: "Request could not be completed." } });
  });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("busy@example.com");
  await page.getByLabel("密碼", { exact: true }).fill(password);
  await page.locator("form.auth-form").evaluate(form => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await expect(page.getByRole("button", { name: "處理中…", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Email", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "顯示密碼", exact: true })).toBeDisabled();
  await expect.poll(() => calls).toBe(1);
  release();
  await expect(page.getByRole("alert")).toHaveText("Email 或密碼不正確。");
  await expect(page.getByRole("button", { name: "登入", exact: true })).toBeEnabled();
  await expect(page.locator('[aria-invalid="true"]')).toHaveCount(0);
  expect(calls).toBe(1);
});


for (const failure of ["duplicate", "storage", "network"] as const) {
  test(`registration keeps the ${failure} error boundary`, async ({ page }) => {
    await page.route("**/v1/accounts", async route => {
      if (failure === "network") { await route.abort("failed"); return; }
      await route.fulfill({ status: failure === "duplicate" ? 409 : 503, json: { schema: "api-error/v1", request_id: learnerId, reason_code: failure === "duplicate" ? "ACCOUNT_UNAVAILABLE" : "STORAGE_UNAVAILABLE", retryable: failure === "storage", message: "Request could not be completed." } });
    });
    await page.goto("/register");
    await page.getByLabel("Email", { exact: true }).fill("registration@example.com");
    await page.getByLabel("密碼", { exact: true }).fill(password);
    await page.getByLabel("確認密碼", { exact: true }).fill(password);
    await page.getByRole("button", { name: "註冊", exact: true }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    if (failure === "duplicate") await expect(alert).toHaveText("這個 Email 已被使用，請使用其他 Email。");
    if (failure === "storage") await expect(alert).toHaveText("資料服務暫時無法使用，請稍後再試。");
    await expect(alert).not.toHaveText("Email 或密碼不正確。");
    await expect(page.getByRole("button", { name: "註冊", exact: true })).toBeEnabled();
  });
}


for (const size of [{ width: 1536, height: 1024 }, { width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
  for (const mode of ["login", "register"] as const) {
    test(`${mode} layout at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
      await page.setViewportSize(size);
      await page.goto(`/${mode}`);
      await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
      const card = await page.locator(".auth-card").boundingBox();
      expect(card!.x).toBeGreaterThanOrEqual(0);
      expect(card!.x + card!.width).toBeLessThanOrEqual(size.width);
      expect(card!.y).toBeGreaterThanOrEqual(0);
      expect(card!.y + card!.height).toBeLessThanOrEqual(size.height);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(size.width);
      const submit = await page.locator("button.auth-submit").boundingBox();
      expect(submit!.height).toBeGreaterThanOrEqual(size.width > 600 ? 56 : 50);
      if (size.width > 600) {
        expect(card!.width).toBeGreaterThanOrEqual(800);
        expect(card!.width).toBeLessThanOrEqual(820);
        expect(Math.abs(card!.x + card!.width / 2 - size.width / 2)).toBeLessThan(1);
        await expect.poll(() => page.locator(".auth-mascot").evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
      }
      await page.screenshot({ path: testInfo.outputPath(`auth-${mode}-${size.width}x${size.height}.png`), fullPage: true });
    });
  }
}


test("server Email syntax rejection remains an inline field error", async ({ page }) => {
  await page.goto("/register");
  const email = page.getByLabel("Email", { exact: true });
  await email.fill("learner@localhost");
  expect(await email.evaluate((input: HTMLInputElement) => input.validity.valid)).toBe(true);
  await page.getByLabel("密碼", { exact: true }).fill(password);
  await page.getByLabel("確認密碼", { exact: true }).fill(password);
  await page.getByRole("button", { name: "註冊", exact: true }).click();
  await expect(page.locator("#email-error")).toHaveText("請輸入有效的 Email 格式。");
  await expect(email).toHaveAttribute("aria-invalid", "true");
  await expect(email).toBeFocused();
  await page.getByLabel("密碼", { exact: true }).fill("Another synthetic password");
  await expect(page.locator("#email-error")).toBeVisible();
  await email.fill("valid@example.com");
  await expect(page.locator("#email-error")).toHaveCount(0);
});

for (const mode of ["login", "register"] as const) {
  for (const outage of ["network", "gateway", "pending"] as const) {
    test(`${mode} form works during ${outage} bootstrap and recovers without reload`, async ({ page }) => {
      await page.clock.install();
      await page.clock.pauseAt(new Date());
      await page.route("**/v1/session/refresh", route => outage === "network" ? route.abort("connectionrefused")
        : outage === "gateway" ? route.fulfill({ status: 502, contentType: "text/html", body: "<html>Bad gateway</html>" }) : undefined);
      await page.goto(`/${mode}`);
      const submit = page.getByRole("button", { name: mode === "login" ? "登入" : "註冊", exact: true });
      await expect(submit).toBeVisible();
      await submit.click();
      await expect(page.locator("#email-error")).toBeVisible();
      await page.getByRole("button", { name: "顯示密碼", exact: true }).click();
      await expect(page.locator("#password")).toHaveAttribute("type", "text");
      await expect(page.getByRole("link", { name: mode === "login" ? "立即註冊" : "立即登入" })).toBeVisible();
      await page.clock.runFor(10_001);
      await expect(submit).toBeEnabled();
      await expect(page.getByText("暫時無法完成", { exact: true })).toHaveCount(0);
      await page.unroute("**/v1/session/refresh");
      await page.locator("#email").fill(mode === "login" ? "learner_test@example.com" : `recovery_${outage}@example.com`);
      await page.locator("#password").fill(password);
      if (mode === "register") await page.locator("#confirm-password").fill(password);
      await submit.click();
      await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
    });
  }

  test(`existing session redirects from ${mode}`, async ({ page }) => {
    await page.request.post(`${origin}/v1/session/login`, { headers: { Origin: origin }, data: { email: "learner_test@example.com", password } });
    await page.goto(`/${mode}`);
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
    await expect(page).toHaveURL(`${origin}/`);
  });

  test(`${mode} submit gateway and timeout release busy and allow retry`, async ({ page }) => {
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    await page.goto(`/${mode}`);
    const endpoint = mode === "login" ? "**/v1/session/login" : "**/v1/accounts";
    await page.locator("#email").fill(mode === "login" ? "learner_test@example.com" : "submit_recovery@example.com");
    await page.locator("#password").fill(password);
    if (mode === "register") await page.locator("#confirm-password").fill(password);
    const submit = page.getByRole("button", { name: mode === "login" ? "登入" : "註冊", exact: true });
    for (const outage of ["network", "gateway", "pending"]) {
      await page.route(endpoint, route => outage === "network" ? route.abort("connectionrefused")
        : outage === "gateway" ? route.fulfill({ status: 502, body: "<html>Bad gateway</html>" }) : undefined);
      const sent = page.waitForRequest(request => request.method() === "POST" && /\/v1\/(accounts|session\/login)$/.test(request.url()));
      await submit.click();
      await sent;
      if (outage === "pending") await page.clock.runFor(10_001);
      await expect(page.getByRole("alert")).toContainText(outage === "gateway" ? "服務暫時無法使用" : outage === "pending" ? "逾時" : "無法連線");
      await expect(submit).toBeEnabled();
      await page.unroute(endpoint);
    }
    await submit.click();
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  });
}

for (const result of ["success", "expired", "network"] as const) {
  test(`late bootstrap ${result} cannot overwrite successful login`, async ({ page }) => {
    await page.addInitScript(({ result }) => {
      const original = window.fetch.bind(window);
      let first = true;
      Object.assign(window, { bootstrapStarted: false });
      window.fetch = (input, init) => {
        if (String(input) === "/v1/session/refresh" && first) {
          first = false;
          Object.assign(window, { bootstrapStarted: true });
          return new Promise((resolve, reject) => Object.assign(window, { releaseBootstrap: () => {
            if (result === "network") reject(new TypeError("offline"));
            else if (result === "success") resolve(new Response(null, { status: 204 }));
            else resolve(Response.json({ schema: "api-error/v1", request_id: "11111111-1111-4111-8111-111111111111", reason_code: "SESSION_REQUIRED", retryable: false, message: "Request could not be completed." }, { status: 401 }));
          } }));
        }
        return original(input, init);
      };
    }, { result });
    await page.goto("/login");
    await expect.poll(() => page.evaluate(() => (window as any).bootstrapStarted)).toBe(true);
    await login(page, "learner_test@example.com");
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
    await page.evaluate(async () => { (window as any).releaseBootstrap(); await new Promise(resolve => setTimeout(resolve, 0)); });
    await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
    await expect(page).toHaveURL(`${origin}/`);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("private routes reject unknown/offline sessions and redirect expired sessions", async ({ page }) => {
  await page.route("**/v1/session/refresh", route => route.abort("connectionrefused"));
  await page.goto("/materials");
  await expect(page.getByText("暫時無法完成", { exact: true })).toBeVisible();
  await expect(page.locator(".app-header")).toHaveCount(0);
  await expect(page.locator("form.auth-form")).toHaveCount(0);
  await page.unroute("**/v1/session/refresh");
  await page.getByRole("button", { name: "再試一次", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator("form.auth-form")).toBeVisible();
  await login(page, "learner_test@example.com");
  await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  await page.route("**/v1/session/refresh", route => route.abort("connectionrefused"));
  const refresh = page.waitForRequest("**/v1/session/refresh");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await refresh;
  await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
});

for (const mode of ["login", "register"] as const) {
  for (const viewport of [{ width: 1536, height: 1024 }, { width: 390, height: 844 }]) {
    test(`${mode} focus respects validation state at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto(`/${mode}`);
      const email = page.locator("#email");
      const secret = page.locator("#password");
      const style = (input: typeof email) => input.evaluate(element => {
        const css = getComputedStyle(element);
        const color = (value: string) => {
          const canvas = document.createElement("canvas").getContext("2d")!;
          canvas.fillStyle = value;
          return canvas.fillStyle;
        };
        const token = (name: string) => css.getPropertyValue(name).trim();
        return { border: color(css.borderColor), outline: css.outlineStyle, width: css.borderWidth, shadow: css.boxShadow,
          neutral: color(token("--border")), blue: color(token("--studydy-blue")), error: color(token("--error")) };
      });
      const normal = await style(email);
      expect(normal.border).toBe(normal.neutral);
      await email.click();
      const focused = await style(email);
      expect(focused.border).toBe(focused.blue);
      expect(focused.outline).toBe("none");
      expect(focused.shadow).toBe("none");
      expect(parseFloat(focused.width)).toBeGreaterThan(parseFloat(normal.width));
      await email.press("Tab");
      await expect(secret).toBeFocused();
      expect((await style(secret)).border).toBe(focused.blue);
      expect((await style(secret)).outline).toBe("none");
      await page.getByRole("button", { name: mode === "login" ? "登入" : "註冊", exact: true }).click();
      await expect(email).toBeFocused();
      const invalid = await style(email);
      expect(invalid.border).toBe(invalid.error);
      expect(invalid.outline).toBe("none");
      expect(invalid.shadow).toBe("none");
      expect(parseFloat(invalid.width)).toBeGreaterThan(parseFloat(normal.width));
      await expect(email).toHaveAttribute("aria-describedby", "email-error");
      await expect(page.locator("#email-error")).toBeVisible();
      await page.screenshot({ path: `/tmp/studydy-auth-focus/${mode}-${viewport.width}-email.png`, fullPage: true });
      await email.press("Tab");
      await expect(secret).toBeFocused();
      expect((await style(email)).border).toBe(invalid.error);
      expect((await style(email)).outline).toBe("none");
      expect((await style(secret)).border).toBe(invalid.error);
      expect((await style(secret)).outline).toBe("none");
      expect((await style(secret)).width).toBe(invalid.width);
      expect((await style(email)).width).toBe(normal.width);
      await page.screenshot({ path: `/tmp/studydy-auth-focus/${mode}-${viewport.width}-password.png`, fullPage: true });
      await email.fill("focus@example.com");
      await expect(email).not.toHaveAttribute("aria-invalid", "true");
      await expect(page.locator("#email-error")).toHaveCount(0);
      expect((await style(email)).outline).toBe("none");
      await secret.fill(password);
      await expect(page.locator("#password-error")).toHaveCount(0);
      expect((await style(secret)).border).toBe(focused.blue);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    });
  }
}
