import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { LearningXp, PodcastSummary } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon, type IconName } from "../../ui/Icon";
import { formatTime } from "../podcasts/PodcastPlayer";

// 問候列旁的等級徽章：盾牌標出等級，進度條是本級經驗值；點了進成就頁。
export function LevelBadge({ xp }: { xp: LearningXp }) {
  const ratio = xp.level_target ? Math.min(1, xp.level_xp / xp.level_target) : 0;
  return <button className="greeting-level" type="button" onClick={() => writeRoute({ name: "achievements" })}
    aria-label={`第 ${xp.level} 級，本級 ${xp.level_xp} / ${xp.level_target} XP，查看成就`}>
    <span className="greeting-level-shield" aria-hidden="true">
      <svg viewBox="0 0 40 44"><path d="M20 2 36 8v12c0 11-7 18-16 22C11 38 4 31 4 20V8Z" /></svg>
      <b>{xp.level}</b>
    </span>
    <span className="greeting-level-text" aria-hidden="true">
      <small>第 {xp.level} 級{xp.today > 0 && <> · 今天 +{xp.today}</>}</small>
      <span className="greeting-level-bar"><i style={{ width: `${Math.max(ratio > 0 ? 4 : 0, ratio * 100)}%` }} /></span>
      <small>{xp.level_xp} / {xp.level_target} XP</small>
    </span>
  </button>;
}

type LastPodcast = { podcast: PodcastSummary; episode: number; position: number; completed: boolean };

// 最近收聽、還沒聽完的 Podcast：依伺服器收聽進度的更新時間挑最新一筆；每集都聽完的略過。
// （completed_episodes 是已生成的集數，不是聽完的集數，所以要讀收聽進度才知道。）
function useLastPodcast(apiClient: StudydyApiClient): LastPodcast | null {
  const [last, setLast] = useState<LastPodcast | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.listPodcasts().then(async ({ podcasts }) => {
      const ready = podcasts.filter((item) => item.status === "ready");
      const read = await Promise.all(ready.map((podcast) => apiClient.getPodcastProgress(podcast.podcast_id).then(
        (progress) => ({ podcast, progress }), () => null)));
      let found: LastPodcast | null = null, at = "";
      for (const item of read) {
        const current = item?.progress.current;
        if (!item || !current || current.updated_at <= at) continue;
        if (item.progress.episodes.filter((episode) => episode.completed).length >= item.podcast.episode_count) continue;
        at = current.updated_at;
        found = { podcast: item.podcast, episode: current.episode_index, position: current.position_seconds,
          completed: Boolean(item.progress.episodes.find((episode) => episode.episode_index === current.episode_index)?.completed) };
      }
      if (!cancelled) setLast(found);
    }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient]);
  return last;
}

export type QuickItem = { icon: IconName; kicker: string; title: string; meta: string; onOpen?: () => void };

function QuickRow({ item }: { item: QuickItem }) {
  const body = <>
    <span className="quest-icon" aria-hidden="true"><Icon name={item.icon} size={18} /></span>
    <span className="quick-item-text">
      <small>{item.kicker}</small>
      <b>{item.title}</b>
      <small>{item.meta}</small>
    </span>
  </>;
  return <li>{item.onOpen
    ? <button className="quick-item" type="button" aria-label={`${item.kicker}：${item.title}`} onClick={item.onOpen}>{body}<Icon name="chevron-right" size={16} /></button>
    : <div className="quick-item">{body}</div>}</li>;
}

// 右欄快速存取：接續學習與最近收聽的 Podcast，與今日任務同樣的列表樣式。
export function QuickAccess({ apiClient, next }: { apiClient: StudydyApiClient; next: QuickItem }) {
  const podcast = useLastPodcast(apiClient);
  return <section className="dashboard-quick surface" aria-labelledby="dashboard-quick-title">
    <h2 id="dashboard-quick-title">快速存取</h2>
    <ul>
      <QuickRow item={next} />
      {podcast && <QuickRow item={{
        icon: "headphones", kicker: "繼續收聽", title: podcast.podcast.name,
        meta: `第 ${podcast.episode + 1} 集${podcast.completed ? " 已聽完" : ` · 停在 ${formatTime(podcast.position)}`}`,
        onOpen: () => writeRoute({ name: "podcast", podcastId: podcast.podcast.podcast_id, materialId: podcast.podcast.material_id }),
      }} />}
    </ul>
  </section>;
}
