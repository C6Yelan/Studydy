import type { RelationType } from "../../api/contracts";

export const relationStyles: Record<RelationType, { label: string; bridge: string; color: string }> = {
  prerequisite: { label: "先備", bridge: "石橋", color: "#2f6bff" },
  part_of: { label: "組成", bridge: "木橋", color: "#22a865" },
  application: { label: "應用", bridge: "航線", color: "#f08a00" },
  example: { label: "例子", bridge: "踏石", color: "#1aa3b8" },
  contrast: { label: "對照", bridge: "對岸", color: "#e0569a" },
};
