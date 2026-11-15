import type { RelationType } from "../../api/contracts";

// 關係在地圖上的白話標籤與顏色；顏色同時用在線上標籤與選島卡片的關係徽章。
export const relationStyles: Record<RelationType, { label: string; color: string }> = {
  prerequisite: { label: "先學", color: "#2f6bff" },
  part_of: { label: "一部分", color: "#22a865" },
  application: { label: "用在", color: "#f08a00" },
  example: { label: "舉例", color: "#1aa3b8" },
  contrast: { label: "比一比", color: "#e0569a" },
};
