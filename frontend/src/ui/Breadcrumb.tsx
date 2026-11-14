import { writeRoute } from "../app/routes";
import "./styles.css";

/** 教材內子頁頁頂的麵包屑：上一層是可按的按鈕（回到所屬清單），後面接目前這一頁的名稱。 */
export function Breadcrumb({ parent, current }: { parent: { label: string; ariaLabel: string; onClick: () => void }; current: string }) {
  return <nav className="crumb" aria-label="目前位置">
    <button className="crumb-parent" type="button" aria-label={parent.ariaLabel} onClick={parent.onClick}>{parent.label}</button>
    <span className="crumb-current" aria-current="page" title={current}>› {current}</span>
  </nav>;
}

/** 卡組、Podcast 的子頁：上一層是所屬清單。從全站清單進來（fromCollection）或不在教材內時回全站清單，否則回這份教材的清單。 */
export function ListBreadcrumb({ kind, materialId, current }: { kind: "concept-cards" | "podcasts"; materialId?: string; current: string }) {
  const inMaterial = materialId && !window.history.state?.fromCollection ? materialId : null;
  const label = kind === "concept-cards" ? "翻卡片" : inMaterial ? "聽 Podcast" : "Podcast";
  return <Breadcrumb current={current} parent={{ label, ariaLabel: `回到${label === "Podcast" ? " " : ""}${label}`, onClick: () => writeRoute(inMaterial ? { name: "material-content", materialId: inMaterial, kind } : { name: kind }) }} />;
}
