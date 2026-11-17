// 全站搜尋只有一個（左側欄）；其他頁面（例如找不到頁面）透過這裡開啟，不模擬鍵盤事件。
let searchOpener: { visible: () => boolean; open: () => void } | null = null;

/** 搜尋元件掛上時註冊開啟器，回傳取消註冊的函式。 */
export function registerGlobalSearch(opener: { visible: () => boolean; open: () => void }) {
  searchOpener = opener;
  return () => { if (searchOpener === opener) searchOpener = null; };
}
/** 目前是否顯示全站搜尋（教材內隱藏左側欄時為 false）。 */
export function globalSearchAvailable() { return searchOpener?.visible() ?? false; }
/** 開啟全站搜尋；未顯示搜尋時不動作。 */
export function openGlobalSearch() { if (searchOpener?.visible()) searchOpener.open(); }
