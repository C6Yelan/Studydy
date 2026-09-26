import type { LearnerIdentity } from "./contracts";
import { identity as isLearnerIdentity } from "./response-validation.ts";

const sessionHintKey = "studydy.session-hint";

// 僅供還原介面；不保存 token，也不能取代資料 API 的 cookie/owner 驗證。
export function readSessionHint(): LearnerIdentity | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(sessionHintKey) ?? "null");
    if (!isLearnerIdentity(value)) return null;
    return { schema: "learner-identity/v1", learner_id: value.learner_id };
  } catch {
    return null;
  }
}

export function saveSessionHint(identity: LearnerIdentity | null): void {
  try {
    if (identity)
      localStorage.setItem(
        sessionHintKey,
        JSON.stringify({ schema: "learner-identity/v1", learner_id: identity.learner_id }),
      );
    else localStorage.removeItem(sessionHintKey);
  } catch {
    /* 儲存受限時仍可用 cookie 完成登入與 API 操作。 */
  }
}
