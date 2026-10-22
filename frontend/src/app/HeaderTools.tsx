import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { CardSetSummary, LearnerProfile, LibrarySearchView, PodcastSummary } from "../api/contracts";
import { Icon } from "../ui/Icon";
import { writeRoute } from "./routes";
import "./header-tools.css";
import { useDismissibleMenu } from "../features/material-flow/useDismissibleMenu";

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
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      {open && children}
    </dialog>
  );
}

type SearchState = { query: string; result: LibrarySearchView | null; error: string | null };

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
  const items = [...(container?.querySelectorAll<HTMLButtonElement>("li button") ?? [])];
  if (!items.length) return;
  const index = items.indexOf(from as HTMLButtonElement);
  const next = index + step;
  if (next < 0) input?.focus();
  else items[Math.min(next, items.length - 1)].focus();
}

function GlobalSearch({ apiClient }: { apiClient: StudydyApiClient }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<SearchState>({ query: "", result: null, error: null });
  const [retry, setRetry] = useState(0);
  // 卡組與 Podcast 數量少，開啟搜尋時讀一次清單，在前端比對名稱與所屬教材；讀不到時只少這兩類結果。
  const [lists, setLists] = useState<{ cardSets: CardSetSummary[] | null; podcasts: PodcastSummary[] | null } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const resultsBox = useRef<HTMLDivElement>(null);
  const inputBox = useRef<HTMLInputElement>(null);
  const query = text.trim();
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void Promise.all([
      apiClient.listCardSets().then((value) => value.card_sets, () => null),
      apiClient.listPodcasts().then((value) => value.podcasts, () => null),
    ]).then(([cardSets, podcasts]) => { if (!cancelled) setLists({ cardSets, podcasts }); });
    return () => { cancelled = true; };
  }, [apiClient, open]);
  useEffect(() => {
    // 「/」開啟搜尋；正在輸入其他欄位時不攔截。教材內隱藏全站頁首，也不開全站搜尋（教材有自己的地圖搜尋）。
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (!trigger.current?.checkVisibility()) return;
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
    setLists(null);
    trigger.current?.focus({ preventScroll: true });
  };
  const current = state.query === query ? state : null;
  const result = current?.result;
  const needle = query.toLocaleLowerCase();
  const matches = (item: { name: string; material_name: string }) => `${item.name} ${item.material_name}`.toLocaleLowerCase().includes(needle);
  const cardSets = lists?.cardSets?.filter(matches) ?? [];
  const podcasts = lists?.podcasts?.filter(matches) ?? [];
  const empty = result && !result.materials.length && !result.concepts.length && !cardSets.length && !podcasts.length;
  return (
    <>
      <button ref={trigger} className="header-search-trigger" type="button" aria-haspopup="dialog" aria-label="搜尋概念、教材、卡組或 Podcast" onClick={() => setOpen(true)}>
        <Icon name="search" size={18} />
        <span>搜尋全部內容…</span>
        <kbd aria-hidden="true">/</kbd>
      </button>
      <Modal label="全站搜尋" open={open} onClose={close} className="search-dialog">
        <form role="search" aria-label="搜尋全部內容" onSubmit={(event) => {
          event.preventDefault();
          if (result?.concepts[0]) { close(); openConcept(result.concepts[0]); }
        }}>
          <Icon name="search" size={20} />
          <input
            ref={inputBox}
            autoFocus
            type="search"
            aria-label="搜尋概念、教材、卡組或 Podcast"
            placeholder="輸入概念、教材、卡組或 Podcast 名稱…"
            maxLength={200}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
            // 搜尋欄有文字時，瀏覽器的 Escape 只會清空欄位；這裡一律直接關閉搜尋。
            onKeyDown={(event) => {
              if (event.key === "Escape") { event.preventDefault(); close(); }
              if (event.key === "ArrowDown") { event.preventDefault(); moveFocus(resultsBox.current, null, 1, inputBox.current); }
            }}
          />
          <button className="header-dialog-close" type="button" aria-label="關閉搜尋" onClick={close}>×</button>
        </form>
        <div ref={resultsBox} className="search-results" onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          moveFocus(resultsBox.current, document.activeElement, event.key === "ArrowDown" ? 1 : -1, inputBox.current);
        }}>
          {!query ? <p className="search-hint">搜尋所有教材的概念名稱與別名，以及教材、卡組與 Podcast 名稱。</p>
            : !current ? <p role="status">正在搜尋…</p>
            : current.error ? <p role="alert">{current.error}<button className="text-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新搜尋</button></p>
            : empty ? <p role="status">找不到「{query}」，試試其他關鍵字。</p>
            : result && <>
              <p className="header-sr-only" role="status">找到 {result.materials.length} 份教材、{result.concepts.length} 個概念、{cardSets.length} 個卡組、{podcasts.length} 份 Podcast</p>
              {result.materials.length > 0 && <section aria-label="教材">
                <h3>教材<span>{result.materials.length}</span></h3>
                <ul>{result.materials.map((material) => <li key={material.material_id}>
                  <button type="button" onClick={() => {
                    close();
                    if (material.run_id && material.knowledge_structure_revision)
                      writeRoute({ name: "knowledge-map", materialId: material.material_id, runId: material.run_id, structureRevision: material.knowledge_structure_revision });
                    else writeRoute({ name: "materials" });
                  }}>
                    <Icon name="book" size={18} />
                    <span><b><Highlight text={material.display_name} needle={needle} /></b><small>{material.knowledge_structure_revision ? "開啟知識地圖" : "知識地圖尚未建立"}</small></span>
                  </button>
                </li>)}</ul>
              </section>}
              {result.concepts.length > 0 && <section aria-label="概念">
                <h3>概念<span>{result.concepts.length}{result.concepts_truncated ? "+" : ""}</span></h3>
                <ul>{result.concepts.map((concept) => <li key={`${concept.material_id}:${concept.concept_id}`}>
                  <button type="button" onClick={() => { close(); openConcept(concept); }}>
                    <Icon name="map" size={18} />
                    <span><b><Highlight text={concept.label} needle={needle} /></b><small>{concept.claim_count} 個重點 · {concept.material_name}</small></span>
                  </button>
                </li>)}</ul>
                {result.concepts_truncated && <p className="search-hint">只顯示前 {result.concepts.length} 個概念，輸入更多關鍵字可縮小範圍。</p>}
              </section>}
              {cardSets.length > 0 && <section aria-label="卡組">
                <h3>卡組<span>{cardSets.length}</span></h3>
                <ul>{cardSets.map((item) => <li key={item.card_set_id}>
                  <button type="button" onClick={() => { close(); writeRoute({ name: "card-set", cardSetId: item.card_set_id, materialId: item.material_id }); }}>
                    <Icon name="cards" size={18} />
                    <span><b><Highlight text={item.name} needle={needle} /></b><small>{item.card_count !== null ? `${item.card_count} 張卡片 · ` : ""}{item.material_name}</small></span>
                  </button>
                </li>)}</ul>
              </section>}
              {podcasts.length > 0 && <section aria-label="Podcast">
                <h3>Podcast<span>{podcasts.length}</span></h3>
                <ul>{podcasts.map((item) => <li key={item.podcast_id}>
                  <button type="button" onClick={() => { close(); writeRoute({ name: "podcast", podcastId: item.podcast_id, materialId: item.material_id }); }}>
                    <Icon name="headphones" size={18} />
                    <span><b><Highlight text={item.name} needle={needle} /></b><small>{item.episode_count} 集 · {item.material_name}</small></span>
                  </button>
                </li>)}</ul>
              </section>}
              {lists && (!lists.cardSets || !lists.podcasts) && <p className="search-hint">無法讀取卡組或 Podcast 清單，這兩類結果暫時不顯示。</p>}
            </>}
        </div>
        <p className="search-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> 選擇　<kbd>Enter</kbd> 開啟第一個概念　<kbd>Esc</kbd> 關閉</p>
      </Modal>
    </>
  );
}

/** 登出動作；教材頁首沒有全站的登出按鈕，由頭像選單取用。 */
export const LogoutContext = createContext<(() => void) | null>(null);

// 教材頁首的頭像選單：個人檔案、成就與登出（進入教材後不顯示全站頁首）。
export function AccountMenu({ apiClient }: { apiClient: StudydyApiClient }) {
  const name = useProfile(apiClient)?.display_name ?? null;
  const logout = useContext(LogoutContext);
  const menu = useRef<HTMLDetailsElement>(null);
  const opener = useRef<HTMLElement>(null);
  useDismissibleMenu(menu, opener);
  const go = (route: Parameters<typeof writeRoute>[0]) => { if (menu.current) menu.current.open = false; writeRoute(route); };
  return <details ref={menu} className="account-menu">
    <summary ref={opener} className="account-profile" role="button" aria-label={`帳號選單：${name ?? "尚未設定名稱"}`}>
      <span className="account-avatar" aria-hidden="true">{name ? [...name][0] : <Icon name="user" size={18} />}</span>
    </summary>
    <div className="account-menu-list">
      {name && <p className="account-menu-name">{name}</p>}
      <button type="button" onClick={() => go({ name: "profile" })}><Icon name="user" size={18} />個人檔案</button>
      <button type="button" onClick={() => go({ name: "achievements" })}><Icon name="sparkle" size={18} />成就</button>
      {logout && <button type="button" onClick={() => { if (menu.current) menu.current.open = false; logout(); }}><Icon name="logout" size={18} />登出</button>}
    </div>
  </details>;
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
