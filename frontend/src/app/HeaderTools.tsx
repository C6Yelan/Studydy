import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { LearnerProfile, LibrarySearchView, LibrarySearchCategory } from "../api/contracts";
import { Icon } from "../ui/Icon";
import { writeRoute } from "./routes";
import "./header-tools.css";

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

export function Modal({ label, open, onClose, className, children, onKeyDown }: {
  label: string; open: boolean; onClose: () => void; className: string; children: ReactNode; onKeyDown?: React.KeyboardEventHandler<HTMLDialogElement>;
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
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onKeyDown={onKeyDown}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      {open && children}
    </dialog>
  );
}

type SearchState = { query: string; category: LibrarySearchCategory; offset: number; result: LibrarySearchView | null; error: string | null };

// 頁首搜尋只有一個；其他頁面（例如找不到頁面）透過這裡開啟，不模擬鍵盤事件。
let searchOpener: { visible: () => boolean; open: () => void } | null = null;
/** 頁首目前是否顯示全站搜尋（教材內隱藏頁首時為 false）。 */
export function globalSearchAvailable() { return searchOpener?.visible() ?? false; }
/** 開啟頁首的全站搜尋；頁首未顯示搜尋時不動作。 */
export function openGlobalSearch() { if (searchOpener?.visible()) searchOpener.open(); }

// 依 history state 開啟指定概念（全站搜尋只在教材外出現，開啟的地圖一定是重新載入）。
function openConcept(concept: LibrarySearchView["concepts"][number]) {
  writeRoute(
    { name: "knowledge-map", materialId: concept.material_id, runId: concept.run_id, structureRevision: concept.knowledge_structure_revision },
    false,
    { knowledgeMap: { revision: concept.knowledge_structure_revision, mode: "focus", search: "", concept: concept.concept_id, detail: concept.concept_id, review: null, relation: null } },
  );
}

// 結果名稱中標出關鍵字（不分大小寫，只標第一處）。
function Highlight({ text, needle }: { text: string; needle: string }) {
  const at = needle ? text.toLocaleLowerCase().indexOf(needle) : -1;
  if (at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark>{text.slice(at, at + needle.length)}</mark>{text.slice(at + needle.length)}</>;
}

// 方向鍵在搜尋欄與結果之間移動焦點。
function moveFocus(container: HTMLElement | null, from: Element | null, step: 1 | -1, input: HTMLInputElement | null) {
  const items = [...(container?.querySelectorAll<HTMLButtonElement>('li button:not(:disabled), .search-pagination button:not(:disabled), [role="alert"] button') ?? [])];
  if (!items.length) return;
  const index = items.indexOf(from as HTMLButtonElement);
  const next = index + step;
  if (next < 0) input?.focus();
  else items[Math.min(next, items.length - 1)].focus();
}

const searchCategories = [
  { id: "all", label: "全部" }, { id: "materials", label: "教材" }, { id: "concepts", label: "概念" },
  { id: "card_sets", label: "卡組" }, { id: "podcasts", label: "Podcast" },
] as const;

function GlobalSearch({ apiClient }: { apiClient: StudydyApiClient }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [category, setCategory] = useState<LibrarySearchCategory>("all");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<SearchState>({ query: "", category: "all", offset: 0, result: null, error: null });
  const [retry, setRetry] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const resultsBox = useRef<HTMLDivElement>(null);
  const inputBox = useRef<HTMLInputElement>(null);
  const focusAfterPage = useRef(false);
  const query = text.trim();
  useEffect(() => { if (open) inputBox.current?.focus(); }, [open]);
  useEffect(() => {
    const opener = { visible: () => trigger.current?.checkVisibility() ?? false, open: () => setOpen(true) };
    searchOpener = opener;
    return () => { if (searchOpener === opener) searchOpener = null; };
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (!trigger.current?.checkVisibility() || target?.closest("input, textarea, select, [contenteditable='true'], dialog")) return;
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
      void apiClient.searchLibrary(query, { category, offset, limit: 30 }).then(
        (page) => {
          if (cancelled) return;
          setState({ query, category, offset, result: page, error: null });
        },
        (error) => {
          if (cancelled) return;
          setState({ query, category, offset, result: null, error: errorMessage(error) });
        },
      );
    }, offset > 0 ? 0 : 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, open, query, category, offset, retry]);
  useEffect(() => {
    if (!focusAfterPage.current || state.query !== query || state.category !== category || state.offset !== offset || !state.result) return;
    focusAfterPage.current = false;
    resultsBox.current?.querySelector<HTMLButtonElement>("section li button")?.focus({ preventScroll: true });
    if (resultsBox.current) resultsBox.current.scrollTop = 0;
  }, [state, query, category, offset]);
  const changePage = (nextOffset: number) => { focusAfterPage.current = true; setOffset(nextOffset); };
  const selectCategory = (next: LibrarySearchCategory) => { focusAfterPage.current = false; setCategory(next); setOffset(0); };
  const close = () => {
    setOpen(false); setText(""); setCategory("all"); setOffset(0); focusAfterPage.current = false;
    setState({ query: "", category: "all", offset: 0, result: null, error: null });
    trigger.current?.focus({ preventScroll: true });
  };
  const current = state.query === query && state.category === category && state.offset === offset ? state : null;
  const result = current?.result;
  const needle = query.toLocaleLowerCase();
  const groups = result ? [
    { id: "materials", label: "教材", icon: "book", items: result.materials.map(item => ({ key: item.material_id, title: item.display_name,
      meta: item.knowledge_structure_revision ? "開啟知識地圖" : "知識地圖尚未建立", onOpen: () => {
        if (item.run_id && item.knowledge_structure_revision) writeRoute({ name: "knowledge-map", materialId: item.material_id, runId: item.run_id, structureRevision: item.knowledge_structure_revision });
        else writeRoute({ name: "materials" });
      } })) },
    { id: "concepts", label: "概念", icon: "map", items: result.concepts.map(item => ({ key: `${item.material_id}:${item.concept_id}`, title: item.label,
      meta: `${item.claim_count} 個重點 · ${item.material_name}`, onOpen: () => openConcept(item) })) },
    { id: "card_sets", label: "卡組", icon: "cards", items: result.card_sets.map(item => ({ key: item.card_set_id, title: item.name,
      meta: `${item.card_count !== null ? `${item.card_count} 張卡片 · ` : ""}${item.material_name}`,
      onOpen: () => writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id }) })) },
    { id: "podcasts", label: "Podcast", icon: "headphones", items: result.podcasts.map(item => ({ key: item.podcast_id, title: item.name,
      meta: `${item.episode_count} 集 · ${item.material_name}`,
      onOpen: () => writeRoute({ name: "podcast", podcastId: item.podcast_id, materialId: item.material_id }) })) },
  ] as const : [];
  const total = result ? Object.values(result.totals).reduce((sum, count) => sum + count, 0) : 0;
  const count = category === "all" ? total : result?.totals[category];
  return <>
    <button ref={trigger} className="header-search-trigger" type="button" aria-haspopup="dialog" aria-label="搜尋概念、教材、卡組或 Podcast" onClick={() => setOpen(true)}>
      <Icon name="search" size={18} /><span>搜尋全部內容…</span><kbd aria-hidden="true">/</kbd>
    </button>
    <Modal label="全站搜尋" open={open} onClose={close} className="search-dialog" onKeyDown={event => {
      if (category === "all" || !result || (event.key !== "PageDown" && event.key !== "PageUp")) return;
      event.preventDefault();
      const next = event.key === "PageDown" ? offset + 30 : offset - 30;
      if (next >= 0 && next < result.totals[category]) changePage(next);
    }}>
      <div className="search-categories" role="tablist" aria-label="搜尋分類" onKeyDown={event => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const index = searchCategories.findIndex(item => item.id === category);
        const next = (index + (event.key === "ArrowRight" ? 1 : -1) + searchCategories.length) % searchCategories.length;
        selectCategory(searchCategories[next].id);
        event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next].focus();
      }}>
        {searchCategories.map(item => <button key={item.id} type="button" role="tab" id={`search-tab-${item.id}`} aria-selected={category === item.id}
          aria-controls="search-results" tabIndex={category === item.id ? 0 : -1} onClick={() => selectCategory(item.id)}>
          {item.label}{result && <span>{item.id === "all" ? total : result.totals[item.id]}</span>}
        </button>)}
      </div>
      <form role="search" aria-label="搜尋全部內容" onSubmit={event => {
        event.preventDefault();
        if (category === "all" && result?.concepts[0]) { close(); openConcept(result.concepts[0]); return; }
        const first = groups.find(group => (category === "all" || category === group.id) && group.items.length)?.items[0];
        if (first) { close(); first.onOpen(); }
      }}>
        <Icon name="search" size={20} />
        <input ref={inputBox} autoFocus type="search" aria-label="搜尋概念、教材、卡組或 Podcast"
          placeholder="輸入概念、教材、卡組或 Podcast 名稱…" maxLength={200} value={text}
          onChange={event => { setText(event.currentTarget.value); setOffset(0); focusAfterPage.current = false; }}
          onKeyDown={event => {
            if (event.key === "Escape") { event.preventDefault(); close(); }
            if (event.key === "ArrowDown") { event.preventDefault(); moveFocus(resultsBox.current, null, 1, inputBox.current); }
          }} />
        <button className="header-dialog-close" type="button" aria-label="關閉搜尋" onClick={close}>×</button>
      </form>
      <div ref={resultsBox} id="search-results" role="tabpanel" aria-labelledby={`search-tab-${category}`} className="search-results" onKeyDown={event => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault();
        moveFocus(resultsBox.current, document.activeElement, event.key === "ArrowDown" ? 1 : -1, inputBox.current);
      }}>
        {!query ? <p className="search-hint">搜尋所有教材的概念名稱與別名，以及教材、卡組與 Podcast 名稱。</p>
          : !current ? <p role="status">正在搜尋…</p> : <>
            {current.error && <p role="alert">{current.error}<button className="text-button" type="button" onClick={() => setRetry(value => value + 1)}>重新搜尋</button></p>}
            {result && <>
              <p className="header-sr-only" role="status">找到 {result.totals.materials} 份教材、{result.totals.concepts} 個概念、{result.totals.card_sets} 個卡組、{result.totals.podcasts} 份 Podcast</p>
              {count === 0 && <p role="status">找不到「{query}」，試試其他關鍵字。</p>}
              {groups.filter(group => category === "all" || category === group.id).map(group => group.items.length > 0 && <section key={group.id} aria-label={group.label}>
                <h3>{group.label}<span>{result.totals[group.id]}</span></h3>
                <ul>{(category === "all" ? group.items.slice(0, 3) : group.items).map(item => <li key={item.key}>
                  <button type="button" onClick={() => { close(); item.onOpen(); }}><Icon name={group.icon} size={18} />
                    <span><b><Highlight text={item.title} needle={needle} /></b><small>{item.meta}</small></span>
                  </button>
                </li>)}
                  {category === "all" && result.totals[group.id] > 3 && <li><button className="search-more" type="button" onClick={() => selectCategory(group.id)}>看全部 {result.totals[group.id]} 筆</button></li>}
                </ul>
              </section>)}
              {category !== "all" && <nav className="search-pagination" aria-label="搜尋結果分頁">
                <button type="button" disabled={offset === 0} onClick={() => changePage(offset - 30)}>上一頁</button>
                <span>第 {Math.floor(offset / 30) + 1}／{Math.max(1, Math.ceil(result.totals[category] / 30))} 頁</span>
                <button type="button" disabled={offset + 30 >= result.totals[category]} onClick={() => changePage(offset + 30)}>下一頁</button>
              </nav>}
            </>}
          </>}
      </div>
      <p className="search-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> 選擇　<kbd>Enter</kbd> 開啟　<kbd>Esc</kbd> 關閉</p>
    </Modal>
  </>;
}

// 頁首頭像：前往個人檔案（成就與帳號設定）。
function ProfileButton({ apiClient, current }: { apiClient: StudydyApiClient; current: boolean }) {
  const name = useProfile(apiClient)?.display_name ?? null;
  return (
    <button className="account-profile" type="button" aria-current={current ? "page" : undefined}
      aria-label={`個人檔案：${name ?? "尚未設定名稱"}`} onClick={() => writeRoute({ name: "profile" })}>
      <span className="account-avatar" aria-hidden="true">{name ? [...name][0] : <Icon name="user" size={18} />}</span>
      {name && <span className="account-name">{name}</span>}
    </button>
  );
}

export function HeaderTools({ apiClient, profileCurrent = false }: { apiClient: StudydyApiClient; profileCurrent?: boolean }) {
  return <>
    <GlobalSearch apiClient={apiClient} />
    <ProfileButton apiClient={apiClient} current={profileCurrent} />
  </>;
}
