import { useRef } from "react";
import type { ResearchDraft } from "../material-tools/research";
import { TopicPage } from "../topics/TopicPage";
import type { StudydyApiClient } from "../../api/client";
import type { AppRoute } from "../../app/routes";
import KnowledgeMap from "../knowledge-map/App";
import { StudySessionPage } from "../study-session/StudySessionPage";
import { Dashboard } from "../dashboard/Dashboard";
import { MaterialContent } from "./MaterialContent";
import { MaterialLibrary } from "./MaterialLibrary";
import { RunView } from "./RunView";
import { SourceView } from "./SourceView";
import { UploadView } from "./UploadView";
import { CardSetLibrary } from "../concept-cards/CardSetLibrary";
import { CreateCardSet } from "../concept-cards/CreateCardSet";
import { CardSetPage } from "../concept-cards/CardSetPage";
import { PodcastLibrary } from "../podcasts/PodcastLibrary";
import { CreateStudyContent } from "./CreateStudyContent";
import { PodcastPage } from "../podcasts/PodcastPage";
import "../podcasts/styles.css";
import "../concept-cards/styles.css";
import "./styles.css";

function MaterialFlowBody({
  apiClient,
  route,
  learnerId,
  researchDraft,
  onResearchDraftChange,
}: {
  apiClient: StudydyApiClient;
  route: AppRoute;
  learnerId: string;
  researchDraft?: ResearchDraft;
  onResearchDraftChange: (draft: ResearchDraft) => void;
}) {
  if (route.name === "topics" || route.name === "topic") return <TopicPage key={route.name === "topic" ? route.topicId : "new"} api={apiClient} topicId={route.name === "topic" ? route.topicId : undefined}/>;
  if ("materialId" in route && route.materialId && ["podcast", "podcast-create", "card-set", "card-set-edit", "card-set-create"].includes(route.name)) {
    const kind = route.name.startsWith("podcast") ? "podcasts" : "concept-cards";
    const content = route.name === "podcast" ? <PodcastPage key={route.podcastId} apiClient={apiClient} podcastId={route.podcastId} materialId={route.materialId} learnerId={learnerId} />
      : route.name === "card-set" ? <CardSetPage key={route.cardSetId} apiClient={apiClient} cardSetId={route.cardSetId} materialId={route.materialId} />
      : route.name === "card-set-edit" ? <CreateCardSet key={`edit/${route.cardSetId}`} apiClient={apiClient} route={route} />
      : route.name === "podcast-create" || route.name === "card-set-create" ? <CreateStudyContent apiClient={apiClient} route={route} /> : null;
    return <MaterialContent key={route.materialId} apiClient={apiClient} learnerId={learnerId} materialId={route.materialId} kind={kind}>{content}</MaterialContent>;
  }
  if (route.name === "research") return <MaterialContent key={route.materialId} apiClient={apiClient} learnerId={learnerId} materialId={route.materialId} kind="research" researchId={route.researchId} researchDraft={researchDraft} onResearchDraftChange={onResearchDraftChange} />;
  if (route.name === "material-content") return <MaterialContent key={route.materialId} apiClient={apiClient} learnerId={learnerId} materialId={route.materialId} kind={route.kind} researchDraft={researchDraft} onResearchDraftChange={onResearchDraftChange} />;
  if (route.name === "podcasts") return <PodcastLibrary apiClient={apiClient} learnerId={learnerId} />;
  if (route.name === "podcast-new" || route.name === "podcast-create" || route.name === "card-set-new" || route.name === "card-set-create") return <CreateStudyContent apiClient={apiClient} route={route} />;
  if (route.name === "podcast") return <PodcastPage key={route.podcastId} apiClient={apiClient} podcastId={route.podcastId} materialId={route.materialId} learnerId={learnerId} />;
  if (route.name === "home") return <Dashboard apiClient={apiClient} />;
  if (route.name === "concept-cards") return <CardSetLibrary apiClient={apiClient} />;

  if (route.name === "card-set-edit") return <CreateCardSet key={`edit/${route.cardSetId}`} apiClient={apiClient} route={route} />;
  if (route.name === "card-set") return <CardSetPage key={route.cardSetId} apiClient={apiClient} cardSetId={route.cardSetId} materialId={route.materialId} />;
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

export function MaterialFlow(props: {apiClient:StudydyApiClient;route:AppRoute;learnerId:string}) {
 const materialId = "materialId" in props.route ? props.route.materialId : undefined;
 const researchDrafts = useRef(new Map<string, ResearchDraft>());
 const researchDraft = materialId ? researchDrafts.current.get(materialId) : undefined;
 const onResearchDraftChange = (draft: ResearchDraft) => { if (materialId) researchDrafts.current.set(materialId, draft); };
 return <MaterialFlowBody {...props} researchDraft={researchDraft} onResearchDraftChange={onResearchDraftChange}/>;
}
