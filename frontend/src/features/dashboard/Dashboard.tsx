import { useMemo, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { LearningQuest } from "../../api/contracts";
import { useLearningActivity } from "../../app/LearningProgress";
import { DailyQuests } from "./LearningCards";
import { useProfile } from "../../app/HeaderTools";
import { writeRoute, type AppRoute } from "../../app/routes";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { Icon } from "../../ui/Icon";
import { Bone } from "../../ui/Skeleton";
import { IslandMap } from "./IslandMap";
import { headSession, islandState, openMaterial, statusText, useMaterialProgress } from "./islands";
import "./styles.css";

export function Dashboard({ apiClient }: { apiClient: StudydyApiClient }) {
  const [retry, setRetry] = useState(0);
  const { materials, progress, error } = useMaterialProgress(apiClient, retry);
  // 首頁一律重新計算，頁首共用同一份結果。
  const activity = useLearningActivity(apiClient, { fresh: true });
  const name = useProfile(apiClient)?.display_name;

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
  const openQuest = (quest: LearningQuest) => {
    if (quest.kind === "review_due_cards" || quest.kind === "review_new_cards") {
      writeRoute({ name: "concept-cards" });
      return;
    }
    if (quest.kind === "finish_podcast_episode" && quest.podcast_id && quest.material_id) {
      writeRoute({ name: "podcast", podcastId: quest.podcast_id, materialId: quest.material_id });
      return;
    }
    const material = materials?.find((item) => item.material_id === quest.material_id);
    const link = material?.study_sessions.find((item) => item.study_session_id === quest.study_session_id);
    if (material && link) writeRoute({ name: "study-session", materialId: material.material_id, runId: link.run_id, structureRevision: link.knowledge_structure_revision, studySessionId: link.study_session_id });
    else if (material) openMaterial(material);
    else writeRoute({ name: "materials" });
  };
  const pages = (materials ?? []).reduce((sum, item) => sum + (item.latest_attempt?.total_pages ?? 0), 0);
  const now = new Date();
  const hour = now.getHours();
  const greeting = name ? `${hour >= 5 && hour < 11 ? "早安" : hour >= 11 && hour < 18 ? "午安" : "晚安"}，${name}！` : "歡迎回來！";

  return (
    <section className="dashboard">
      <header className="dashboard-greeting">
        <h1>{greeting}今天想探索哪座島？</h1>
        <div className="dashboard-actions">
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "topics" })}><Icon name="sparkle" size={18} />從主題建立教材</button>
          <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}><Icon name="upload" size={18} />上傳教材</button>
        </div>
      </header>
      <div className="dashboard-content">
        <section className="archipelago surface" aria-labelledby="archipelago-title">
          <header className="archipelago-head">
            <div>
              <h2 id="archipelago-title">我的學習群島</h2>
              {materials && <span>{materials.length} 份教材{pages > 0 && ` · ${pages} 頁`}</span>}
            </div>
            <button className="text-button" type="button" onClick={() => writeRoute({ name: "materials" })}>看全部教材<Icon name="chevron-right" size={16} /></button>
          </header>
          {error ? (
            <div className="dashboard-error" role="alert">
              <p>{error}</p>
              <button className="secondary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button>
            </div>
          ) : <IslandMap materials={materials} progress={progress} focusId={focus?.material_id} />}
          {materials && <footer className="archipelago-foot">
            <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
            <p className="guide-bubble">
              {focus && focusSession?.status === "completed" ? <>
                <small>已完成 · {focus.display_name}</small>這份教材已完成學習，可以回顧成果或挑選概念複習。
              </> : focus && focusSession ? <>
                <small>上次停在 · {focus.display_name}</small>
                {focusProgress?.currentLabel ? <>回到「<b>{focusProgress.currentLabel}</b>」，把這個概念練熟吧。</> : "回來接續上次的學習吧。"}
              </> : focus ? <>
                <small>下一步</small>打開「<b>{focus.display_name}</b>」的知識地圖，從第一個概念開始。
              </> : materials.length ? "教材正在整理中，完成後就能開始探索。" : "上傳第一份教材，建立你的第一座島嶼。"}
            </p>
            {continueRoute ? <button className="primary-button" type="button" onClick={() => writeRoute(continueRoute)}>
              {focusSession?.status === "completed" ? "查看學習成果" : "繼續學習"}<Icon name="chevron-right" size={18} />
            </button> : focus ? <button className="primary-button" type="button" onClick={() => openMaterial(focus)}>開啟知識地圖<Icon name="chevron-right" size={18} /></button>
              : !materials.length && <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>上傳教材<Icon name="chevron-right" size={18} /></button>}
          </footer>}
          {/* 手機上地圖較小，另列全部教材方便直接點選。 */}
          {materials && ordered.length > 1 && <ul className="archipelago-list" aria-label="全部教材島嶼">
            {ordered.map((material) => {
              const status = statusText(islandState(material), progress.get(material.material_id));
              return <li key={material.material_id}>
                <button type="button" onClick={() => openMaterial(material)}>
                  <span className="archipelago-list-thumb" aria-hidden="true"><IslandArt palette={islandPalette(material.material_id)} landmarks={false} /></span>
                  <span className="archipelago-list-text"><b>{material.display_name}</b><small>{status}</small></span>
                  <Icon name="chevron-right" size={16} />
                </button>
              </li>;
            })}
          </ul>}
        </section>
        {/* 右欄只留今日任務；等級、徽章與統計在「成就」頁，概念卡複習在概念卡頁。 */}
        <aside className="dashboard-side">
          {activity === undefined && <div className="dashboard-skeleton-card surface" aria-hidden="true"><Bone className="is-line is-title" style={{ width: "35%" }} /><Bone className="is-row" /><Bone className="is-row" /></div>}
          {activity && activity.quests.length > 0 && <DailyQuests quests={activity.quests} onOpen={openQuest} />}
          {activity && <button className="dashboard-achievements surface" type="button" onClick={() => writeRoute({ name: "profile" })}>
            <span className="stat-icon is-amber"><Icon name="sparkle" size={18} /></span>
            <span><b>第 {activity.xp.level} 級 · {activity.xp.total} XP</b><small>查看等級、徽章與本週學習節奏</small></span>
            <Icon name="chevron-right" size={16} />
          </button>}
        </aside>
      </div>
    </section>
  );
}
