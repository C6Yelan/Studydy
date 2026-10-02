import type { StudydyApiClient } from "../../api/client";
import type { AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import { StudySessionPage } from "../study-session/StudySessionPage";
import { Dashboard } from "../dashboard/Dashboard";
import { MaterialLibrary } from "./MaterialLibrary";
import { RunView } from "./RunView";
import { SourceView } from "./SourceView";
import { UploadView } from "./UploadView";
import { CardSetLibrary } from "../concept-cards/CardSetLibrary";
import { CreateCardSet } from "../concept-cards/CreateCardSet";
import { CardSetPage } from "../concept-cards/CardSetPage";
import "../concept-cards/styles.css";
import "./styles.css";

export function MaterialFlow({
  apiClient,
  route,
}: {
  apiClient: StudydyApiClient;
  route: AppRoute;
}) {
  if (route.name === "home") return <Dashboard apiClient={apiClient} />;
  if (route.name === "concept-cards") return <CardSetLibrary apiClient={apiClient} />;
  if (route.name === "card-set-create") return <CreateCardSet key={`${route.materialId}/${route.structureRevision}`} apiClient={apiClient} route={route} />;
  if (route.name === "card-set-edit") return <CreateCardSet key={`edit/${route.cardSetId}`} apiClient={apiClient} route={route} />;
  if (route.name === "card-set") return <CardSetPage key={route.cardSetId} apiClient={apiClient} cardSetId={route.cardSetId} />;
  if (route.name === "materials") return <MaterialLibrary key="library" apiClient={apiClient} />;
  if (route.name === "upload") return <UploadView apiClient={apiClient} />;
  if (route.name === "material-sources")
    return (
      <SourceView key={route.materialId} apiClient={apiClient} materialId={route.materialId} />
    );
  if (route.name === "material-run")
    return <RunView key={route.runId} apiClient={apiClient} route={route} />;
  if (route.name === "knowledge-map")
    return (
      <KnowledgeMap
        key={`${route.materialId}/${route.runId}/${route.structureRevision}`}
        apiClient={apiClient}
        route={route}
      />
    );
  return (
    <StudySessionPage
      key={`${route.materialId}/${route.runId}/${route.structureRevision}/${route.studySessionId}`}
      apiClient={apiClient}
      route={route}
    />
  );
}
