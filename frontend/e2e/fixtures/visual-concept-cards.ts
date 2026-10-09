// 純合成的視覺邊界案例，不包含私人教材或真實模型品質判定。
import { structureView } from "./knowledge-map.ts";

export const visualCases = ["one", "two", "three", "four", "many", "code", "formula", "long", "missing-relation"] as const;
export type VisualCase = typeof visualCases[number];

export function visualCase(name: VisualCase) {
  const view = structureView();
  view.relations = [];
  const card = view.concepts[0];
  card.label = name === "two" ? "通訊端點（合成案例）" : "資料的表示（合成案例）";
  const original = structuredClone(card.claims[0]);
  const text = ["資料可以使用不同形式表示。", "表示方式需保留原本的意義與必要條件。", "讀取資料時，應依其保存的格式解讀。", "同一份資料的不同表示，不表示資料內容相同。", "編碼與解碼需使用對應的規則。", "來源位置用來回查資料原文。"];
  const count = name === "one" ? 1 : name === "two" ? 2 : name === "three" ? 3 : name === "four" ? 4 : name === "many" ? 6 : 1;
  card.claims = text.slice(0, count).map((text, i) => {
    const claim = structuredClone(original);
    claim.claim_id = `claim:sha256:${(100 + i).toString(16).padStart(64, "0")}`;
    claim.text = text;
    Object.assign(claim.evidence[0], {
      evidence_id: `evidence:sha256:${(100 + i).toString(16).padStart(64, "0")}`,
      quote: text, source_name: i % 2 ? "表示方式 B.pdf" : "資料表示 A.pdf",
      source_id: i % 2 ? "66666666-6666-4666-8666-666666666666" : "77777777-7777-4777-8777-777777777777",
      normalized_page: i + 1,
    });
    return claim;
  });
  if (name === "two") {
    card.claims[0].text = "能參與網路通訊的端點，例如電腦、行動裝置與服務主機。";
    card.claims[1].text = "同一台電腦可在不同通訊中扮演不同角色。";
  } else if (name === "code") {
    card.label = "Array indexing（程式碼合成案例）";
    card.claims[0].text = "const values = [2, 4, 6];\n\nif (index >= 0 && index < values.length) {\n  return values[index] !== 0;\n}\n// 不更動識別符、否定或邊界條件";
    card.claims[0].evidence[0].kind = "code";
  } else if (name === "formula") {
    card.label = "等差級數（公式合成案例）";
    card.claims[0].text = "Σᵢ₌₁ⁿ i = n(n + 1)/2\nn ∈ ℕ，n ≥ 1\n\nf(x) = (x² − 1)/(x − 1)，x ≠ 1";
    card.claims[0].evidence[0].kind = "formula";
  } else if (name === "long") {
    card.label = "長標題與混合術語：Transmission Control Protocol 與資料表示的必要條件、限制及來源（合成版面邊界案例）";
    card.claims[0].text = "Transmission Control Protocol 必須保留這個完整英文術語。".repeat(12) + "\n必要条件：x != 0；禁止刪除否定與數字 65535。";
  } else if (name === "missing-relation") {
    view.status.quality = "needs_review"; view.status.decision = "review";
    view.relations = structureView().relations;
    view.relations[0].evidence_refs = [`evidence:sha256:${"0".repeat(64)}`];
  }
  card.claims.forEach(c => { c.evidence[0].quote = c.text; });
  return view;
}
