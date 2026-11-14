import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { PodcastSummary } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { Icon, type IconName } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";
import { formatTime } from "../podcasts/PodcastPlayer";

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

export type QuickItem = {
  icon: IconName; kicker: string; title: string; meta: string; cta: string; onOpen?: () => void;
  /** 有教材時畫那座島、顯示概念進度。 */
  island?: { materialId: string; completed: number; total: number };
};

// 首頁最上面的「繼續上次的冒險」：接續學習（或查看成果、開地圖、上傳）的大卡，下面一列最近收聽且未聽完的 Podcast。
export function QuickAccess({ apiClient, next }: { apiClient: StudydyApiClient; next: QuickItem }) {
  const podcast = useLastPodcast(apiClient);
  const label = `${next.kicker}：${next.title}`;
  return <section className="dashboard-continue" aria-label="接續學習">
    <div className="continue-card surface">
      <div className="continue-art" aria-hidden="true">
        {next.island ? <IslandArt palette={islandPalette(next.island.materialId)} seed={next.island.materialId} viewBox="100 60 840 470" landmarks={false} /> : <Icon name={next.icon} size={44} />}
      </div>
      <div className="continue-info">
        <div className="continue-text">
          <small className="continue-kicker">{next.kicker}</small>
          <b className="continue-title">{next.title}</b>
          <small className="continue-meta">{next.meta}</small>
          {next.island && next.island.total > 0 && <Progress value={next.island.completed} max={next.island.total} aria-label="概念學習進度"
            aria-valuetext={`已完成 ${next.island.completed} / ${next.island.total} 個概念`} />}
        </div>
        {next.onOpen && <button className="primary-button continue-go" type="button" aria-label={label} onClick={next.onOpen}>{next.cta}</button>}
      </div>
    </div>
    {podcast && <button className="continue-podcast" type="button" aria-label={`繼續收聽：${podcast.podcast.name}`}
      onClick={() => writeRoute({ name: "podcast", podcastId: podcast.podcast.podcast_id, materialId: podcast.podcast.material_id })}>
      <span className="quest-icon" aria-hidden="true"><Icon name="headphones" size={18} /></span>
      <span className="quick-item-text">
        <small>繼續收聽</small>
        <b>{podcast.podcast.name}</b>
        <small>{`第 ${podcast.episode + 1} 集${podcast.completed ? " 已聽完" : ` · 停在 ${formatTime(podcast.position)}`}`}</small>
      </span>
      <Icon name="chevron-right" size={16} />
    </button>}
  </section>;
}
