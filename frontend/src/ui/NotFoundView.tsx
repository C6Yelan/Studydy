import { useEffect, useState } from "react";
import { writeRoute, type AppRoute } from "../app/routes";
import { globalSearchAvailable, openGlobalSearch } from "../app/HeaderTools";
import { Icon } from "./Icon";
import "./not-found.css";

/** 找不到頁面或資源時的共用畫面：吉祥物插圖＋說明＋回首頁（可再加一個相關清單的入口），頁首可用時另提供搜尋。 */
export function NotFoundView({
  title = "找不到這個頁面",
  description = "網址可能打錯了，或這個頁面已經移除。",
  back = { label: "我的教材", route: { name: "materials" } },
}: {
  title?: string;
  description?: string;
  /** 回到相關清單，例如「回到我的教材」。 */
  back?: { label: string; route: AppRoute } | null;
}) {
  // 全站搜尋在頁首；頁首隱藏搜尋時（例如教材內）不顯示這個入口。
  const [search, setSearch] = useState(false);
  useEffect(() => { setSearch(globalSearchAvailable()); }, []);
  return (
    <section className="not-found surface" aria-labelledby="not-found-title">
      <div className="not-found-art" aria-hidden="true">
        <span>404</span>
        <img src="/assets/studydy/failure-confused.png" alt="" />
      </div>
      <h1 id="not-found-title">{title}</h1>
      <p>{description}</p>
      <div className="not-found-actions">
        <button className="primary-button" type="button" onClick={() => writeRoute({ name: "home" })}>
          <Icon name="home" size={18} />回到首頁
        </button>
        {back && (
          <button className="secondary-button" type="button" onClick={() => writeRoute(back.route)}>
            {back.label}
          </button>
        )}
        {search && (
          <button className="text-button" type="button"
            onClick={openGlobalSearch}>
            <Icon name="search" size={16} />搜尋全部內容
          </button>
        )}
      </div>
    </section>
  );
}

export const materialNotFound = {
  title: "找不到這份教材",
  description: "這份教材可能已被刪除，或不屬於目前登入的帳號。",
  back: { label: "回到我的教材", route: { name: "materials" } as AppRoute },
};
