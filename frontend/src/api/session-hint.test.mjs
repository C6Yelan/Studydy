import assert from "node:assert/strict";
import test from "node:test";
import { readSessionHint, saveSessionHint } from "./session-hint.ts";

test("session hint persists only a public identity and can be discarded", () => {
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storageData = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => storageData.get(key) ?? null,
      setItem: (key, value) => storageData.set(key, value),
      removeItem: (key) => storageData.delete(key),
    },
  });
  try {
    const identity = {
      schema: "learner-identity/v1",
      learner_id: "33333333-3333-4333-8333-333333333333",
    };
    saveSessionHint({ ...identity, token: "must-not-be-persisted" });
    assert.deepEqual(readSessionHint(), identity);
    assert.deepEqual([...storageData.values()].map((value) => JSON.parse(value)), [identity]);
    storageData.set("studydy.session-hint", JSON.stringify({ ...identity, learner_id: "invalid" }));
    assert.equal(readSessionHint(), null);
    saveSessionHint(null);
    assert.equal(storageData.size, 0);
  } finally {
    if (storageDescriptor) Object.defineProperty(globalThis, "localStorage", storageDescriptor);
    else delete globalThis.localStorage;
  }
});

test("unavailable storage returns no hint and clearing does not throw", () => {
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
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
    if (storageDescriptor) Object.defineProperty(globalThis, "localStorage", storageDescriptor);
    else delete globalThis.localStorage;
  }
});
