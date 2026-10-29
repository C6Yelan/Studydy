import { useEffect, useRef, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { MaterialNotesView, NoteKind } from "../../api/contracts";
import { writeRoute } from "../../app/routes";

/** 知識地圖帶到筆記頁的目標：哪一種筆記的哪一段。 */
export type NoteTarget = { kind: NoteKind; paragraphId: string };

/** 筆記中引用這個概念的第一段；先找重點整理，再找完整筆記。沒有就回傳 null。 */
export function useConceptNoteTarget(api: StudydyApiClient, materialId: string, revision: string, conceptId: string) {
  const [target, setTarget] = useState<NoteTarget | null>(null);
  useEffect(() => {
    let cancelled = false;
    setTarget(null);
    void (async () => {
      for (const kind of ["summary", "full"] as const) {
        const view = await api.getNotes(materialId, revision, kind).catch(() => null);
        const paragraph = view?.paragraphs.find(p => p.concepts.some(c => c.concept_id === conceptId));
        if (paragraph) { if (!cancelled) setTarget({ kind, paragraphId: paragraph.id }); return; }
      }
    })();
    return () => { cancelled = true; };
  }, [api, materialId, revision, conceptId]);
  return target;
}

export function openNoteTarget(materialId: string, target: NoteTarget) {
  writeRoute({ name: "material-content", materialId, kind: "notes" }, false, { noteTarget: target });
}

export function pendingNoteTarget(): NoteTarget | null {
  const target = window.history.state?.noteTarget;
  return target && (target.kind === "summary" || target.kind === "full") && typeof target.paragraphId === "string" ? target : null;
}

/** 筆記載入後捲到目標段落並短暫標示；只執行一次。 */
export function useLocateParagraph(view: MaterialNotesView | null, target: NoteTarget | null) {
  const done = useRef(false);
  useEffect(() => {
    if (!view || !target || done.current || view.kind !== target.kind) return;
    const section = document.getElementById(`note-section-${target.paragraphId}`);
    if (!section) return;
    done.current = true;
    section.scrollIntoView({ block: "start" });
    section.focus({ preventScroll: true });
    section.classList.add("note-paragraph-located");
    window.history.replaceState({ ...window.history.state, noteTarget: undefined }, "");
    setTimeout(() => section.classList.remove("note-paragraph-located"), 4000);
  }, [view, target]);
}
