import type { AnchorHTMLAttributes } from "react";
import { routePath, writeRoute, type AppRoute } from "../app/routes";

// 站內導覽用真正的連結：可在新分頁開啟、複製網址；一般點擊仍走單頁路由。
export function RouteLink({ route, onClick, ...props }: { route: AppRoute } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">) {
  return <a {...props} href={routePath(route)} onClick={(event) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    writeRoute(route);
  }} />;
}
