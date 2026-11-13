import type { RelationType } from "../../api/contracts";

// meaning：地圖右上「橋的說明」用的白話，例如「石橋：要先學」。
export const relationStyles: Record<RelationType, { label: string; bridge: string; meaning: string; color: string }> = {
  prerequisite: { label: "先備", bridge: "石橋", meaning: "要先學", color: "#2f6bff" },
  part_of: { label: "組成", bridge: "木橋", meaning: "是它的一部分", color: "#22a865" },
  application: { label: "應用", bridge: "航線", meaning: "會用到", color: "#f08a00" },
  example: { label: "例子", bridge: "踏石", meaning: "舉例", color: "#1aa3b8" },
  contrast: { label: "對照", bridge: "對岸", meaning: "拿來比較", color: "#e0569a" },
};
