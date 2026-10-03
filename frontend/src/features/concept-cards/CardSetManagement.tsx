import { useEffect, useRef, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetSummary } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { useDismissibleMenu } from "../material-flow/useDismissibleMenu";
import { InlineNameEditor } from "../material-flow/InlineNameEditor";

export function CardSetManagement({ item, apiClient, onChanged, onDelete, materialId }: {
  materialId?: string;
  item: CardSetSummary; apiClient: StudydyApiClient; onChanged: () => void;
  onDelete: (opener: HTMLElement) => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null), opener = useRef<HTMLElement>(null);
  const active = useRef(false), saving = useRef(false), interacted = useRef(false);
  const [renaming, setRenaming] = useState(false), [busy, setBusy] = useState(false);
  const [name, setName] = useState(""), [error, setError] = useState<string | null>(null);
  useDismissibleMenu(menu, opener);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { if (interacted.current && !renaming) opener.current?.focus({ preventScroll: true }); }, [renaming]);
  const rename = async () => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError(null);
    try {
      await apiClient.updateCardSet(item.card_set_id, { schema: "card-set-update/v1", name: name.trim(), expected_version: item.version });
      if (active.current) { setRenaming(false); onChanged(); }
    } catch (e) { if (active.current) { setError(errorMessage(e)); onChanged(); } }
    finally { saving.current = false; if (active.current) setBusy(false); }
  };
  return <>
    <details ref={menu} className="material-management-menu" name="card-set-management" hidden={renaming}>
      <summary ref={opener} role="button" tabIndex={0} aria-label={`管理卡組「${item.name}」`}>⋯</summary>
      <div>
        <button type="button" onClick={() => writeRoute({ name: "card-set-edit", cardSetId: item.card_set_id, ...(materialId ? { materialId } : {}) })}>管理卡組</button>
        <button type="button" onClick={() => { if (menu.current) menu.current.open = false; interacted.current = true; setName(item.name); setError(null); setRenaming(true); }}>重新命名</button>
        <button className="material-delete-action" type="button" aria-label={`刪除卡組「${item.name}」`} onClick={() => {
          if (menu.current) menu.current.open = false;
          if (opener.current) onDelete(opener.current);
        }}>刪除卡組</button>
      </div>
    </details>
    {renaming ? <InlineNameEditor label="卡組名稱" name={name} busy={busy} error={error} onChange={setName} onSave={() => void rename()} onCancel={() => setRenaming(false)} />
      : <h2 title={item.name}>{item.name}</h2>}
  </>;
}
