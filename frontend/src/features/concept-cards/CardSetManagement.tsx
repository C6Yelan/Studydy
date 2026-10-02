import { useRef } from "react";
import type { CardSetSummary } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { useDismissibleMenu } from "../material-flow/useDismissibleMenu";

export function CardSetManagement({ item, onDelete }: {
  item: CardSetSummary;
  onDelete: (opener: HTMLElement) => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const opener = useRef<HTMLElement>(null);
  useDismissibleMenu(menu, opener);
  return <>
    <details ref={menu} className="material-management-menu" name="card-set-management">
      <summary ref={opener} role="button" tabIndex={0} aria-label={`管理卡組「${item.name}」`}>⋯</summary>
      <div>
        <button type="button" onClick={() => writeRoute({ name: "card-set-edit", cardSetId: item.card_set_id })}>管理卡組</button>
        <button className="material-delete-action" type="button" aria-label={`刪除卡組「${item.name}」`} onClick={() => {
          if (menu.current) menu.current.open = false;
          if (opener.current) onDelete(opener.current);
        }}>刪除卡組</button>
      </div>
    </details>
    <h2 title={item.name}>{item.name}</h2>
  </>;
}
