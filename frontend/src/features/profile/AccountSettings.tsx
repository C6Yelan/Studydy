import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AccountSessions } from "../../api/contracts";
import { useProfile } from "../../app/HeaderTools";

const NAME_LIMIT = 20;

// 個人檔案頁的帳號設定：名稱、密碼與登入裝置。
export function AccountSettings({ apiClient }: { apiClient: StudydyApiClient }) {
  const profile = useProfile(apiClient);
  const [sessions, setSessions] = useState<AccountSessions | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getAccountSessions().then((value) => { if (!cancelled) setSessions(value); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient]);
  return (
    <section className="account-settings surface" aria-labelledby="account-settings-title">
      <h2 id="account-settings-title">帳號設定</h2>
      {/* 名稱讀到後才重新掛載表單，草稿從目前名稱開始。 */}
      <NameSection key={profile ? "ready" : "loading"} apiClient={apiClient} initial={profile?.display_name ?? ""} />
      <PasswordSection apiClient={apiClient} onChanged={setSessions} />
      <DevicesSection apiClient={apiClient} sessions={sessions} onChanged={setSessions} />
    </section>
  );
}

function NameSection({ apiClient, initial }: { apiClient: StudydyApiClient; initial: string }) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const length = [...draft.trim()].length;
  return (
    <section aria-labelledby="account-name-heading">
      <h3 id="account-name-heading">個人名稱</h3>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (busy || length > NAME_LIMIT) return;
        setBusy(true);
        setError(null);
        setDone(false);
        void apiClient.updateProfile(draft.trim() || null).then(
          () => { setBusy(false); setDone(true); },
          (failure) => { setBusy(false); setError(errorMessage(failure)); },
        );
      }}>
        <label className="profile-field">
          <span>顯示名稱</span>
          <input value={draft} disabled={busy} placeholder="例如：以安"
            aria-describedby="profile-name-help" aria-invalid={length > NAME_LIMIT || undefined}
            onChange={(event) => { setDraft(event.currentTarget.value); setDone(false); }} />
        </label>
        <p id="profile-name-help" className={length > NAME_LIMIT ? "form-error" : "profile-help"}>
          {length > NAME_LIMIT ? `名稱最多 ${NAME_LIMIT} 個字，目前 ${length} 個字。` : `會顯示在首頁問候語；留空即不顯示名稱。（${length}/${NAME_LIMIT}）`}
        </p>
        {error && <p className="form-error" role="alert">{error}</p>}
        {done && <p className="form-success" role="status">名稱已更新。</p>}
        <footer>
          <button className="primary-button" type="submit" disabled={busy || length > NAME_LIMIT}>{busy ? "正在儲存…" : "儲存名稱"}</button>
        </footer>
      </form>
    </section>
  );
}

// 長度與註冊相同（15–128 個字元，以 Unicode 字元計算，與後端一致）。
const PASSWORD_MIN = 15;
const PASSWORD_MAX = 128;

function PasswordSection({ apiClient, onChanged }: {
  apiClient: StudydyApiClient; onChanged: (sessions: AccountSessions) => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const currentField = useRef<HTMLInputElement>(null);
  // 送出期間欄位停用；等重新啟用後才把焦點移回目前密碼。
  const [refocus, setRefocus] = useState(0);
  useEffect(() => { if (refocus) currentField.current?.focus(); }, [refocus]);
  const nextLength = [...next].length;
  const lengthInvalid = next !== "" && (nextLength < PASSWORD_MIN || nextLength > PASSWORD_MAX);
  const mismatch = confirm !== "" && confirm !== next;
  const ready = current !== "" && next !== "" && !lengthInvalid && confirm === next;
  const edit = (setter: (value: string) => void) => (event: { currentTarget: HTMLInputElement }) => {
    setter(event.currentTarget.value);
    setDone(false);
  };
  return (
    <section aria-labelledby="account-password-heading">
      <h3 id="account-password-heading">修改密碼</h3>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (busy || !ready) return;
        setBusy(true);
        setError(null);
        setDone(false);
        void apiClient.changePassword(current, next).then(
          (sessions) => {
            setBusy(false);
            setCurrent(""); setNext(""); setConfirm("");
            setDone(true);
            onChanged(sessions);
          },
          (failure) => {
            setBusy(false);
            const reason = failure instanceof ApiClientError ? failure.reasonCode : null;
            setError(reason === "REQUEST_INVALID" ? `新密碼需為 ${PASSWORD_MIN}–${PASSWORD_MAX} 個字元。` : errorMessage(failure));
            // 目前密碼錯誤時清空該欄重新輸入，新密碼保留。
            if (reason === "CURRENT_PASSWORD_INVALID") {
              setCurrent("");
              setRefocus((value) => value + 1);
            }
          },
        );
      }}>
        <label className="profile-field">
          <span>目前密碼</span>
          <input ref={currentField} type="password" autoComplete="current-password" maxLength={PASSWORD_MAX}
            value={current} disabled={busy} onChange={edit(setCurrent)} />
        </label>
        <label className="profile-field">
          <span>新密碼</span>
          <input type="password" autoComplete="new-password" value={next} disabled={busy}
            aria-describedby="account-password-help" aria-invalid={lengthInvalid || undefined} onChange={edit(setNext)} />
        </label>
        <p id="account-password-help" className={lengthInvalid ? "form-error" : "profile-help"}>
          {lengthInvalid ? `新密碼需為 ${PASSWORD_MIN}–${PASSWORD_MAX} 個字元，目前 ${nextLength} 個字元。` : `${PASSWORD_MIN}–${PASSWORD_MAX} 個字元。更新後其他裝置需要用新密碼重新登入。`}
        </p>
        <label className="profile-field">
          <span>確認新密碼</span>
          <input type="password" autoComplete="new-password" value={confirm} disabled={busy}
            aria-describedby={mismatch ? "account-password-mismatch" : undefined} aria-invalid={mismatch || undefined} onChange={edit(setConfirm)} />
        </label>
        {mismatch && <p id="account-password-mismatch" className="form-error">兩次輸入的新密碼不一致。</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {done && <p className="form-success" role="status">密碼已更新，其他裝置已登出；這個裝置維持登入。</p>}
        <footer>
          <button className="primary-button" type="submit" disabled={busy || !ready}>{busy ? "正在更新…" : "更新密碼"}</button>
        </footer>
      </form>
    </section>
  );
}

function DevicesSection({ apiClient, sessions, onChanged }: {
  apiClient: StudydyApiClient; sessions: AccountSessions | null; onChanged: (sessions: AccountSessions) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const count = sessions?.active_sessions ?? null;
  return (
    <section aria-labelledby="account-devices-heading">
      <h3 id="account-devices-heading">登入裝置</h3>
      {/* 讀不到數量時不顯示數字，仍可操作。 */}
      <p className="profile-help">
        {count === null ? "可讓其他裝置上的登入全部失效，這個裝置會維持登入。"
          : count === 1 ? "目前只有這個裝置登入中。"
          : `目前有 ${count} 個有效登入（含這個裝置）。`}
      </p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {done && <p className="form-success" role="status">已登出其他裝置，目前只有這個裝置登入中。</p>}
      <footer>
        <button className="secondary-button" type="button" disabled={busy || count === 1} onClick={() => {
          setBusy(true);
          setError(null);
          setDone(false);
          void apiClient.revokeOtherSessions().then(
            (value) => { setBusy(false); setDone(true); onChanged(value); },
            (failure) => { setBusy(false); setError(errorMessage(failure)); },
          );
        }}>{busy ? "正在登出…" : "登出其他裝置"}</button>
      </footer>
    </section>
  );
}
