import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeConceptsView } from "../../api/contracts";

export function useConceptContext(api: StudydyApiClient, materialId: string, revision: string, ids: string[], neighbors = false) {
  const selection = ids.join(",");
  const key = `${materialId}/${revision}/${neighbors}/${selection}`;
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<{ key: string; context: KnowledgeConceptsView | null; error: string | null }>({ key: "", context: null, error: null });
  useEffect(() => {
    let cancelled = false;
    setState({ key, context: null, error: null });
    if (selection) void api.getConceptContext({ materialId, structureRevision: revision }, selection.split(","), neighbors)
      .then(context => { if (!cancelled) setState({ key, context, error: null }); },
        error => { if (!cancelled) setState({ key, context: null, error: errorMessage(error) }); });
    return () => { cancelled = true; };
  }, [api, materialId, revision, selection, neighbors, reload]);
  // 換概念的第一個 render 不可顯示上一個概念的來源。
  return { context: state.key === key ? state.context : null, error: state.key === key ? state.error : null,
    retry: () => setReload(value => value + 1) };
}
