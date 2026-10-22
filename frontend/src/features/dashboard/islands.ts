import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem, StudySessionLink } from "../../api/contracts";
import { writeRoute } from "../../app/routes";

// 首頁群島與成就頁共用：教材狀態判斷、開啟方式與概念進度讀取。

export type IslandState = "building" | "failed" | "ready" | "learning" | "completed";
export type IslandProgress = { completed: number; total: number; currentLabel: string | null };

export function headStructure(material: MaterialLibraryItem) {
  return material.available_structures.find((item) => item.knowledge_structure_revision === material.head_revision)
    ?? material.available_structures[0];
}

export function headSession(material: MaterialLibraryItem): StudySessionLink | undefined {
  const structure = headStructure(material);
  return structure && material.study_sessions.find((item) =>
    item.run_id === structure.run_id && item.knowledge_structure_revision === structure.knowledge_structure_revision);
}

export function islandState(material: MaterialLibraryItem): IslandState {
  if (!headStructure(material))
    return ["pending", "running"].includes(material.latest_attempt?.status ?? "") || ["pending", "running"].includes(material.source?.status ?? "") ? "building" : "failed";
  const session = headSession(material);
  if (!session) return "ready";
  return session.status === "completed" ? "completed" : "learning";
}

export function openMaterial(material: MaterialLibraryItem) {
  const structure = headStructure(material);
  if (structure) writeRoute({ name: "knowledge-map", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision });
  else if (material.latest_attempt) writeRoute({ name: "material-run", materialId: material.material_id, runId: material.latest_attempt.run_id });
  else writeRoute({ name: "materials" });
}

export function statusText(state: IslandState, progress: IslandProgress | undefined) {
  if (state === "building") return "知識地圖建立中";
  if (state === "failed") return "建立未完成，需要處理";
  if (state === "completed") return "已完成學習";
  if (state === "ready") return progress ? `尚未開始 · ${progress.total} 個概念` : "尚未開始";
  if (progress?.currentLabel) return `學習中：${progress.currentLabel}`;
  return progress ? `已完成 ${progress.completed} / ${progress.total} 個概念` : "學習中";
}

// 讀取教材清單與每份教材的概念進度摘要；個別摘要失敗只少顯示數字。
export function useMaterialProgress(apiClient: StudydyApiClient, retry = 0) {
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [progress, setProgress] = useState<Map<string, IslandProgress>>(new Map());
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then(
      (library) => { if (!cancelled) setMaterials(library.materials); },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, retry]);
  useEffect(() => {
    let cancelled = false;
    for (const material of materials ?? []) {
      const structure = headStructure(material);
      if (!structure) continue;
      const session = headSession(material);
      // 只讀摘要，不下載完整 outline 與逐概念狀態（千頁教材可達數 MB）。
      void apiClient.readProgressSummary({ materialId: material.material_id, structureRevision: structure.knowledge_structure_revision }, session?.study_session_id).then((summary) => {
        if (cancelled) return;
        const value: IslandProgress = { total: summary.concept_count, completed: summary.completed_count, currentLabel: summary.current_concept_label };
        setProgress((previous) => new Map(previous).set(material.material_id, value));
      }, () => undefined);
    }
    return () => { cancelled = true; };
  }, [apiClient, materials]);
  return { materials, progress, error };
}
