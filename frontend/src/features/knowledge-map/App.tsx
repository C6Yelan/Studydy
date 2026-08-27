import { useEffect, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { KnowledgeMapView } from "../../api/contracts";
import type { AppRoute } from "../../app/routes";
import { StateView } from "../../ui/StateView";
import { KnowledgeMapWorkspace } from "./KnowledgeMapWorkspace";
import "./styles.css";

export default function KnowledgeMap({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: Extract<AppRoute, { name: "knowledge-map" }>;
}) {
  const [view, setView] = useState<KnowledgeMapView | null>(null);
  const [sourceArtifactId, setSourceArtifactId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      apiClient.getKnowledgeMap({
        materialId: route.materialId,
        runId: route.runId,
        mapRevision: route.mapRevision,
      }),
      apiClient.getMaterialRun(route.runId),
    ]).then(
      ([map, run]) => {
        if (cancelled) return;
        if (run.material_id !== route.materialId) throw new Error("RUN_MATERIAL_MISMATCH");
        setView(map);
        setSourceArtifactId(run.source_artifact_id);
        setMessage(null);
      },
      (error) => {
        if (!cancelled) setMessage(errorMessage(error));
      },
    );
    return () => { cancelled = true; };
  }, [apiClient, route.mapRevision, route.materialId, route.runId]);

  if (message) return (
    <StateView
      description={message}
      image="/assets/studydy/failure-confused.png"
      title="無法讀取知識地圖"
      tone="failure"
    />
  );
  if (!view || !sourceArtifactId) return (
    <StateView
      description="正在載入已發布的概念、關係與教材順序。"
      live
      title="正在讀取知識地圖"
      tone="loading"
    />
  );
  return <KnowledgeMapWorkspace apiClient={apiClient} sourceArtifactId={sourceArtifactId} view={view} />;
}
