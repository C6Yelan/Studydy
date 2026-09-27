import { useRef, useState } from "react";
import { ApiClientError, errorMessage } from "../../api/client";
import { Icon } from "../../ui/Icon";
import "./styles.css";

type Mode = "login" | "register";

function AccountFrame({ mode, children }: { mode: Mode; children: React.ReactNode }) {
  const isRegister = mode === "register";
  return (
    <main className="auth-page">
      <section
        className={`auth-card is-${mode}`}
        aria-label={isRegister ? "註冊 Studydy" : "登入 Studydy"}
      >
        <aside className="auth-illustration" aria-label="Studydy 學習夥伴">
          <div className="auth-brand">
            <img src="/assets/studydy/brand-idle.png" alt="" />
            <span>Studydy</span>
          </div>
          <h2>{isRegister ? "開始你的學習之旅" : "歡迎回來！"}</h2>
          <p>{isRegister ? "建立帳戶以使用 Studydy 的所有功能" : "登入以繼續您的學習旅程"}</p>
          <div className="auth-study-tile tile-chart" aria-hidden="true">
            <Icon name="chart" size={22} />
          </div>
          <div className="auth-study-tile tile-clock" aria-hidden="true">
            <Icon name="clock" size={22} />
          </div>
          <div className="auth-study-tile tile-book" aria-hidden="true">
            <Icon name="book" size={22} />
          </div>
          <img
            className="auth-mascot"
            alt={isRegister ? "Studydy 歡迎你開始學習" : "Studydy 正在閱讀書本"}
            src={
              isRegister
                ? "/assets/Studydy_角色素材/歡迎/welcome_present.png"
                : "/assets/Studydy_角色素材/鼓勵/小於60_/LT60.png"
            }
          />
          <svg className="auth-plant" viewBox="0 0 80 110" aria-hidden="true">
            <path
              d="M40 105V25M40 65L17 43M40 85L65 62"
              fill="none"
              stroke="#6995ef"
              strokeWidth="4"
            />
            <ellipse cx="15" cy="36" rx="15" ry="18" fill="#80a8fa" />
            <ellipse cx="64" cy="56" rx="14" ry="17" fill="#a6c2ff" />
            <ellipse cx="38" cy="22" rx="12" ry="20" fill="#c4d7ff" />
          </svg>
          <svg
            className="auth-waves"
            viewBox="0 0 322 108"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path d="M0 26L62 8L123 33L185 13L258 28L322 8V108H0Z" fill="#e2ebff" />
            <path d="M0 54L62 36L123 57L185 39L258 53L322 35V108H0Z" fill="#d5e3ff" />
            <path d="M0 78L62 62L123 79L185 63L258 77L322 58V108H0Z" fill="#c8daff" />
          </svg>
        </aside>
        <div className="auth-form-panel">{children}</div>
      </section>
    </main>
  );
}

type FieldName = "email" | "password" | "confirm-password";
type FieldErrors = Partial<Record<FieldName, string>>;
const fieldOrder: FieldName[] = ["email", "password", "confirm-password"];

function getField(form: HTMLFormElement, name: FieldName): HTMLInputElement {
  return form.elements.namedItem(name) as HTMLInputElement;
}

function validateFields(
  form: HTMLFormElement,
  isRegister: boolean,
  rejectedEmail: string | null,
): FieldErrors {
  const errors: FieldErrors = {};
  const email = getField(form, "email");
  const password = getField(form, "password");
  if (!email.value.trim()) errors.email = "請輸入 Email。";
  else if (
    email.validity.typeMismatch ||
    email.value.length > email.maxLength ||
    email.value.trim() === rejectedEmail
  )
    errors.email = "請輸入有效的 Email 格式。";
  if (!password.value) errors.password = "請輸入密碼。";
  else if (password.value.length < password.minLength) errors.password = "密碼至少 15 個字元。";
  else if (password.value.length > password.maxLength)
    errors.password = "密碼不可超過 128 個字元。";
  if (isRegister) {
    const confirm = getField(form, "confirm-password");
    if (!confirm.value) errors["confirm-password"] = "請再次輸入密碼。";
    else if (confirm.value !== password.value)
      errors["confirm-password"] = "兩次輸入的密碼不一致，請再確認。";
  }
  return errors;
}

function PasswordField({
  name,
  label,
  placeholder,
  isRegister,
  disabled,
  error,
}: {
  name: "password" | "confirm-password";
  label: string;
  placeholder: string;
  isRegister: boolean;
  disabled: boolean;
  error?: string;
}) {
  const [visible, setVisible] = useState(false);
  const isConfirmation = name === "confirm-password";
  const describedBy =
    [isRegister && !isConfirmation ? "password-hint" : null, error ? `${name}-error` : null]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <div className="auth-field">
      <label htmlFor={name}>{label}</label>
      <div className="auth-input-wrap">
        <Icon name="lock" size={18} />
        <input
          id={name}
          name={name}
          type={visible ? "text" : "password"}
          placeholder={placeholder}
          required
          minLength={15}
          maxLength={128}
          autoComplete={isRegister ? "new-password" : "current-password"}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
        />
        <button
          type="button"
          className="auth-eye"
          aria-label={`${visible ? "隱藏" : "顯示"}${isConfirmation ? "確認密碼" : "密碼"}`}
          aria-pressed={visible}
          disabled={disabled}
          onClick={() => setVisible((value) => !value)}
        >
          <Icon name={visible ? "eye-off" : "eye"} size={18} />
        </button>
      </div>
      {isRegister && !isConfirmation && <small id="password-hint">密碼至少 15 個字元</small>}
      {error && (
        <p className="auth-field-error" id={`${name}-error`}>
          {error}
        </p>
      )}
    </div>
  );
}

export function AccountPage({
  mode,
  authenticate,
  sessionNotice,
}: {
  sessionNotice?: React.ReactNode;
  mode: Mode;
  authenticate: (mode: Mode, email: string, password: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const rejectedEmail = useRef<string | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const isRegister = mode === "register";

  async function handleSubmit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const form = event.currentTarget;
    const nextErrors = validateFields(form, isRegister, rejectedEmail.current);
    setAttempted(true);
    setErrors(nextErrors);
    setSubmitError("");
    const firstInvalid = fieldOrder.find((name) => nextErrors[name]);
    if (firstInvalid) {
      getField(form, firstInvalid).focus();
      return;
    }
    const email = getField(form, "email").value.trim();
    const password = getField(form, "password").value;
    submitting.current = true;
    setBusy(true);
    try {
      await authenticate(mode, email, password);
    } catch (error) {
      if (error instanceof ApiClientError && error.reasonCode === "INVALID_EMAIL") {
        rejectedEmail.current = email;
        setErrors({ email: "請輸入有效的 Email 格式。" });
        requestAnimationFrame(() => getField(form, "email").focus());
      } else setSubmitError(errorMessage(error));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <AccountFrame mode={mode}>
      <h1>{isRegister ? "建立新帳戶" : "登入您的帳戶"}</h1>
      {sessionNotice}
      <form
        className="auth-form"
        noValidate
        onInput={(event) => {
          if (attempted)
            setErrors(validateFields(event.currentTarget, isRegister, rejectedEmail.current));
          setSubmitError("");
        }}
        onSubmit={handleSubmit}
      >
        <div className="auth-field">
          <label htmlFor="email">Email</label>
          <div className="auth-input-wrap">
            <Icon name="user" size={18} />
            <input
              type="email"
              id="email"
              name="email"
              autoComplete="username"
              placeholder="請輸入 Email"
              required
              maxLength={254}
              disabled={busy}
              aria-invalid={errors.email ? true : undefined}
              aria-describedby={errors.email ? "email-error" : undefined}
            />
          </div>
          {errors.email && (
            <p className="auth-field-error" id="email-error">
              {errors.email}
            </p>
          )}
        </div>
        <PasswordField
          name="password"
          label="密碼"
          placeholder={isRegister ? "請設定密碼" : "請輸入密碼"}
          isRegister={isRegister}
          disabled={busy}
          error={errors.password}
        />
        {isRegister && (
          <PasswordField
            name="confirm-password"
            label="確認密碼"
            placeholder="請再次輸入密碼"
            isRegister
            disabled={busy}
            error={errors["confirm-password"]}
          />
        )}
        {submitError && (
          <p className="auth-error" role="alert">
            {submitError}
          </p>
        )}
        <button className="primary-button auth-submit" disabled={busy} type="submit">
          {busy ? "處理中…" : isRegister ? "註冊" : "登入"}
        </button>
      </form>
      <p className="auth-switch">
        {isRegister ? "已經有帳戶？" : "還沒有帳戶？"}{" "}
        <a href={isRegister ? "/login" : "/register"}>{isRegister ? "立即登入" : "立即註冊"}</a>
      </p>
    </AccountFrame>
  );
}
