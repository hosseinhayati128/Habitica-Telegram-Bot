"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const tasks = require("../static/miniapp/tasks.js");

function fixtureTask(overrides = {}) {
  return {
    id: "habit-1",
    type: "habit",
    text: "Drink water",
    notes: "",
    priority: 1,
    value: 0,
    up: true,
    down: true,
    completed: false,
    dueToday: false,
    streak: 0,
    date: null,
    startDate: null,
    repeatDays: [],
    scheduleEditable: false,
    checklist: [],
    ...overrides,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function nextTurn() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("Habitica task-value colors preserve every official boundary and neutralize invalid values", () => {
  const cases = [
    [-20.001, "worst"],
    [-20, "worse"],
    [-10.001, "worse"],
    [-10, "bad"],
    [-1.001, "bad"],
    [-1, "neutral"],
    [0, "neutral"],
    [0.999, "neutral"],
    [1, "good"],
    [4.999, "good"],
    [5, "better"],
    [9.999, "better"],
    [10, "best"],
  ];

  cases.forEach(([value, expected]) => {
    assert.equal(tasks.taskColorToken(value), expected, `value ${value}`);
  });
  [undefined, null, "10", NaN, Infinity, -Infinity, {}, []].forEach((value) => {
    assert.equal(tasks.taskColorToken(value), "neutral");
  });
});

test("compact metadata keeps only useful schedule, due-date, streak, and checklist facts", () => {
  const daily = fixtureTask({
    type: "daily",
    dueToday: true,
    streak: 7.9,
    checklist: [
      { id: "one", text: "First", completed: true },
      { id: "two", text: "Second", completed: false },
    ],
  });
  assert.deepEqual(tasks.compactTaskMetadata(daily), [
    "1/2 checklist",
    "Due today",
    "7 day streak",
  ]);

  const todo = fixtureTask({
    type: "todo",
    completed: false,
    date: "2026-08-20",
    checklist: [{ id: "one", text: "First", completed: false }],
  });
  assert.deepEqual(tasks.compactTaskMetadata(todo), ["0/1 checklist", "Due 2026-08-20"]);
  assert.deepEqual(tasks.compactTaskMetadata(fixtureTask()), []);
});

test("quick add creates minimal safe defaults for the active task type", () => {
  assert.deepEqual(tasks.quickAddDefaults("habits", "Stretch"), {
    type: "habit",
    text: "Stretch",
    notes: "",
    priority: 1,
    up: true,
    down: true,
  });
  assert.deepEqual(tasks.quickAddDefaults("daily", "Medicine"), {
    type: "daily",
    text: "Medicine",
    notes: "",
    priority: 1,
    scheduleEditable: true,
    repeatDays: [...tasks.DAY_KEYS],
    startDate: "",
    checklist: [],
  });
  assert.deepEqual(tasks.quickAddDefaults("todos", "Book appointment"), {
    type: "todo",
    text: "Book appointment",
    notes: "",
    priority: 1,
    date: null,
    checklist: [],
  });
  assert.throws(() => tasks.quickAddDefaults("reward", "Unsafe"), /invalid/i);
});

test("profile coordinator rejects stale responses and applies only the newest profile", async () => {
  const firstResponse = deferred();
  const secondResponse = deferred();
  const responses = [firstResponse, secondResponse];
  const applied = [];
  const coordinator = tasks.createProfileCoordinator({
    fetchProfile: () => responses.shift().promise,
    applyProfile: (profile) => applied.push(profile),
  });

  const firstLoad = coordinator.load();
  const secondLoad = coordinator.load();
  firstResponse.resolve({ marker: "old" });
  assert.deepEqual(await firstLoad, { applied: false, stale: true });
  assert.deepEqual(applied, []);

  secondResponse.resolve({ marker: "new" });
  assert.deepEqual(await secondLoad, { applied: true, stale: false });
  assert.deepEqual(applied, [{ marker: "new" }]);
  coordinator.destroy();
});

test("profile coordinator coalesces scoring bursts and warns quietly when refresh fails", async () => {
  const timers = [];
  const patches = [];
  const profiles = [];
  const warnings = [];
  let fetchCount = 0;
  let rejectRefresh = false;
  const coordinator = tasks.createProfileCoordinator({
    fetchProfile: async () => {
      fetchCount += 1;
      if (rejectRefresh) throw new Error("profile unavailable");
      return { marker: fetchCount };
    },
    applyProfile: (profile) => profiles.push(profile),
    applyPatch: (patch) => patches.push(patch),
    warn: (error) => warnings.push(error.message),
    setTimer: (callback) => {
      const timer = { callback, active: true };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => { timer.active = false; },
  });

  coordinator.scheduleMutationRefresh({ hp: 49 });
  coordinator.scheduleMutationRefresh({ hp: 48 });
  assert.deepEqual(patches, [{ hp: 49 }, { hp: 48 }]);
  assert.equal(timers.filter((timer) => timer.active).length, 1);
  timers.find((timer) => timer.active).callback();
  await nextTurn();
  assert.equal(fetchCount, 1);
  assert.deepEqual(profiles, [{ marker: 1 }]);

  rejectRefresh = true;
  coordinator.scheduleMutationRefresh({ exp: 100 });
  timers.findLast((timer) => timer.active).callback();
  await nextTurn();
  assert.equal(fetchCount, 2);
  assert.deepEqual(warnings, ["profile unavailable"]);
  assert.deepEqual(profiles, [{ marker: 1 }]);
  coordinator.destroy();
});

test("confirmed scoring invokes profile synchronization without letting callback failure undo the task", async () => {
  const callbacks = [];
  const initial = fixtureTask({ value: 0, counterUp: 0 });
  const scored = fixtureTask({ value: 1, counterUp: 1, colorToken: "good" });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "GET") return { ok: true, tasks: [initial] };
      return { ok: true, task: scored, profilePatch: { exp: 4073, gold: 3848 } };
    },
    onMutationConfirmed: (detail) => {
      callbacks.push(detail);
      return Promise.reject(new Error("auxiliary profile refresh failed"));
    },
  });

  await controller.activate("habits");
  const result = await controller.scoreTask("habit", initial.id, "up");
  await nextTurn();

  assert.equal(result.counterUp, 1);
  assert.equal(controller.findTask(initial.id).value, 1);
  assert.equal(controller.state.pending.size, 0);
  assert.equal(callbacks.length, 1);
  assert.equal(callbacks[0].kind, "habit-score");
  assert.equal(callbacks[0].sequence, 1);
  assert.deepEqual(callbacks[0].profilePatch, { exp: 4073, gold: 3848 });
  controller.destroy();
});

test("confirmed score callbacks carry intent-ordered sequences even when responses finish out of order", async () => {
  const firstResponse = deferred();
  const secondResponse = deferred();
  const callbacks = [];
  const first = fixtureTask({ id: "first-score" });
  const second = fixtureTask({ id: "second-score" });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (requestPath, options) => {
      if (options.method === "GET") return { ok: true, tasks: [first, second] };
      return requestPath.includes("first-score") ? firstResponse.promise : secondResponse.promise;
    },
    onMutationConfirmed: (detail) => callbacks.push(detail),
  });

  await controller.activate("habits");
  const firstScore = controller.scoreTask("habit", first.id, "up");
  const secondScore = controller.scoreTask("habit", second.id, "up");
  secondResponse.resolve({
    ok: true,
    task: fixtureTask({ id: second.id, value: 1, counterUp: 1 }),
    profilePatch: { exp: 102 },
  });
  await secondScore;
  firstResponse.resolve({
    ok: true,
    task: fixtureTask({ id: first.id, value: 1, counterUp: 1 }),
    profilePatch: { exp: 101 },
  });
  await firstScore;

  assert.deepEqual(callbacks.map((detail) => ({
    taskId: detail.task.id,
    sequence: detail.sequence,
    exp: detail.profilePatch.exp,
  })), [
    { taskId: second.id, sequence: 2, exp: 102 },
    { taskId: first.id, sequence: 1, exp: 101 },
  ]);
  controller.destroy();
});

test("rapid duplicate score taps produce one mutation and one confirmed callback", async () => {
  const response = deferred();
  const initial = fixtureTask({ id: "rapid-score", value: 0, counterUp: 0 });
  let postCount = 0;
  let callbackCount = 0;
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "GET") return { ok: true, tasks: [initial] };
      postCount += 1;
      return response.promise;
    },
    onMutationConfirmed: () => { callbackCount += 1; },
  });

  await controller.activate("habits");
  const firstTap = controller.scoreTask("habit", initial.id, "up");
  const secondTap = controller.scoreTask("habit", initial.id, "up");
  assert.equal(await secondTap, null);
  assert.equal(postCount, 1);

  response.resolve({
    ok: true,
    task: fixtureTask({ id: "rapid-score", value: 1, counterUp: 1 }),
  });
  await firstTap;
  assert.equal(postCount, 1);
  assert.equal(callbackCount, 1);
  assert.equal(controller.state.pending.size, 0);
  controller.destroy();
});

test("delegated center and edge actions stay separated", async () => {
  const listeners = new Map();
  const dispatched = [];
  const root = {
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type) { listeners.delete(type); },
    dispatchEvent(event) { dispatched.push(event); return true; },
    querySelector() { return null; },
  };
  const originalCustomEvent = globalThis.CustomEvent;
  globalThis.CustomEvent = class FakeCustomEvent {
    constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
  };

  let posts = 0;
  const initial = fixtureTask({ id: "separate-actions" });
  const controller = tasks.createTaskController({
    root,
    requestJson: async (_path, options) => {
      if (options.method === "GET") return { ok: true, tasks: [initial] };
      posts += 1;
      return { ok: true, task: fixtureTask({ id: "separate-actions", value: 1, counterUp: 1 }) };
    },
  });

  try {
    await controller.activate("habits");
    const target = (action, direction) => ({
      disabled: false,
      dataset: {
        taskAction: action,
        taskType: "habit",
        taskId: initial.id,
        ...(direction ? { direction } : {}),
      },
      closest(selector) { return selector === "[data-task-action]" ? this : null; },
    });

    listeners.get("click")({ target: target("open") });
    await nextTurn();
    assert.equal(posts, 0);
    assert.deepEqual(dispatched.map((event) => event.type), ["miniapp:task-edit"]);

    listeners.get("click")({ target: target("score", "up") });
    await nextTurn();
    assert.equal(posts, 1);
    assert.deepEqual(dispatched.map((event) => event.type), ["miniapp:task-edit"]);
  } finally {
    controller.destroy();
    if (originalCustomEvent === undefined) delete globalThis.CustomEvent;
    else globalThis.CustomEvent = originalCustomEvent;
  }
});
