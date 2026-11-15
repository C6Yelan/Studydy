import { useStudyStart } from "../study-session/useStudyStart";
import { useEffect, useMemo, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { LearningGoalSummary, LearningGoalsView, LearningQuest } from "../../api/contracts";
import { errorMessage } from "../../api/client";
import { goalSummaryText } from "../learning-goals/goal-summary";
import { useLearningActivity } from "../../app/LearningProgress";
import { DailyQuests, dailyTaskCount } from "./LearningCards";
import { useProfile } from "../../app/useProfile";
import { writeRoute, type AppRoute } from "../../app/routes";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { Icon } from "../../ui/Icon";
import { Bone } from "../../ui/Skeleton";
import { IslandMap } from "./IslandMap";
import { QuickAccess, type QuickItem } from "./QuickAccess";
import { headSession, islandState, openMaterial, statusText, useMaterialProgress } from "./islands";
import "./styles.css";

export function Dashboard({ apiClient }: { apiClient: StudydyApiClient }) {
  const launch = useStudyStart(apiClient);
  const [retry, setRetry] = useState(0);
  const { materials, progress, error } = useMaterialProgress(apiClient, retry);
  // 首頁一律重新計算，外殼的慶祝動畫共用同一份結果。
  const activity = useLearningActivity(apiClient, { fresh: true });
  const name = useProfile(apiClient)?.display_name;
  const [goals, setGoals] = useState<LearningGoalsView | null>(null);
  const [goalsError, setGoalsError] = useState<string | null>(null);
  const [goalsReload, setGoalsReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setGoals(null); setGoalsError(null);
    const read = () => void apiClient.getLearningGoals().then(value => {
      if (!cancelled) { setGoals(value); setGoalsError(null); }
    }, failure => { if (!cancelled) { setGoals(null); setGoalsError(errorMessage(failure)); } });
    read();
    window.addEventListener("focus", read);
    return () => { cancelled = true; window.removeEventListener("focus", read); };
  }, [apiClient, goalsReload]);
  const goalsByMaterial = useMemo(() => new Map(goals?.goals.map(goal => [goal.material_id, goal]) ?? []), [goals]);
  const openGoal = (goal: LearningGoalSummary) => writeRoute({ name: "material-progress", materialId: goal.material_id,
    runId: goal.run_id, structureRevision: goal.knowledge_structure_revision });

  // 依最近學習排序：有學習紀錄的教材在前，其餘依建立時間。
  const ordered = useMemo(() => [...(materials ?? [])].sort((a, b) => {
    const at = headSession(a)?.started_at ?? "", bt = headSession(b)?.started_at ?? "";
    if (at !== bt) return bt.localeCompare(at);
    return b.created_at.localeCompare(a.created_at);
  }), [materials]);
  // 優先接續學習中的教材，其次查看已完成成果，最後才是尚未開始的地圖。
  const focus = (["learning", "completed", "ready"] as const).map((state) => ordered.find((item) => islandState(item) === state)).find(Boolean);

  const focusSession = focus && headSession(focus);
  const focusProgress = focus && progress.get(focus.material_id);
  const continueRoute: AppRoute | null = focus && focusSession
    ? { name: "study-session", materialId: focus.material_id, runId: focusSession.run_id, structureRevision: focusSession.knowledge_structure_revision, studySessionId: focusSession.study_session_id }
    : null;
  // 「繼續上次的冒險」：接續學習中的教材，其次查看已完成成果、開啟尚未開始的地圖。
  const island = focus && { materialId: focus.material_id, completed: focusProgress?.completed ?? 0, total: focusProgress?.total ?? 0 };
  const startFocus = () => void launch.start({ materialId: focus!.material_id, runId: focusSession!.run_id, structureRevision: focusSession!.knowledge_structure_revision });
  const next: QuickItem = focus && continueRoute && focusSession?.status === "completed"
    ? { icon: "check", kicker: "查看學習成果", title: focus.display_name, meta: "這份教材已完成學習", cta: "查看", island, onOpen: () => writeRoute(continueRoute) }
    : focus && continueRoute ? { icon: "learning", kicker: "繼續學習", title: focusProgress?.currentLabel ?? focus.display_name,
      meta: focusProgress?.currentLabel ? `上次停在 · ${focus.display_name}` : "回來接續上次的學習", cta: "繼續", island, onOpen: startFocus }
    : focus ? { icon: "map", kicker: "開啟知識地圖", title: focus.display_name, meta: "從第一個概念開始", cta: "開啟地圖", island, onOpen: () => openMaterial(focus) }
    : materials?.length ? { icon: "process", kicker: "教材整理中", title: "完成後就能開始探索", meta: "整理進度可在我的群島查看", cta: "" }
    : { icon: "upload", kicker: "上傳教材", title: "建立你的第一座島嶼", meta: "上傳 PDF 或從主題建立教材", cta: "上傳教材", onOpen: () => writeRoute({ name: "upload" }) };
  const openQuest = (quest: LearningQuest) => {
    if (quest.kind === "mark_cards") {
      writeRoute({ name: "concept-cards" });
      return;
    }
    if (quest.kind === "finish_podcast_episode" && quest.podcast_id && quest.material_id) {
      writeRoute({ name: "podcast", podcastId: quest.podcast_id, materialId: quest.material_id });
      return;
    }
    const material = materials?.find((item) => item.material_id === quest.material_id);
    const link = material?.study_sessions.find((item) => item.study_session_id === quest.study_session_id);
    if (material && link) void launch.start({ materialId: material.material_id, runId: link.run_id, structureRevision: link.knowledge_structure_revision });
    else if (material) openMaterial(material);
    else writeRoute({ name: "materials" });
  };
  const now = new Date();
  const hour = now.getHours();
  const tasks = dailyTaskCount(activity?.quests ?? [], goals?.goals ?? []);
  const hello = name ? `${hour >= 5 && hour < 11 ? "早安" : hour >= 11 && hour < 18 ? "午安" : "晚安"}，${name}！` : "歡迎回來！";
  // 還有小事沒完成時，機器人直接說還差幾件；讀不到或沒有小事就用原本的問候。
  const nudge = activity && tasks.total > 0 && tasks.done < tasks.total ? `再完成 ${tasks.total - tasks.done} 件小事，今天就達標了。` : "今天想探索哪座島？";
  // 群島地圖要等目標和教材都到了才決定開場置中；目標讀取失敗也算讀完。
  const goalsSettled = goals !== null || goalsError !== null;

  return (
    <section className="dashboard">

      {launch.error && <p className="form-error" role="alert">{launch.error}</p>}
      <header className="dashboard-greeting">
        <img className="dashboard-bot" src="/assets/studydy/brand-guide.png" alt="Studydy 機器人" />
        <div className="dashboard-bubble">
          <h1>{hello}{nudge}</h1>
          {/* 等級和連續天數只在首頁出現（全站頁首拿掉後）；讀不到學習活動就不顯示。 */}
          {activity && <p className="dashboard-standing">{activity.streak_days ? `連續 ${activity.streak_days} 天` : "尚未連續"} · 第 {activity.xp.level} 級</p>}
        </div>
      </header>
      <div className="dashboard-content">
        <section className="archipelago surface" aria-labelledby="archipelago-title">
          <header className="archipelago-head">
            <div>
              <h2 id="archipelago-title">我的學習群島</h2>
              {materials && <span>{materials.length} 份教材</span>}
            </div>
            <div className="archipelago-links">
              <button className="text-button" type="button" onClick={() => writeRoute({ name: "topics" })}>從主題建立教材</button>
              <button className="text-button" type="button" onClick={() => writeRoute({ name: "materials" })}>看全部教材<Icon name="chevron-right" size={16} /></button>
            </div>
          </header>
          {error ? (
            <div className="dashboard-error" role="alert">
              <p>{error}</p>
              <button className="secondary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button>
            </div>
          ) : <IslandMap materials={materials} progress={progress} goals={goalsByMaterial} goalsReady={goalsSettled} />}
        </section>
        {/* 右欄：繼續上次的冒險與今天的小事（放在右欄是為了讓地圖在一般筆電視窗內仍不必捲動）；徽章與統計在「成就」頁。 */}
        <aside className="dashboard-side">
          {materials && <QuickAccess apiClient={apiClient} next={next} />}
          {activity === undefined && <div className="dashboard-skeleton-card surface" aria-hidden="true"><Bone className="is-line is-title" style={{ width: "35%" }} /><Bone className="is-row" /><Bone className="is-row" /></div>}
          {((activity?.quests.length ?? 0) > 0 || (goals?.goals.length ?? 0) > 0) && <DailyQuests quests={activity?.quests ?? []} goals={goals?.goals ?? []} onOpen={openQuest} onOpenGoal={openGoal}
            onContinue={focus && continueRoute && focusSession?.status !== "completed" ? startFocus : undefined} />}
          {goalsError && <p role="alert" className="dashboard-goal-error">學習目標暫時無法讀取。{goalsError}<button className="text-button" onClick={() => setGoalsReload(value => value + 1)}>重新讀取目標</button></p>}
        </aside>
        {/* 手機上地圖較小，在右欄之後另列全部教材方便直接點選。 */}
        {materials && ordered.length > 1 && <ul className="archipelago-list" aria-label="全部教材島嶼">
          {ordered.map((material) => {
            const status = statusText(islandState(material), progress.get(material.material_id));
            const goal = goalsByMaterial.get(material.material_id);
            return <li key={material.material_id}>
              <button type="button" onClick={() => openMaterial(material)}>
                <span className="archipelago-list-thumb" aria-hidden="true"><IslandArt palette={islandPalette(material.material_id)} landmarks={false} /></span>
                <span className="archipelago-list-text"><b>{material.display_name}</b><small>{status}</small>{goal && <small className="island-goal-summary">{goalSummaryText(goal)}</small>}</span>
                <Icon name="chevron-right" size={16} />
              </button>
            </li>;
          })}
        </ul>}
      </div>
    </section>
  );
}
