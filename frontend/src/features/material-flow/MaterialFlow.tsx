import type { StudydyApiClient } from "../../api/client";
import type { AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import { RunView } from "./RunView";
import { UploadView } from "./UploadView";
import "./styles.css";

export function MaterialFlow({ apiClient, route }: {
  apiClient: StudydyApiClient;
  route: AppRoute;
}) {
  if (route.name === "home") return <UploadView apiClient={apiClient} />;
  if (route.name === "material-run") return <RunView apiClient={apiClient} route={route} />;
  return <KnowledgeMap apiClient={apiClient} route={route} />;
}
