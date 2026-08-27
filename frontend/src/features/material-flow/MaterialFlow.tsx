import type { StudydyApiClient } from "../../api/client";
import type { AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import { StudySessionPage } from "../study-session/StudySessionPage";
import { RunView } from "./RunView";
import { UploadView } from "./UploadView";
import "./styles.css";

export function MaterialFlow({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: AppRoute;
}) {
  if (route.name === "home") return <UploadView apiClient={apiClient} />;
  if (route.name === "material-run") return <RunView apiClient={apiClient} route={route} />;
  if (route.name === "knowledge-map") return <KnowledgeMap apiClient={apiClient} route={route} />;
  return <StudySessionPage apiClient={apiClient} route={route} />;
}
