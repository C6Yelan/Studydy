import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { PodcastView } from "../../api/contracts";
import { validTimeline, type Timeline } from "../podcasts/PodcastTranscript";
import { ExportControls } from "./ExportControls";

export function PodcastExports({ api, view, index, videoReady, videoVersion }: {
  api: StudydyApiClient; view: PodcastView; index: number; videoReady: boolean; videoVersion: number;
}) {
  const [aligned, setAligned] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setAligned(false);
    if (!videoReady) void api.studyTools<Timeline>(`/v1/podcasts/${view.podcast_id}/episodes/${index}/timeline`)
      .then(value => { if (!cancelled) setAligned(validTimeline(value, view, index)); }, () => {});
    return () => { cancelled = true; };
  }, [api, view, index, videoReady, videoVersion]);
  return <ExportControls label="匯出本集" options={[
    { value: "mp3", label: "MP3 音檔" },
    ...(videoReady || aligned ? [{ value: "md", label: "逐字稿 Markdown" }] : []),
    ...(videoReady ? [{ value: "mp4", label: "MP4 影片" }] : []),
  ]} download={format => api.downloadExport(`/v1/podcasts/${view.podcast_id}/episodes/${index}/export/${format}`)} />;
}
