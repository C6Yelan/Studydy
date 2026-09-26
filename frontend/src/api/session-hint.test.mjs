import assert from "node:assert/strict";
import test from "node:test";
import { readSessionHint, saveSessionHint } from "./session-hint.ts";

test("session hint persists only a public identity and can be discarded", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const data = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k) => data.get(k) ?? null,
      setItem: (k, v) => data.set(k, v),
      removeItem: (k) => data.delete(k),
    },
  });
  try {
    const identity = {
      schema: "learner-identity/v1",
      learner_id: "33333333-3333-4333-8333-333333333333",
    };
    saveSessionHint({ ...identity, token: "must-not-be-persisted" });
    assert.deepEqual(readSessionHint(), identity);
    assert.equal([...data.values()].join("").includes("must-not-be-persisted"), false);
    data.set("studydy.session-hint", '{"schema":"learner-identity/v1","learner_id":"invalid"}');
    assert.equal(readSessionHint(), null);
    saveSessionHint(null);
    assert.equal(data.size, 0);
  } finally {
    if (saved) Object.defineProperty(globalThis, "localStorage", saved);
    else delete globalThis.localStorage;
  }
});

test("unavailable storage does not prevent cookie based session recovery", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("storage unavailable");
    },
  });
  try {
    assert.equal(readSessionHint(), null);
    assert.doesNotThrow(() => saveSessionHint(null));
  } finally {
    if (saved) Object.defineProperty(globalThis, "localStorage", saved);
    else delete globalThis.localStorage;
  }
});
