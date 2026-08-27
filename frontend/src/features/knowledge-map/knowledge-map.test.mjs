import assert from "node:assert/strict";
import test from "node:test";

import { relationPresentation, safeExternalUrl } from "./knowledge-map.ts";

test("production Relation presentation contains only the frozen three types", () => {
  assert.deepEqual(
    ["prerequisite", "contains", "related"].map((type) => ({ type, ...relationPresentation(type) })),
    [
      {
        type: "prerequisite",
        className: "is-prerequisite",
        directional: true,
        label: "先備關係",
        explanation: "來源概念需要先學，再進入目標概念。",
      },
      {
        type: "contains",
        className: "is-contains",
        directional: true,
        label: "組成關係",
        explanation: "來源概念包含目標概念作為內容的一部分。",
      },
      {
        type: "related",
        className: "is-related",
        directional: false,
        label: "互相關聯",
        explanation: "兩個概念在教材中互有關聯，沒有單向學習箭頭。",
      },
    ],
  );
});

test("resource links only allow HTTP(S)", () => {
  assert.equal(safeExternalUrl("https://example.com/resource"), "https://example.com/resource");
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("not a URL"), null);
});
