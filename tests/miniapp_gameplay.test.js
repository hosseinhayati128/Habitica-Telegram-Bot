"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const gameplay = require("../static/miniapp/gameplay.js");

function daily(id, overrides = {}) {
  return {
    id,
    text: `Daily ${id}`,
    notes: "Useful context",
    priority: 1,
    checklist: [],
    completed: false,
    ...overrides,
  };
}

test("day status normalization distinguishes ready, one missed day, and multiple missed days", () => {
  assert.deepEqual(gameplay.normalizeDayPayload({ day: { refreshRequired: false } }), {
    refreshRequired: false,
    daysMissed: 0,
    reviewLabel: "Yesterday",
    dailies: [],
  });

  const review = gameplay.normalizeDayPayload({
    day: {
      refreshRequired: true,
      daysMissed: 1,
      reviewLabel: "Yesterday",
      dailies: [daily("one"), daily("one", { text: "Duplicate" }), daily("done", { completed: true })],
    },
  });
  assert.equal(review.daysMissed, 1);
  assert.deepEqual(review.dailies.map((item) => item.id), ["one"]);
  assert.equal(review.dailies[0].text, "Daily one");

  const multiple = gameplay.normalizeDayPayload({
    day: { refreshRequired: true, daysMissed: 4, reviewLabel: "Last active day", dailies: [] },
  });
  assert.equal(multiple.daysMissed, 4);
  assert.equal(multiple.reviewLabel, "Last active day");
});

test("review Daily normalization safely filters malformed and completed entries", () => {
  const result = gameplay.normalizeReviewDailies([
    null,
    { id: "missing-title" },
    daily("valid", {
      checklist: [
        { id: "step", text: "First", completed: true },
        { text: "Second", completed: false },
        { id: "bad" },
      ],
    }),
    daily("completed", { completed: true }),
  ]);
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].checklist, [
    { id: "step", text: "First", completed: true },
    { id: "checklist-1", text: "Second", completed: false },
  ]);
});

test("partial refresh selection drops resolved IDs and preserves only unresolved eligible IDs", () => {
  const selected = new Set(["scored", "retry", "stale"]);
  const reconciled = gameplay.reconcileReviewSelection(selected, {
    resolvedDailyIds: ["scored"],
    unresolvedDailyIds: ["retry"],
    dailies: [daily("retry"), daily("other")],
  });
  assert.deepEqual([...reconciled], ["retry"]);
  assert.deepEqual([...selected], ["scored", "retry", "stale"], "the input selection remains immutable");
});

test("potion metadata uses authoritative optional fields and sanitizes malformed values", () => {
  assert.deepEqual(gameplay.normalizePotionPayload({
    potion: { name: "Health Potion", price: 25, healing: 15 },
    stats: { hp: 35, maxHp: 50, gold: 100.25 },
    healthFull: false,
    canAfford: true,
  }), {
    name: "Health Potion",
    price: 25,
    healing: 15,
    healthFull: false,
    canAfford: true,
    stats: { hp: 35, maxHp: 50, gold: 100.25 },
  });

  const malformed = gameplay.normalizePotionPayload({
    potion: { price: -1, healing: "15" },
    stats: { hp: null, maxHp: Infinity, gold: -4 },
  });
  assert.equal(malformed.price, null);
  assert.equal(malformed.healing, null);
  assert.deepEqual(malformed.stats, { hp: null, maxHp: null, gold: null });
});

test("profile envelopes accept canonical nested mutation responses only", () => {
  const nested = gameplay.profileEnvelope({
    profile: { profile: { level: 113, class: "warrior" }, stats: { hp: 50, gold: 90 } },
  });
  assert.deepEqual(nested, {
    profile: { level: 113, class: "warrior" },
    stats: { hp: 50, gold: 90 },
  });
  assert.equal(gameplay.profileEnvelope({ profile: { level: 113 } }), null);
});

test("startup state machine exposes every explicit state and rejects invented states", () => {
  const machine = gameplay.createStartupStateMachine();
  assert.equal(machine.current, "checking_day");
  assert.equal(machine.transition("review_required"), 1);
  assert.equal(machine.transition("submitting_review"), 2);
  assert.equal(machine.transition("refreshing_day"), 3);
  assert.equal(machine.transition("ready"), 4);
  assert.equal(machine.is("ready"), true);
  assert.throws(() => machine.transition("silently_run_cron"), /invalid/i);
});
