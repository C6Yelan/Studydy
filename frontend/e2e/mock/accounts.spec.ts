import { expect, test, type Page, type Route } from "@playwright/test";

const origin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";
const password = "Synthetic test password 42";
const learnerId = "11111111-1111-4111-8111-111111111111";
const identity = { schema: "learner-identity/v1", learner_id: learnerId };
type AuthMode = "login" | "register";
type Outage = "network" | "gateway" | "pending";
type BootstrapWindow = Window & { bootstrapStarted: boolean; releaseBootstrap: () => void };

function apiError(reason_code: string) {
  return {
    schema: "api-error/v1",
    request_id: learnerId,
    reason_code,
    retryable: false,
    message: "Request could not be completed.",
  };
}

async function fillCredentials(page: Page, mode: AuthMode, email = "learner_test@example.com") {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("密碼", { exact: true }).fill(password);
  if (mode === "register") await page.getByLabel("確認密碼", { exact: true }).fill(password);
}

function mockOutage(route: Route, outage: Outage) {
  if (outage === "network") return route.abort("connectionrefused");
  if (outage === "gateway")
    return route.fulfill({
      status: 502,
      contentType: "text/html",
      body: "<html>Bad gateway</html>",
    });
  // pending 不回應，讓 API client 自己逾時，驗證表單會恢復操作。
}

test.beforeEach(async ({ page }) => {
  let signedIn = false;
  // 共用 RegExp 路由；案例覆蓋／移除自己的 glob 時，不會移除預設回應。
  await page.route(/\/v[12]\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    switch (path) {
      case "/v1/session":
      case "/v1/session/refresh":
        expect(request.method()).toBe(path === "/v1/session" ? "GET" : "POST");
        return route.fulfill({
          status: signedIn ? 200 : 401,
          json: signedIn ? identity : apiError("SESSION_REQUIRED"),
        });
      case "/v1/accounts":
      case "/v1/session/login":
        expect(request.method()).toBe("POST");
        signedIn = true;
        return route.fulfill({ status: path === "/v1/accounts" ? 201 : 200, json: identity });
      case "/v1/materials":
        expect(request.method()).toBe("GET");
        return route.fulfill({ json: { schema: "material-library/v1", materials: [] } });
      default:
        throw new Error(`Unexpected account mock request: ${request.method()} ${path}`);
    }
  });
});

for (const mode of ["login", "register"] as const) {
  test(`${mode} validation, password visibility and mode switching stay accessible`, async ({
    page,
  }) => {
    await page.goto(`/${mode}`);
    const form = page.locator("form.auth-form");
    const email = page.getByLabel("Email", { exact: true });
    const secret = page.getByLabel("密碼", { exact: true });
    await expect(
      page.getByRole("heading", {
        name: mode === "login" ? "登入您的帳戶" : "建立新帳戶",
        exact: true,
      }),
    ).toBeVisible();
    await expect(secret).toHaveAttribute("minlength", "15");
    await expect(secret).toHaveAttribute("maxlength", "128");
    if (mode === "register") {
      await expect(page.locator("#password-hint")).toHaveText("密碼至少 15 個字元");
    }
    await expect(email).toHaveAttribute("type", "email");
    await expect(email).toHaveAttribute("autocomplete", "username");
    await expect(secret).toHaveAttribute(
      "autocomplete",
      mode === "login" ? "current-password" : "new-password",
    );
    await expect(page.locator('[aria-invalid="true"]')).toHaveCount(0);
    expect(await form.evaluate((element: HTMLFormElement) => element.noValidate)).toBe(true);
    await form.evaluate((element) => {
      (element as HTMLFormElement).dataset.invalidEvents = "0";
      element.addEventListener(
        "invalid",
        () => {
          (element as HTMLFormElement).dataset.invalidEvents = "1";
        },
        true,
      );
    });
    let calls = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/v1\/(accounts|session\/login)$/.test(request.url()))
        calls++;
    });
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
      await expect(page.getByLabel("確認密碼", { exact: true })).toHaveAttribute(
        "autocomplete",
        "new-password",
      );
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
    await page.getByRole("button", { name: "顯示密碼", exact: true }).click();
    await expect(secret).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "隱藏密碼", exact: true }).click();
    await expect(secret).toHaveAttribute("type", "password");
    if (mode === "register") {
      const confirm = page.getByLabel("確認密碼", { exact: true });
      await confirm.fill("Different synthetic password");
      await expect(page.locator("#confirm-password-error")).toHaveText(
        "兩次輸入的密碼不一致，請再確認。",
      );
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
    // 路由重新整理與模式切換各驗一次，不隨每個版面尺寸重跑。
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`/${mode}$`));
    await page
      .getByRole("link", { name: mode === "login" ? "立即註冊" : "立即登入", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(mode === "login" ? "/register$" : "/login$"));
    await expect(
      page.getByRole("heading", {
        name: mode === "login" ? "建立新帳戶" : "登入您的帳戶",
        exact: true,
      }),
    ).toBeVisible();
  });
}

test("busy submit is disabled and never duplicates the authentication request", async ({
  page,
}) => {
  let calls = 0;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/v1/session/login", async (route) => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ email: "busy@example.com", password });
    await pending;
    await route.fulfill({ status: 401, json: apiError("INVALID_CREDENTIALS") });
  });
  await page.goto("/login");
  await fillCredentials(page, "login", "busy@example.com");
  await page.locator("form.auth-form").evaluate((form) => {
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

// 登入與註冊各驗桌機／手機，覆蓋兩種 CSS 分支。
for (const size of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  for (const mode of ["login", "register"] as const) {
    test(`${mode} layout at ${size.width}x${size.height}`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto(`/${mode}`);
      await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
      await expect(page.locator(".app-header")).toHaveCount(0);
      const card = await page.locator(".auth-card").boundingBox();
      expect(card!.x).toBeGreaterThanOrEqual(0);
      expect(card!.x + card!.width).toBeLessThanOrEqual(size.width);
      expect(card!.y).toBeGreaterThanOrEqual(0);
      expect(card!.y + card!.height).toBeLessThanOrEqual(size.height);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(size.width);
      const submit = await page.locator("button.auth-submit").boundingBox();
      expect(submit!.height).toBeGreaterThanOrEqual(size.width > 600 ? 56 : 50);
      if (size.width > 600) {
        if (mode === "register") {
          expect(card!.height).toBeGreaterThanOrEqual(760);
          expect(card!.height).toBeLessThanOrEqual(820);
        }
        expect(card!.width).toBeGreaterThanOrEqual(800);
        expect(card!.width).toBeLessThanOrEqual(820);
        expect(Math.abs(card!.x + card!.width / 2 - size.width / 2)).toBeLessThan(1);
        await expect
          .poll(() =>
            page
              .locator(".auth-mascot")
              .evaluate(
                (element: HTMLImageElement) => element.complete && element.naturalWidth > 0,
              ),
          )
          .toBe(true);
      }
    });
  }
}

// bootstrap 使用同一個 App 流程：登入涵蓋三種失敗，註冊補驗額外確認欄位仍可操作。
for (const { mode, outage } of [
  { mode: "login", outage: "network" },
  { mode: "login", outage: "gateway" },
  { mode: "login", outage: "pending" },
  { mode: "register", outage: "pending" },
] as const) {
  test(`${mode} form works during ${outage} bootstrap and recovers without reload`, async ({
    page,
  }) => {
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    await page.route("**/v1/session/refresh", (route) => mockOutage(route, outage));
    await page.goto(`/${mode}`);
    const submit = page.getByRole("button", {
      name: mode === "login" ? "登入" : "註冊",
      exact: true,
    });
    await expect(submit).toBeVisible();
    await submit.click();
    await expect(page.locator("#email-error")).toBeVisible();
    await page.getByRole("button", { name: "顯示密碼", exact: true }).click();
    await expect(page.locator("#password")).toHaveAttribute("type", "text");
    await expect(
      page.getByRole("link", { name: mode === "login" ? "立即註冊" : "立即登入" }),
    ).toBeVisible();
    await page.clock.runFor(10_001);
    await expect(submit).toBeEnabled();
    await expect(page.getByText("暫時無法完成", { exact: true })).toHaveCount(0);
    await page.unroute("**/v1/session/refresh");
    await fillCredentials(page, mode);
    await submit.click();
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  });
}

for (const mode of ["login", "register"] as const) {
  test(`${mode} submit failures release busy and allow retry`, async ({ page }) => {
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    await page.goto(`/${mode}`);
    const endpoint = mode === "login" ? "**/v1/session/login" : "**/v1/accounts";
    await fillCredentials(page, mode);
    const submit = page.getByRole("button", {
      name: mode === "login" ? "登入" : "註冊",
      exact: true,
    });
    for (const outage of ["network", "gateway", "pending"] as const) {
      await page.route(endpoint, (route) => mockOutage(route, outage));
      const sent = page.waitForRequest(
        (request) =>
          request.method() === "POST" && /\/v1\/(accounts|session\/login)$/.test(request.url()),
      );
      await submit.click();
      await sent;
      if (outage === "pending") await page.clock.runFor(10_001);
      await expect(page.getByRole("alert")).toContainText(
        outage === "gateway" ? "服務暫時無法使用" : outage === "pending" ? "逾時" : "無法連線",
      );
      await expect(submit).toBeEnabled();
      await page.unroute(endpoint);
    }
    if (mode === "register") {
      await page.route(
        endpoint,
        (route) => route.fulfill({ status: 409, json: apiError("ACCOUNT_UNAVAILABLE") }),
        { times: 1 },
      );
      await submit.click();
      await expect(page.getByRole("alert")).toHaveText("這個 Email 已被使用，請使用其他 Email。");
      await expect(submit).toBeEnabled();
    }
    await submit.click();
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
  });
}

for (const result of ["success", "expired", "network"] as const) {
  test(`late bootstrap ${result} cannot overwrite successful login`, async ({ page }) => {
    const response =
      result === "success"
        ? { ...identity, learner_id: "33333333-3333-4333-8333-333333333333" }
        : apiError("SESSION_REQUIRED");
    // 故意忽略 AbortSignal，模擬舊請求在登入完成後才回應。
    await page.addInitScript(
      ({ result, response }) => {
        const original = window.fetch.bind(window);
        let first = true;
        Object.assign(window, { bootstrapStarted: false });
        window.fetch = (input, init) => {
          if (String(input) === "/v1/session/refresh" && first) {
            first = false;
            Object.assign(window, { bootstrapStarted: true });
            return new Promise((resolve, reject) =>
              Object.assign(window, {
                releaseBootstrap: () => {
                  if (result === "network") reject(new TypeError("offline"));
                  else
                    resolve(Response.json(response, { status: result === "success" ? 200 : 401 }));
                },
              }),
            );
          }
          return original(input, init);
        };
      },
      { result, response },
    );
    await page.goto("/login");
    await expect
      .poll(() => page.evaluate(() => (window as BootstrapWindow).bootstrapStarted))
      .toBe(true);
    await fillCredentials(page, "login");
    await page.getByRole("button", { name: "登入", exact: true }).click();
    await expect(page.getByRole("button", { name: "登出", exact: true })).toBeVisible();
    await page.evaluate(async () => {
      (window as BootstrapWindow).releaseBootstrap();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await expect(page.getByRole("navigation", { name: "主要導覽", exact: true })).toBeVisible();
    await expect(page).toHaveURL(`${origin}/`);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => JSON.parse(localStorage.getItem("studydy.session-hint") ?? "null")?.learner_id,
      ),
    ).toBe(learnerId);
  });
}

test("private routes hide authentication and content while the session is unknown", async ({
  page,
}) => {
  await page.route("**/v1/session/refresh", (route) => route.abort("connectionrefused"));
  await page.goto("/materials");
  await expect(page.getByText("暫時無法完成", { exact: true })).toBeVisible();
  await expect(page.locator(".app-header")).toBeVisible();
  await expect(page.locator("form.auth-form")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toHaveCount(0);
  await page.unroute("**/v1/session/refresh");
  await page.getByRole("button", { name: "再試一次", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator("form.auth-form")).toBeVisible();
});

// email／password 的焦點樣式在兩種模式及 viewport 共用同一組 CSS。
test("shared authentication inputs keep focus and invalid styles distinct", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.goto("/login");
  const email = page.locator("#email");
  const secret = page.locator("#password");
  const style = (input: typeof email) =>
    input.evaluate((element) => {
      const css = getComputedStyle(element);
      const color = (value: string) => {
        const canvas = document.createElement("canvas").getContext("2d")!;
        canvas.fillStyle = value;
        return canvas.fillStyle;
      };
      const token = (name: string) => css.getPropertyValue(name).trim();
      return {
        border: color(css.borderColor),
        outline: css.outlineStyle,
        width: css.borderWidth,
        shadow: css.boxShadow,
        neutral: color(token("--border")),
        blue: color(token("--studydy-blue")),
        error: color(token("--error")),
      };
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
  expect(await style(secret)).toMatchObject({ border: focused.blue, outline: "none" });
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(email).toBeFocused();
  const invalid = await style(email);
  expect(invalid).toMatchObject({ border: invalid.error, outline: "none", shadow: "none" });
  expect(parseFloat(invalid.width)).toBeGreaterThan(parseFloat(normal.width));
  await email.press("Tab");
  await expect(secret).toBeFocused();
  expect(await style(email)).toMatchObject({
    border: invalid.error,
    outline: "none",
    width: normal.width,
  });
  expect(await style(secret)).toMatchObject({
    border: invalid.error,
    outline: "none",
    width: invalid.width,
  });

  await email.fill("focus@example.com");
  expect(await style(email)).toMatchObject({ border: focused.blue, outline: "none" });
  await secret.fill(password);
  expect((await style(secret)).border).toBe(focused.blue);
});
