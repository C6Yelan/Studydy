import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";

export function useAssessmentPresence(api: StudydyApiClient, materialId: string, setId: string | null, answering: boolean) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    if (!answering || !setId) return;
    let left = false;
    const report = () => void api.setAssessmentPresence(materialId, setId, true).then(
      () => { if (!left) setError(null); },
      failure => { if (!left) setError(errorMessage(failure)); },
    );
    report();
    const timer = window.setInterval(report, 20000);
    // 離頁只停回報；同瀏覽器其他分頁仍可能作答，交由伺服器期限解除。
    return () => { left = true; window.clearInterval(timer); };
  }, [api, materialId, setId, answering]);
  return error;
}
