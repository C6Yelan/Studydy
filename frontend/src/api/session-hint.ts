import type { LearnerIdentity } from "./client";

const key = "studydy.session-hint";

// 僅供還原介面；不保存 token，也不能取代資料 API 的 cookie/owner 驗證。
export function readSessionHint(): LearnerIdentity | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    return value?.schema === "learner-identity/v1" &&
      typeof value.learner_id === "string" &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        value.learner_id,
      )
      ? { schema: "learner-identity/v1", learner_id: value.learner_id }
      : null;
  } catch {
    return null;
  }
}

export function saveSessionHint(identity: LearnerIdentity | null): void {
  try {
    if (identity)
      localStorage.setItem(
        key,
        JSON.stringify({ schema: "learner-identity/v1", learner_id: identity.learner_id }),
      );
    else localStorage.removeItem(key);
  } catch {
    /* 儲存受限時仍可用 cookie 完成登入與 API 操作。 */
  }
}
