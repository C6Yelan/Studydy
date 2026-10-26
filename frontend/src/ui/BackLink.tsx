import { writeRoute, type AppRoute } from "../app/routes";
import { Icon } from "./Icon";

/** 全站統一的返回控制：白色膠囊＋左箭頭，放在頁面內容左上方。 */
export function BackLink({ label, route, onClick }: { label: string; route?: AppRoute; onClick?: () => void }) {
  return <button className="back-link" type="button" title={label} onClick={() => (onClick ? onClick() : route && writeRoute(route))}>
    <Icon name="arrow-left" size={17} /><span>{label}</span>
  </button>;
}
