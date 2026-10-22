import { writeRoute, type AppRoute } from "../app/routes";
import { StateView } from "./StateView";

/** 找不到頁面或資源時的共用畫面：吉祥物插圖＋說明＋回首頁（可再加一個相關清單的入口）。 */
export function NotFoundView({
  title = "找不到這個頁面",
  description = "網址可能打錯了，或這個頁面已經移除。",
  back = { label: "我的教材", route: { name: "materials" } },
}: {
  title?: string;
  description?: string;
  /** 回到相關清單，例如「回到我的教材」。 */
  back?: { label: string; route: AppRoute };
}) {
  return (
    <StateView
      tone="empty"
      image="/assets/studydy/failure-confused.png"
      title={title}
      description={description}
      action={
        <>
          <button className="primary-button" type="button" onClick={() => writeRoute({ name: "home" })}>
            回到首頁
          </button>
          {back && (
            <button className="secondary-button" type="button" onClick={() => writeRoute(back.route)}>
              {back.label}
            </button>
          )}
        </>
      }
    />
  );
}

export const materialNotFound = {
  title: "找不到這份教材",
  description: "這份教材可能已被刪除，或不屬於目前登入的帳號。",
  back: { label: "回到我的教材", route: { name: "materials" } as AppRoute },
};
