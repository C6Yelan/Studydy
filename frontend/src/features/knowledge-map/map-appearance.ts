import type { RelationType } from "../../api/contracts";

// 關係在地圖上的標籤與顏色；顏色同時用在線上標籤與選島卡片的關係徽章。
export const relationStyles: Record<RelationType, { label: string; color: string }> = {
  prerequisite: { label: "先備", color: "#2f6bff" },
  part_of: { label: "組成", color: "#22a865" },
  application: { label: "應用", color: "#f08a00" },
  example: { label: "例子", color: "#1aa3b8" },
  contrast: { label: "對照", color: "#e0569a" },
};
