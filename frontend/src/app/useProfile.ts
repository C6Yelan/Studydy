import { useEffect, useState } from "react";
import type { StudydyApiClient } from "../api/client";
import type { LearnerProfile } from "../api/contracts";

// 名稱與首頁、個人檔案共用；讀取失敗時視為未設定，不阻擋其他功能。
export function useProfile(apiClient: StudydyApiClient): LearnerProfile | null {
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiClient.getProfile().then((value) => { if (!cancelled) setProfile(value); }, () => undefined);
    const unsubscribe = apiClient.onProfileChange(setProfile);
    return () => { cancelled = true; unsubscribe(); };
  }, [apiClient]);
  return profile;
}
