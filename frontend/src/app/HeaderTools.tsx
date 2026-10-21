import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { LearnerProfile, LibrarySearchView } from "../api/contracts";
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

function ProfileButton({ apiClient }: { apiClient: StudydyApiClient }) {
  const profile = useProfile(apiClient);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const name = profile?.display_name ?? null;
  const length = [...draft.trim()].length;
  const close = () => {
    if (busy) return;
    setOpen(false);
    setError(null);
    trigger.current?.focus({ preventScroll: true });
  };
  return (
    <>
      <button ref={trigger} className="account-profile" type="button" aria-haspopup="dialog"
        aria-label={`帳號設定：${name ?? "尚未設定名稱"}`}
        onClick={() => { setDraft(name ?? ""); setError(null); setOpen(true); }}>
        <span className="account-avatar" aria-hidden="true">{name ? [...name][0] : <Icon name="user" size={18} />}</span>
        {name && <span className="account-name">{name}</span>}
      </button>
      <Modal label="帳號設定" open={open} onClose={close} className="profile-dialog">
        <form onSubmit={(event) => {
          event.preventDefault();
          if (busy || length > NAME_LIMIT) return;
          setBusy(true);
          setError(null);
          void apiClient.updateProfile(draft.trim() || null).then(
            () => { setBusy(false); setOpen(false); trigger.current?.focus({ preventScroll: true }); },
            (failure) => { setBusy(false); setError(errorMessage(failure)); },
          );
        }}>
          <header>
            <h2>帳號設定</h2>
            <button className="header-dialog-close" type="button" aria-label="關閉帳號設定" onClick={close}>×</button>
          </header>
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
            <button className="secondary-button" type="button" disabled={busy} onClick={close}>取消</button>
            <button className="primary-button" type="submit" disabled={busy || length > NAME_LIMIT}>{busy ? "正在儲存…" : "儲存"}</button>
          </footer>
        </form>
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
