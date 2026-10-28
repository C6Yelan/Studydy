import { useEffect, useRef, useState } from "react";
import { InlineConfirm } from "../../ui/InlineConfirm";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { AccountSession, AccountSessions } from "../../api/contracts";
import { useProfile } from "../../app/HeaderTools";
import { playCue, setSoundEnabled, soundEnabled } from "../../ui/feedback-sound";
import { Icon } from "../../ui/Icon";

const NAME_LIMIT = 20;

// 登入裝置清單：帳號設定（改密碼後其他裝置登出）與登入裝置卡片共用同一份。
// undefined 表示讀取中，null 表示讀取失敗。
export function useAccountSessions(apiClient: StudydyApiClient): [AccountSessions | null | undefined, (sessions: AccountSessions) => void] {
  const [sessions, setSessions] = useState<AccountSessions | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getAccountSessions().then(
      (value) => { if (!cancelled) setSessions(value); },
      () => { if (!cancelled) setSessions(null); },
    );
    return () => { cancelled = true; };
  }, [apiClient]);
  return [sessions, setSessions];
}

// 個人檔案頁的帳號設定：名稱與密碼；登入裝置是獨立的卡片（DevicesCard）。
export function AccountSettings({ apiClient, onSessionsChanged }: {
  apiClient: StudydyApiClient; onSessionsChanged: (sessions: AccountSessions) => void;
}) {
  const profile = useProfile(apiClient);
  return (
    <section className="account-settings surface" aria-labelledby="account-settings-title">
      <h2 id="account-settings-title">帳號設定</h2>
      {/* 名稱讀到後才重新掛載表單，草稿從目前名稱開始。 */}
      <NameSection key={profile ? "ready" : "loading"} apiClient={apiClient} initial={profile?.display_name ?? ""} />
      <PasswordSection apiClient={apiClient} onChanged={onSessionsChanged} />
      <SoundSection />
    </section>
  );
}

// 音效開關：只存在這個瀏覽器，不屬於帳號資料。
function SoundSection() {
  const [on, setOn] = useState(soundEnabled);
  return (
    <section aria-labelledby="account-sound-heading">
      <h3 id="account-sound-heading">音效</h3>
      <label className="sound-toggle">
        <input type="checkbox" checked={on} onChange={(event) => {
          const next = event.currentTarget.checked;
          setSoundEnabled(next);
          setOn(next);
          if (next) playCue("click");
        }} />
        <span>播放操作音效</span>
      </label>
      <p className="profile-help">答題、升級、取得徽章與完成關卡時的提示音，只保存在這個瀏覽器。</p>
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

// 收合時顯示的裝置數（含這個裝置）；寬螢幕兩欄排列剛好一列，讓個人檔案不必整頁捲動。
const VISIBLE_DEVICES = 2;
const taipeiDate = new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "numeric", day: "numeric" });
const taipeiDateWithYear = new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "numeric", day: "numeric" });
const taipeiFull = new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", dateStyle: "medium", timeStyle: "short" });
const taipeiYear = new Intl.DateTimeFormat("en", { timeZone: "Asia/Taipei", year: "numeric" });

// 一週內用相對時間，更早顯示台灣時區的日期（不同年份才加年份）。
function relativeTime(value: string, now = Date.now()): string {
  const at = Date.parse(value);
  const minutes = Math.floor((now - at) / 60_000);
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} 小時前`;
  if (minutes < 7 * 24 * 60) return `${Math.floor(minutes / (24 * 60))} 天前`;
  return calendarDate(value, now);
}

function calendarDate(value: string, now = Date.now()): string {
  const sameYear = taipeiYear.format(Date.parse(value)) === taipeiYear.format(now);
  return (sameYear ? taipeiDate : taipeiDateWithYear).format(Date.parse(value));
}

const phoneSystems = ["iPhone", "iPad", "Android"];

export function DevicesCard({ apiClient, sessions, onChanged }: {
  apiClient: StudydyApiClient; sessions: AccountSessions | null | undefined; onChanged: (sessions: AccountSessions) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const list = sessions?.sessions ?? [];
  const shown = expanded ? list : list.slice(0, VISIBLE_DEVICES);
  const hidden = list.length - shown.length;
  return (
    <section className="profile-devices surface" aria-labelledby="account-devices-heading">
      <header>
        <h2 id="account-devices-heading" ref={heading} tabIndex={-1}>登入裝置</h2>
        {(hidden > 0 || expanded) && <button className="text-button device-more" type="button" aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}>{expanded ? "收合" : `顯示其他 ${hidden} 個裝置`}</button>}
        <button className="secondary-button" type="button" disabled={busy || list.length === 1} onClick={() => {
          setBusy(true);
          setError(null);
          setNotice(null);
          void apiClient.revokeOtherSessions().then(
            (value) => { setBusy(false); setNotice("已登出其他裝置，目前只有這個裝置登入中。"); setExpanded(false); onChanged(value); },
            (failure) => { setBusy(false); setError(errorMessage(failure)); },
          );
        }}>{busy ? "正在登出…" : "登出其他裝置"}</button>
      </header>
      {sessions === undefined && <p className="profile-help" role="status">正在讀取登入裝置…</p>}
      {/* 讀不到清單時仍可一次登出其他裝置。 */}
      {sessions === null && <p className="profile-help">無法讀取登入裝置清單；仍可讓其他裝置上的登入全部失效，這個裝置會維持登入。</p>}
      {sessions && <ul className={`device-list${expanded ? " is-expanded" : ""}`}>
        {shown.map((item) => <DeviceRow key={item.session_id} apiClient={apiClient} session={item} onRevoked={(value) => {
          setError(null);
          setNotice(`已登出「${item.device_label ?? "未知裝置"}」。`);
          onChanged(value);
          heading.current?.focus();
        }} />)}
      </ul>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-success" role="status">{notice}</p>}
    </section>
  );
}

// 一個登入：這個裝置只顯示標記；其他裝置可在列內確認後個別登出。
function DeviceRow({ apiClient, session, onRevoked }: {
  apiClient: StudydyApiClient; session: AccountSession; onRevoked: (sessions: AccountSessions) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const label = session.device_label ?? "未知裝置";
  const system = session.device_label?.split(" · ").at(-1) ?? "";
  const close = () => { setConfirming(false); setError(null); requestAnimationFrame(() => opener.current?.focus()); };
  return (
    <li className={`device-row${session.current ? " is-current" : ""}`}>
      <span className="stat-icon is-blue"><Icon name={phoneSystems.includes(system) ? "phone" : "monitor"} size={16} /></span>
      <div className="device-text">
        <strong>{label}{session.current && <span className="device-current">這個裝置</span>}</strong>
        <small>
          {session.current ? "使用中" : <>最後使用 <time dateTime={session.last_used_at} title={taipeiFull.format(Date.parse(session.last_used_at))}>{relativeTime(session.last_used_at)}</time></>}
          {" · "}<time dateTime={session.created_at} title={taipeiFull.format(Date.parse(session.created_at))}>{calendarDate(session.created_at)}</time> 登入
        </small>
      </div>
      {!session.current && !confirming && <button ref={opener} className="text-button" type="button"
        aria-label={`登出「${label}」`} onClick={() => setConfirming(true)}>登出</button>}
      {confirming && <InlineConfirm message="登出這個裝置？" label={`登出「${label}」確認`} confirmLabel="登出" busyLabel="正在登出…"
        busy={busy} error={error} onCancel={close} onConfirm={() => {
          setBusy(true);
          setError(null);
          void apiClient.revokeSession(session.session_id).then(
            (value) => { setBusy(false); onRevoked(value); },
            (failure) => { setBusy(false); setError(errorMessage(failure)); },
          );
        }} />}
    </li>
  );
}
