import type { KnowledgeMapView } from "../../api/contracts";

export type RelationType = KnowledgeMapView["relations"][number]["type"];

export type RelationPresentation = {
  className: string;
  directional: boolean;
  label: string;
  explanation: string;
};

export function relationPresentation(type: RelationType): RelationPresentation {
  if (type === "prerequisite") return {
    className: "is-prerequisite",
    directional: true,
    label: "先備關係",
    explanation: "來源概念需要先學，再進入目標概念。",
  };
  if (type === "contains") return {
    className: "is-contains",
    directional: true,
    label: "組成關係",
    explanation: "來源概念包含目標概念作為內容的一部分。",
  };
  return {
    className: "is-related",
    directional: false,
    label: "互相關聯",
    explanation: "兩個概念在教材中互有關聯，沒有單向學習箭頭。",
  };
}

export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}
