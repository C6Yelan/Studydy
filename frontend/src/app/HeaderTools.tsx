import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../api/client";
import type { AccountSessions, LearnerProfile, LibrarySearchView } from "../api/contracts";
import { Icon } from "../ui/Icon";
import { writeRoute } from "./routes";

const NAME_LIMIT = 20;

// 名稱與頁首、首頁共用；讀取失敗時視為未設定，不阻擋其他功能。
export function useProfile(apiClient: StudydyApiClient): LearnerProfile | null {
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getProfile().then((value) => { if (!cancelled) setProfile(value); }, () => undefined);
    const unsubscribe = apiClient.onProfileChange(setProfile);
    return () => { cancelled = true; unsubscribe(); };
  }, [apiClient]);
  return profile;
}

export function Modal({ label, open, onClose, className, children }: {
  label: string; open: boolean; onClose: () => void; className: string; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`header-dialog ${className}`}
      aria-label={label}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      {open && children}
    </dialog>
  );
}

type SearchState = { query: string; result: LibrarySearchView | null; error: string | null };

// 依 history state 開啟指定概念；同一張地圖已開啟時由地圖監聽 popstate 處理。
function openConcept(concept: LibrarySearchView["concepts"][number]) {
  writeRoute(
    { name: "knowledge-map", materialId: concept.material_id, runId: concept.run_id, structureRevision: concept.knowledge_structure_revision },
    false,
    {
      knowledgeMap: { revision: concept.knowledge_structure_revision, mode: "focus", search: "", concept: concept.concept_id, detail: concept.concept_id, review: null, relation: null },
      searchFocus: { revision: concept.knowledge_structure_revision, concept: concept.concept_id, at: Date.now() },
    },
  );
}

function GlobalSearch({ apiClient }: { apiClient: StudydyApiClient }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<SearchState>({ query: "", result: null, error: null });
  const [retry, setRetry] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const query = text.trim();
  useEffect(() => {
    // 「/」開啟搜尋；正在輸入其他欄位時不攔截。
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (target?.closest("input, textarea, select, [contenteditable='true'], dialog")) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (!open || !query) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void apiClient.searchLibrary(query).then(
        (result) => { if (!cancelled) setState({ query, result, error: null }); },
        (error) => { if (!cancelled) setState({ query, result: null, error: errorMessage(error) }); },
      );
    }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, open, query, retry]);
  const close = () => {
    setOpen(false);
    setText("");
    setState({ query: "", result: null, error: null });
    trigger.current?.focus({ preventScroll: true });
  };
  const current = state.query === query ? state : null;
  const result = current?.result;
  const empty = result && !result.materials.length && !result.concepts.length;
  return (
    <>
      <button ref={trigger} className="header-search-trigger" type="button" aria-haspopup="dialog" aria-label="搜尋概念或教材" onClick={() => setOpen(true)}>
        <Icon name="search" size={18} />
        <span>搜尋概念或教材…</span>
        <kbd aria-hidden="true">/</kbd>
      </button>
      <Modal label="全站搜尋" open={open} onClose={close} className="search-dialog">
        <form role="search" aria-label="搜尋全部教材" onSubmit={(event) => {
          event.preventDefault();
          if (result?.concepts[0]) { close(); openConcept(result.concepts[0]); }
        }}>
          <Icon name="search" size={20} />
          <input
            autoFocus
            type="search"
            aria-label="搜尋概念或教材"
            placeholder="輸入概念或教材名稱…"
            maxLength={200}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
            // 搜尋欄有文字時，瀏覽器的 Escape 只會清空欄位；這裡一律直接關閉搜尋。
            onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); close(); } }}
          />
          <button className="header-dialog-close" type="button" aria-label="關閉搜尋" onClick={close}>×</button>
        </form>
        <div className="search-results">
          {!query ? <p className="search-hint">搜尋所有教材的概念名稱、別名與教材名稱。</p>
            : !current ? <p role="status">正在搜尋…</p>
            : current.error ? <p role="alert">{current.error}<button className="text-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新搜尋</button></p>
            : empty ? <p role="status">找不到「{query}」，試試其他關鍵字。</p>
            : result && <>
              <p className="header-sr-only" role="status">找到 {result.materials.length} 份教材、{result.concepts.length} 個概念</p>
              {result.materials.length > 0 && <section aria-label="教材">
                <h3>教材</h3>
                <ul>{result.materials.map((material) => <li key={material.material_id}>
                  <button type="button" onClick={() => {
                    close();
                    if (material.run_id && material.knowledge_structure_revision)
                      writeRoute({ name: "knowledge-map", materialId: material.material_id, runId: material.run_id, structureRevision: material.knowledge_structure_revision });
                    else writeRoute({ name: "materials" });
                  }}>
                    <Icon name="book" size={18} />
                    <span><b>{material.display_name}</b><small>{material.knowledge_structure_revision ? "開啟知識地圖" : "知識地圖尚未建立"}</small></span>
                  </button>
                </li>)}</ul>
              </section>}
              {result.concepts.length > 0 && <section aria-label="概念">
                <h3>概念</h3>
                <ul>{result.concepts.map((concept) => <li key={`${concept.material_id}:${concept.concept_id}`}>
                  <button type="button" onClick={() => { close(); openConcept(concept); }}>
                    <Icon name="map" size={18} />
                    <span><b>{concept.label}</b><small>{concept.claim_count} 個重點 · {concept.material_name}</small></span>
                  </button>
                </li>)}</ul>
                {result.concepts_truncated && <p className="search-hint">只顯示前 {result.concepts.length} 個概念，輸入更多關鍵字可縮小範圍。</p>}
              </section>}
            </>}
        </div>
      </Modal>
    </>
  );
}

function NameSection({ apiClient, initial, onBusy, onSaved }: {
  apiClient: StudydyApiClient; initial: string; onBusy: (busy: boolean) => void; onSaved: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const length = [...draft.trim()].length;
  return (
    <section aria-labelledby="account-name-heading">
      <h3 id="account-name-heading">個人名稱</h3>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (busy || length > NAME_LIMIT) return;
        setBusy(true);
        onBusy(true);
        setError(null);
        void apiClient.updateProfile(draft.trim() || null).then(
          () => { setBusy(false); onBusy(false); onSaved(); },
          (failure) => { setBusy(false); onBusy(false); setError(errorMessage(failure)); },
        );
      }}>
        <label className="profile-field">
          <span>顯示名稱</span>
          <input autoFocus value={draft} disabled={busy} placeholder="例如：以安"
            aria-describedby="profile-name-help" aria-invalid={length > NAME_LIMIT || undefined}
            onChange={(event) => setDraft(event.currentTarget.value)} />
        </label>
        <p id="profile-name-help" className={length > NAME_LIMIT ? "form-error" : "profile-help"}>
          {length > NAME_LIMIT ? `名稱最多 ${NAME_LIMIT} 個字，目前 ${length} 個字。` : `會顯示在首頁問候語；留空即不顯示名稱。（${length}/${NAME_LIMIT}）`}
        </p>
        {error && <p className="form-error" role="alert">{error}</p>}
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

function PasswordSection({ apiClient, onBusy, onChanged }: {
  apiClient: StudydyApiClient; onBusy: (busy: boolean) => void; onChanged: (sessions: AccountSessions) => void;
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
        onBusy(true);
        setError(null);
        setDone(false);
        void apiClient.changePassword(current, next).then(
          (sessions) => {
            setBusy(false);
            onBusy(false);
            setCurrent(""); setNext(""); setConfirm("");
            setDone(true);
            onChanged(sessions);
          },
          (failure) => {
            setBusy(false);
            onBusy(false);
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

function DevicesSection({ apiClient, sessions, onBusy, onChanged }: {
  apiClient: StudydyApiClient; sessions: AccountSessions | null;
  onBusy: (busy: boolean) => void; onChanged: (sessions: AccountSessions) => void;
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
          onBusy(true);
          setError(null);
          setDone(false);
          void apiClient.revokeOtherSessions().then(
            (value) => { setBusy(false); onBusy(false); setDone(true); onChanged(value); },
            (failure) => { setBusy(false); onBusy(false); setError(errorMessage(failure)); },
          );
        }}>{busy ? "正在登出…" : "登出其他裝置"}</button>
      </footer>
    </section>
  );
}

function ProfileButton({ apiClient }: { apiClient: StudydyApiClient }) {
  const profile = useProfile(apiClient);
  const [open, setOpen] = useState(false);
  // 每次開啟重新掛載各區，清除上次輸入的密碼與訊息。
  const [opened, setOpened] = useState(0);
  const [busy, setBusy] = useState(0);
  const [sessions, setSessions] = useState<AccountSessions | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const name = profile?.display_name ?? null;
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSessions(null);
    void apiClient.getAccountSessions().then((value) => { if (!cancelled) setSessions(value); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, open, opened]);
  const close = () => {
    if (busy) return;
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  };
  const track = (value: boolean) => setBusy((count) => count + (value ? 1 : -1));
  return (
    <>
      <button ref={trigger} className="account-profile" type="button" aria-haspopup="dialog"
        aria-label={`帳號設定：${name ?? "尚未設定名稱"}`}
        onClick={() => { setOpened((value) => value + 1); setOpen(true); }}>
        <span className="account-avatar" aria-hidden="true">{name ? [...name][0] : <Icon name="user" size={18} />}</span>
        {name && <span className="account-name">{name}</span>}
      </button>
      <Modal label="帳號設定" open={open} onClose={close} className="profile-dialog">
        <header>
          <h2>帳號設定</h2>
          <button className="header-dialog-close" type="button" aria-label="關閉帳號設定" disabled={busy > 0} onClick={close}>×</button>
        </header>
        <div className="account-settings" key={opened}>
          <NameSection apiClient={apiClient} initial={name ?? ""} onBusy={track} onSaved={() => { setOpen(false); trigger.current?.focus({ preventScroll: true }); }} />
          <PasswordSection apiClient={apiClient} onBusy={track} onChanged={setSessions} />
          <DevicesSection apiClient={apiClient} sessions={sessions} onBusy={track} onChanged={setSessions} />
        </div>
      </Modal>
    </>
  );
}

export function HeaderTools({ apiClient }: { apiClient: StudydyApiClient }) {
  return <>
    <GlobalSearch apiClient={apiClient} />
    <ProfileButton apiClient={apiClient} />
  </>;
}
