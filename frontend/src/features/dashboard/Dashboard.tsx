import { useEffect, useMemo, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetSummary, LearningActivityView, MaterialLibraryItem, PodcastSummary, StudySessionLink } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { ProgressRing } from "../../ui/art/ProgressRing";
import { Icon } from "../../ui/Icon";
import "./styles.css";

// 群島上的島位（百分比）；教材多於島位時其餘改從列表檢視進入。
const slots = [
  { x: 25, y: 56, w: 28 }, { x: 62, y: 38, w: 30 }, { x: 82, y: 76, w: 26 },
  { x: 88, y: 30, w: 19 }, { x: 13, y: 24, w: 18 }, { x: 37, y: 17, w: 17 },
];

type IslandState = "building" | "failed" | "ready" | "learning" | "completed";
type Progress = { completed: number; total: number; currentLabel: string | null };

function headStructure(material: MaterialLibraryItem) {
  return material.available_structures.find((item) => item.knowledge_structure_revision === material.head_revision)
    ?? material.available_structures[0];
}

function headSession(material: MaterialLibraryItem): StudySessionLink | undefined {
  const structure = headStructure(material);
  return structure && material.study_sessions.find((item) =>
    item.run_id === structure.run_id && item.knowledge_structure_revision === structure.knowledge_structure_revision);
}

function islandState(material: MaterialLibraryItem): IslandState {
  if (!headStructure(material))
    return ["pending", "running"].includes(material.latest_attempt?.status ?? "") || ["pending", "running"].includes(material.source?.status ?? "") ? "building" : "failed";
  const session = headSession(material);
  if (!session) return "ready";
  return session.status === "completed" ? "completed" : "learning";
}

function openMaterial(material: MaterialLibraryItem) {
  const structure = headStructure(material);
  if (structure) writeRoute({ name: "knowledge-map", materialId: material.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision });
  else if (material.latest_attempt) writeRoute({ name: "material-run", materialId: material.material_id, runId: material.latest_attempt.run_id });
  else writeRoute({ name: "materials" });
}

function statusText(state: IslandState, progress: Progress | undefined) {
  if (state === "building") return "知識地圖建立中";
  if (state === "failed") return "建立未完成，需要處理";
  if (state === "completed") return "已完成學習";
  if (state === "ready") return progress ? `尚未開始 · ${progress.total} 個概念` : "尚未開始";
  if (progress?.currentLabel) return `學習中：${progress.currentLabel}`;
  return progress ? `已完成 ${progress.completed} / ${progress.total} 個概念` : "學習中";
}

const weekdays = ["一", "二", "三", "四", "五", "六", "日"];

// 本週節奏只顯示作答紀錄推導的真實數字；概念卡與 Podcast 尚未記錄，不計入。
function WeekRhythm({ activity }: { activity: LearningActivityView }) {
  const answers = activity.week.reduce((sum, day) => sum + day.answers, 0);
  const correct = activity.week.reduce((sum, day) => sum + day.correct, 0);
  const peak = Math.max(1, ...activity.week.map((day) => day.answers));
  return <section className="dashboard-week surface" aria-labelledby="dashboard-week-title">
    <header>
      <h2 id="dashboard-week-title">本週學習節奏</h2>
      <span className={`dashboard-streak${activity.streak_days ? "" : " is-idle"}`}>
        <Icon name="flame" size={16} />{activity.streak_days ? `連續 ${activity.streak_days} 天` : "尚未連續"}
      </span>
    </header>
    <ol className="week-days">
      {activity.week.map((day, index) => {
        const today = day.date === activity.today;
        const future = day.date > activity.today;
        return <li key={day.date} className={[today && "is-today", day.answers > 0 && "is-active", future && "is-future"].filter(Boolean).join(" ")}
          aria-label={`星期${weekdays[index]} ${Number(day.date.slice(5, 7))}/${Number(day.date.slice(8))}${today ? "（今天）" : ""}：${future ? "尚未到" : day.answers ? `作答 ${day.answers} 題，答對 ${day.correct} 題` : "沒有作答"}`}>
          <span className="week-bar" aria-hidden="true"><i style={{ height: `${day.answers ? Math.max(12, (day.answers / peak) * 100) : 0}%` }} /></span>
          <b aria-hidden="true">{day.answers || (future ? "" : "–")}</b>
          <small aria-hidden="true">{weekdays[index]}</small>
        </li>;
      })}
    </ol>
    <p className="week-summary">
      {answers ? <>本週作答 <b>{answers}</b> 題 · 答對率 <b>{Math.round((correct / answers) * 100)}%</b></>
        : activity.streak_days ? "今天作答一題，就能延續連續紀錄。" : "本週還沒有作答，完成一次練習就會出現在這裡。"}
    </p>
    <small className="week-note">依作答紀錄計算，以台灣時間為準。</small>
  </section>;
}

function Sea() {
  const waves = [[8, 12], [30, 8], [44, 30], [90, 46], [6, 90], [52, 92], [36, 96], [96, 6]];
  return <svg className="archipelago-sea" viewBox="0 0 1000 560" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="archipelago-sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8fe0fb" /><stop offset="1" stopColor="#3aa6e2" /></linearGradient></defs>
    <rect width="1000" height="560" fill="url(#archipelago-sea)" />
    {waves.map(([x, y]) => <path key={`${x}-${y}`} className="sea-wave" d={`M${x * 10} ${y * 5.6} q8 -6 16 0 t16 0`} stroke="#fff" strokeOpacity=".7" strokeWidth="3" fill="none" strokeLinecap="round" />)}
    <ellipse cx="130" cy="486" rx="86" ry="42" fill="#fff" opacity=".22" />
    <ellipse cx="130" cy="486" rx="86" ry="42" fill="none" stroke="#fff" strokeWidth="3" strokeDasharray="4 10" strokeLinecap="round" opacity=".9" />
    <g transform="translate(948 60)" opacity=".85">
      <circle r="28" fill="#fff" opacity=".3" /><circle r="28" fill="none" stroke="#fff" strokeWidth="2" />
      <path d="M0 -24 L6 0 L0 24 L-6 0Z" fill="#fff" /><path d="M0 -24 L6 0 L-6 0Z" fill="#ff6b5e" />
    </g>
    <g transform="translate(560 492)">
      <path d="M-60 6 q-16 4 -30 0" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeDasharray="2 8" opacity=".8" />
      <path d="M-24 0 h48 l-8 12 h-32z" fill="#8a5a35" />
      <path d="M0 0 v-40 l22 34z" fill="#fff" /><path d="M0 -40 v34 h-16z" fill="#ffe27a" />
    </g>
  </svg>;
}

export function Dashboard({ apiClient }: { apiClient: StudydyApiClient }) {
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [progress, setProgress] = useState<Map<string, Progress>>(new Map());
  const [cardSets, setCardSets] = useState<CardSetSummary[] | null>(null);
  const [podcasts, setPodcasts] = useState<PodcastSummary[] | null>(null);
  const [activity, setActivity] = useState<LearningActivityView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then(
      (library) => { if (!cancelled) setMaterials(library.materials); },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    // 概念卡與 Podcast 只是首頁捷徑，讀取失敗時隱藏該卡，不阻擋群島。
    void apiClient.listCardSets().then((value) => { if (!cancelled) setCardSets(value.card_sets); }, () => { if (!cancelled) setCardSets([]); });
    void apiClient.listPodcasts().then((value) => { if (!cancelled) setPodcasts(value.podcasts); }, () => { if (!cancelled) setPodcasts([]); });
    void apiClient.getLearningActivity().then((value) => { if (!cancelled) setActivity(value); }, () => { if (!cancelled) setActivity(null); });
    return () => { cancelled = true; };
  }, [apiClient, retry]);

  // 依最近學習排序：有學習紀錄的教材在前，其餘依建立時間。
  const ordered = useMemo(() => [...(materials ?? [])].sort((a, b) => {
    const at = headSession(a)?.started_at ?? "", bt = headSession(b)?.started_at ?? "";
    if (at !== bt) return bt.localeCompare(at);
    return b.created_at.localeCompare(a.created_at);
  }), [materials]);
  const shown = ordered.slice(0, slots.length);
  // 優先接續學習中的教材，其次查看已完成成果，最後才是尚未開始的地圖。
  const focus = (["learning", "completed", "ready"] as const).map((state) => ordered.find((item) => islandState(item) === state)).find(Boolean);

  // 只為島上顯示的教材讀取概念數與進度；個別失敗只少顯示數字。
  useEffect(() => {
    let cancelled = false;
    for (const material of shown) {
      const structure = headStructure(material);
      if (!structure) continue;
      const session = headSession(material);
      const request = { materialId: material.material_id, structureRevision: structure.knowledge_structure_revision };
      void Promise.all([
        apiClient.getKnowledgeOutline(request),
        session ? apiClient.readMapProgress(session.study_session_id, structure.knowledge_structure_revision) : Promise.resolve(null),
      ]).then(([outline, map]) => {
        if (cancelled) return;
        const currentId = map?.current_concept_id ?? session?.current_concept_id ?? null;
        const value: Progress = {
          total: outline.concepts.length,
          completed: map?.concept_states.filter((item) => item.status === "completed").length ?? 0,
          currentLabel: outline.concepts.find((item) => item.concept_id === currentId)?.label ?? null,
        };
        setProgress((previous) => new Map(previous).set(material.material_id, value));
      }, () => undefined);
    }
    return () => { cancelled = true; };
    // shown 由 materials 推導；materials 更新時才重新讀取。
  }, [apiClient, materials]);

  const focusSession = focus && headSession(focus);
  const focusProgress = focus && progress.get(focus.material_id);
  const continueRoute: AppRoute | null = focus && focusSession
    ? { name: "study-session", materialId: focus.material_id, runId: focusSession.run_id, structureRevision: focusSession.knowledge_structure_revision, studySessionId: focusSession.study_session_id }
    : null;
  const pages = (materials ?? []).reduce((sum, item) => sum + (item.latest_attempt?.total_pages ?? 0), 0);
  const completedConcepts = [...progress.values()].reduce((sum, item) => sum + item.completed, 0);
  const latestCards = cardSets?.filter((item) => item.card_count !== null).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const latestPodcast = podcasts?.filter((item) => item.status === "ready").sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const today = new Intl.DateTimeFormat("zh-TW", { weekday: "long", month: "long", day: "numeric" }).format(new Date());

  return (
    <section className="dashboard">
      <header className="dashboard-greeting">
        <div>
          <p className="eyebrow">{today}</p>
          <h1>歡迎回來！今天想探索哪座島？</h1>
        </div>
        <div className="dashboard-actions">
          <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "topics" })}><Icon name="sparkle" size={18} />從主題建立教材</button>
          <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}><Icon name="upload" size={18} />上傳教材</button>
        </div>
      </header>
      <div className="dashboard-content">
        <div className="dashboard-primary">
          <section className="archipelago surface" aria-labelledby="archipelago-title">
            <header className="archipelago-head">
              <div>
                <h2 id="archipelago-title">我的學習群島</h2>
                {materials && <span>{materials.length} 份教材{pages > 0 && ` · ${pages} 頁`}</span>}
              </div>
              <button className="text-button" type="button" onClick={() => writeRoute({ name: "materials" })}>列表檢視<Icon name="chevron-right" size={16} /></button>
            </header>
            {error ? (
              <div className="dashboard-error" role="alert">
                <p>{error}</p>
                <button className="secondary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button>
              </div>
            ) : (
              <div className="archipelago-viewport">
                <div className="archipelago-stage" aria-busy={materials === null}>
                  <Sea />
                  <ul className="archipelago-islands" aria-label="教材島嶼">
                    {shown.map((material, index) => {
                      const state = islandState(material);
                      const value = progress.get(material.material_id);
                      const slot = slots[index];
                      const status = statusText(state, value);
                      const started = value?.completed ?? 0;
                      return <li key={material.material_id} className={`archipelago-island is-${state}${material === focus ? " is-focus" : ""}`}
                        style={{ left: `${slot.x}%`, top: `${slot.y}%`, width: `${slot.w}%` }}>
                        <button type="button" onClick={() => openMaterial(material)} aria-label={`${material.display_name}，${status}`}>
                          <IslandArt palette={islandPalette(material.material_id)} viewBox="100 60 840 470" landmarks={state !== "building"} />
                          <span className="island-flag">
                            {material === focus && state === "learning" && <span className="island-pin">你在這裡</span>}
                            <ProgressRing ratio={value && value.total ? started / value.total : 0} color={state === "completed" ? "var(--success)" : "var(--warning)"}>
                              {state === "building" ? <Icon name="process" size={16} /> : state === "failed" ? <Icon name="warning" size={16} /> : started}
                            </ProgressRing>
                            <span className="island-flag-text"><b>{material.display_name}</b><small>{status}</small></span>
                          </span>
                        </button>
                      </li>;
                    })}
                  </ul>
                  <button className="archipelago-new" type="button" onClick={() => writeRoute({ name: "upload" })}>
                    <span><Icon name="plus" size={24} /></span><b>發現新島嶼</b><small>上傳教材或輸入主題</small>
                  </button>
                  {ordered.length > shown.length && <button className="archipelago-more" type="button" onClick={() => writeRoute({ name: "materials" })}>還有 {ordered.length - shown.length} 座島嶼</button>}
                </div>
              </div>
            )}
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
          </section>
        </div>
        <aside className="dashboard-side">
          <section className="dashboard-overview surface" aria-label="學習總覽">
            <h2>學習總覽</h2>
            <div className="dashboard-stats" aria-busy={materials === null && !error}>
              {([
                ["book", "is-blue", materials?.length, "份教材"],
                ["learning", "is-amber", materials ? ordered.filter((item) => islandState(item) === "learning").length : undefined, "學習中的教材"],
                ["check", "is-green", materials ? completedConcepts : undefined, "已完成概念"],
              ] as const).map(([icon, tone, value, label]) => <div key={label} className="dashboard-stat">
                <span className={`stat-icon ${tone}`}><Icon name={icon} size={18} /></span>
                <strong>{error ? "—" : (value ?? "—")}</strong>
                <small>{label}</small>
              </div>)}
            </div>
          </section>
          {activity && <WeekRhythm activity={activity} />}
          {cardSets && <section className="dashboard-shortcut is-cards surface" aria-label="概念卡">
            <header><h2>概念卡</h2>{cardSets.length > 0 && <button className="text-button" type="button" onClick={() => writeRoute({ name: "concept-cards" })}>全部<Icon name="chevron-right" size={16} /></button>}</header>
            {latestCards ? <button type="button" className="shortcut-card" onClick={() => writeRoute({ name: "card-set", cardSetId: latestCards.card_set_id, materialId: latestCards.material_id })}>
              <small>{latestCards.material_name}</small>
              <b>{latestCards.name}</b>
              <span><Icon name="cards" size={16} />{latestCards.card_count} 張卡片</span>
            </button> : <p>還沒有概念卡。從教材的知識地圖挑選概念，就能建立一組。</p>}
          </section>}
          {podcasts && <section className="dashboard-shortcut is-podcast surface" aria-label="Podcast">
            <header><h2>Podcast</h2>{podcasts.length > 0 && <button className="text-button" type="button" onClick={() => writeRoute({ name: "podcasts" })}>全部<Icon name="chevron-right" size={16} /></button>}</header>
            {latestPodcast ? <button type="button" className="shortcut-podcast" onClick={() => writeRoute({ name: "podcast", podcastId: latestPodcast.podcast_id, materialId: latestPodcast.material_id })}>
              <span className="shortcut-podcast-cover" aria-hidden="true"><Icon name="headphones" size={30} /></span>
              <span>
                <small>{latestPodcast.material_name}</small>
                <b>{latestPodcast.name}</b>
                <span>{latestPodcast.delivery === "dialogue" ? "雙人對談" : "單人講解"} · {latestPodcast.episode_count} 集</span>
              </span>
            </button> : <p>還沒有完成的 Podcast。可以從教材挑選概念生成節目。</p>}
          </section>}
        </aside>
      </div>
    </section>
  );
}
