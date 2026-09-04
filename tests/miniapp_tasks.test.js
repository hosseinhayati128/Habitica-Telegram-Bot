"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const tasks = require("../static/miniapp/tasks.js");

function task(overrides = {}) {
  return {
    id: "task-1",
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
    dateCompleted: null,
    startDate: null,
    repeatDays: [],
    scheduleEditable: false,
    checklist: [],
    ...overrides,
  };
}

test("normalizeQuestLogSummary accepts bounded counts and rejects malformed data", () => {
  const payload = {
    ok: true,
    summary: {
      habits: { total: 4, completed: 3 },
      dailies: { total: 2, completed: 1 },
      todos: { total: 0, completed: 0 },
    },
  };
  assert.deepEqual(tasks.normalizeQuestLogSummary(payload), payload.summary);
  assert.throws(
    () => tasks.normalizeQuestLogSummary({
      ...payload,
      summary: { ...payload.summary, habits: { total: 1, completed: 2 } },
    }),
    /invalid/i,
  );
  assert.throws(
    () => tasks.normalizeQuestLogSummary({ ...payload, summary: { habits: payload.summary.habits } }),
    /invalid|incomplete/i,
  );
});

test("dedupeTasks preserves first position and applies final normalized value", () => {
  const result = tasks.dedupeTasks([
    task({ id: "a", text: "old" }),
    task({ id: "b", text: "second" }),
    task({ id: "a", text: "new" }),
  ]);

  assert.deepEqual(result.order, ["a", "b"]);
  assert.equal(result.byId.get("a").text, "new");
  assert.equal(result.byId.size, 2);
});

test("habit filters include a two-direction Habit in both directional views", () => {
  const positive = task({ id: "positive", up: true, down: false });
  const negative = task({ id: "negative", up: false, down: true });
  const both = task({ id: "both", up: true, down: true });

  assert.deepEqual(tasks.filterTasks("habit", [positive, negative, both], "positive").map((item) => item.id), ["positive", "both"]);
  assert.deepEqual(tasks.filterTasks("habit", [positive, negative, both], "negative").map((item) => item.id), ["negative", "both"]);
  assert.deepEqual(tasks.visibleHabitDirections(positive), ["up"]);
  assert.deepEqual(tasks.visibleHabitDirections(negative), ["down"]);
  assert.deepEqual(tasks.visibleHabitDirections(both), ["up", "down"]);
});

test("Daily grouping and filters distinguish due, not due, and completed", () => {
  const due = task({ id: "due", type: "daily", dueToday: true });
  const optional = task({ id: "optional", type: "daily", dueToday: false });
  const completed = task({ id: "done", type: "daily", dueToday: true, completed: true });
  const list = [due, optional, completed];

  assert.deepEqual(tasks.filterTasks("daily", list, "due").map((item) => item.id), ["due"]);
  assert.deepEqual(tasks.filterTasks("daily", list, "completed").map((item) => item.id), ["done"]);
  const grouped = tasks.groupDailyTasks(list);
  assert.deepEqual(grouped.due.map((item) => item.id), ["due"]);
  assert.deepEqual(grouped.notDue.map((item) => item.id), ["optional"]);
  assert.deepEqual(grouped.completed.map((item) => item.id), ["done"]);
});

test("Todo filters keep active and completed data distinct", () => {
  const active = task({ id: "active", type: "todo", completed: false });
  const completed = task({ id: "done", type: "todo", completed: true });
  assert.deepEqual(tasks.filterTasks("todo", [active, completed], "active").map((item) => item.id), ["active"]);
  assert.deepEqual(tasks.filterTasks("todo", [active, completed], "completed").map((item) => item.id), ["done"]);
});

test("Todo smart views and list views classify locally with Inbox-safe malformed tags", () => {
  const lists = [
    { id: "work", name: "Work", order: 0 },
    { id: "personal", name: "Personal", order: 1 },
  ];
  const fixture = [
    task({ id: "inbox", type: "todo", tagIds: [], date: null }),
    task({ id: "today", type: "todo", tagIds: ["work"], date: "2026-08-24" }),
    task({ id: "future", type: "todo", tagIds: ["personal"], date: "2026-08-26" }),
    task({ id: "late", type: "todo", tagIds: ["work"], date: "2026-08-20" }),
    task({ id: "unknown", type: "todo", tagIds: ["deleted"], date: null }),
    task({ id: "multiple", type: "todo", tagIds: ["work", "personal"], date: null }),
  ].map(tasks.normalizeTask);

  assert.deepEqual(tasks.todoTasksForView(fixture, "inbox", lists, "2026-08-24").map((item) => item.id), [
    "inbox", "unknown", "multiple",
  ]);
  assert.deepEqual(tasks.todoTasksForView(fixture, "today", lists, "2026-08-24").map((item) => item.id), ["today"]);
  assert.deepEqual(tasks.todoTasksForView(fixture, "upcoming", lists, "2026-08-24").map((item) => item.id), ["future"]);
  assert.deepEqual(tasks.todoTasksForView(fixture, "overdue", lists, "2026-08-24").map((item) => item.id), ["late"]);
  assert.deepEqual(tasks.todoTasksForView(fixture, "list:work", lists, "2026-08-24").map((item) => item.id), ["today", "late"]);
  assert.equal(tasks.primaryTodoListId(fixture.at(-1), lists), null);
});

test("All Todos groups Inbox first, then Habitica tag order, preserving task order within groups", () => {
  const lists = [
    { id: "personal", name: "Personal", order: 0 },
    { id: "work", name: "Work", order: 1 },
  ];
  const fixture = [
    task({ id: "work-1", type: "todo", tagIds: ["work"] }),
    task({ id: "inbox-1", type: "todo", tagIds: [] }),
    task({ id: "personal-1", type: "todo", tagIds: ["personal"] }),
    task({ id: "work-2", type: "todo", tagIds: ["work"] }),
  ].map(tasks.normalizeTask);
  assert.deepEqual(tasks.groupAllTodos(fixture, lists).map((group) => ({
    label: group.label,
    ids: group.tasks.map((item) => item.id),
  })), [
    { label: "Inbox", ids: ["inbox-1"] },
    { label: "Personal", ids: ["personal-1"] },
    { label: "Work", ids: ["work-1", "work-2"] },
  ]);
});

test("task editor validation returns a compact whitelisted Habit payload", () => {
  const result = tasks.validateTaskDraft({
    type: "habit",
    text: "  Read  ",
    notes: "  A chapter  ",
    priority: "1.5",
    up: true,
    down: false,
    ignoredCredential: "must-not-pass",
  });

  assert.equal(result.ok, true);
  assert.deepEqual(result.value, {
    type: "habit",
    text: "Read",
    notes: "A chapter",
    priority: 1.5,
    up: true,
    down: false,
  });
  assert.equal("ignoredCredential" in result.value, false);
});

test("edit validation preserves the editor snapshot revision", () => {
  const result = tasks.validateTaskDraft({
    type: "todo",
    text: "Snapshot title",
    notes: "",
    priority: 1,
    revision: "a".repeat(32),
    date: "",
    checklist: [],
  });
  assert.equal(result.ok, true);
  assert.equal(result.value.revision, "a".repeat(32));
});

test("task editor validation rejects invalid common, Habit, Daily, and Todo fields", () => {
  const habit = tasks.validateTaskDraft({ type: "habit", text: " ", notes: "", priority: 9, up: false, down: false });
  assert.equal(habit.ok, false);
  assert.ok(habit.errors.text);
  assert.ok(habit.errors.priority);
  assert.ok(habit.errors.directions);

  const daily = tasks.validateTaskDraft({
    type: "daily",
    text: "Daily",
    notes: "",
    priority: 1,
    scheduleEditable: true,
    repeatDays: ["mo", "bogus"],
    startDate: "2025-02-29",
    checklist: [],
  });
  assert.equal(daily.ok, false);
  assert.ok(daily.errors.repeatDays);
  assert.ok(daily.errors.startDate);

  const todo = tasks.validateTaskDraft({
    type: "todo",
    text: "Todo",
    notes: "",
    priority: 1,
    date: "2026-04-31",
    checklist: [{ text: " " }],
  });
  assert.equal(todo.ok, false);
  assert.ok(todo.errors.date);
  assert.ok(todo.errors["checklist.0"]);
});

test("Daily validation omits weekly fields when an advanced schedule is not editable", () => {
  const result = tasks.validateTaskDraft({
    type: "daily",
    text: "Advanced Daily",
    notes: "",
    priority: 1,
    scheduleEditable: false,
    repeatDays: ["bogus"],
    startDate: "not-a-date",
    checklist: [],
  });

  assert.equal(result.ok, true);
  assert.equal("repeatDays" in result.value, false);
  assert.equal("startDate" in result.value, false);
});

test("oversized titles, notes, and checklist data are rejected with injected limits", () => {
  const result = tasks.validateTaskDraft({
    type: "todo",
    text: "12345",
    notes: "12345",
    priority: 1,
    date: "",
    checklist: [{ text: "12345" }, { text: "second" }],
  }, { title: 4, notes: 4, checklistItems: 1, checklistText: 4 });

  assert.equal(result.ok, false);
  assert.ok(result.errors.text);
  assert.ok(result.errors.notes);
  assert.ok(result.errors.checklist);
});

test("older and mutation-raced list responses cannot overwrite collection state", () => {
  const collection = tasks.createCollection();
  const older = tasks.beginCollectionRequest(collection);
  const newer = tasks.beginCollectionRequest(collection);

  assert.deepEqual(tasks.applyCollectionResponse(collection, newer, [task({ text: "new" })]), { applied: true });
  assert.deepEqual(tasks.applyCollectionResponse(collection, older, [task({ text: "old" })]), {
    applied: false,
    reason: "stale_request",
  });
  assert.equal(tasks.collectionTasks(collection)[0].text, "new");

  const raced = tasks.beginCollectionRequest(collection, { refresh: true });
  tasks.markCollectionChanged(collection);
  assert.deepEqual(tasks.applyCollectionResponse(collection, raced, [task({ text: "stale refresh" })]), {
    applied: false,
    reason: "concurrent_mutation",
  });
  assert.equal(tasks.collectionTasks(collection)[0].text, "new");
  assert.equal(collection.needsRefresh, true);
});

test("pending registry suppresses duplicate operations without blocking unrelated keys", () => {
  const pending = new Map();
  const first = tasks.pendingKey("score", "task-1");
  const other = tasks.pendingKey("checklist", "task-1", "item-2");

  assert.equal(tasks.claimPending(pending, first, { kind: "score", taskId: "task-1" }), true);
  assert.equal(tasks.claimPending(pending, first, { kind: "score", taskId: "task-1" }), false);
  assert.equal(tasks.claimPending(pending, other, { kind: "checklist", taskId: "task-1", itemId: "item-2" }), true);
  assert.equal(pending.size, 2);
  assert.equal(tasks.releasePending(pending, first), true);
  assert.equal(tasks.claimPending(pending, first), true);
});

test("setSafeText assigns literal untrusted task content without touching innerHTML", () => {
  let stored = "";
  const fakeNode = {
    get textContent() { return stored; },
    set textContent(value) { stored = value; },
    set innerHTML(_value) { throw new Error("innerHTML must not be used"); },
  };
  const malicious = '<img src=x onerror="globalThis.pwned=true">';
  tasks.setSafeText(fakeNode, malicious);
  assert.equal(stored, malicious);
  assert.equal(globalThis.pwned, undefined);
});

test("controller lazy-loads collections and can refresh one when its tab is reopened", async () => {
  const calls = [];
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (path, options) => {
      calls.push({ path, options });
      const type = new URL(`https://test.invalid${path}`).searchParams.get("type");
      return { ok: true, tasks: [task({ id: `${type}-1`, type })] };
    },
  });

  await controller.activate("habits");
  await controller.activate("habits");
  await controller.activate("dailies");
  await controller.activate("habits", { refresh: true });
  await controller.refresh("habits");

  assert.deepEqual(calls.map((call) => call.path), [
    "/miniapp/api/tasks?type=habit",
    "/miniapp/api/tasks?type=daily",
    "/miniapp/api/tasks?type=habit",
    "/miniapp/api/tasks?type=habit",
  ]);
  assert.ok(calls.every((call) => call.options.method === "GET"));
  controller.destroy();
});

test("Todo view switching is local and completed Todos load only when first opened", async () => {
  const calls = [];
  const active = [task({ id: "todo-1", type: "todo", tagIds: ["work"] })];
  const completed = [task({ id: "todo-done", type: "todo", completed: true, tagIds: ["work"] })];
  const controller = tasks.createTaskController({
    root: null,
    today: "2026-08-24",
    requestJson: async (path, options) => {
      calls.push({ path, options });
      if (path.endsWith("/todo-lists")) {
        return { ok: true, lists: [{ id: "work", name: "Work", order: 0 }], ordinaryTags: [] };
      }
      return { ok: true, tasks: path.includes("completed=true") ? completed : active };
    },
  });

  await controller.activate("todos");
  await controller.setTodoView("all");
  await controller.setTodoView("list:work");
  await controller.setTodoView("today");
  assert.equal(calls.length, 2);
  await controller.setTodoView("completed");
  await controller.setTodoView("inbox");
  await controller.setTodoView("completed");
  assert.equal(calls.filter((call) => call.path.includes("completed=true")).length, 1);
  assert.equal(calls.filter((call) => call.path.endsWith("/todo-lists")).length, 1);
  controller.destroy();
});

test("confirmed list deletion moves locally classified tasks to Inbox without deleting them", async () => {
  const active = task({ id: "todo-work", type: "todo", tagIds: ["work"] });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (path, options) => {
      if (path.endsWith("/todo-lists") && options.method === "GET") {
        return { ok: true, lists: [{ id: "work", name: "Work", order: 0 }], ordinaryTags: [] };
      }
      if (path.includes("/todo-lists/work") && options.method === "DELETE") {
        return { ok: true, deleted: { id: "work", name: "Work" } };
      }
      return { ok: true, tasks: [active] };
    },
  });

  await controller.activate("todos");
  await controller.setTodoView("list:work");
  await controller.deleteTodoList("work");
  assert.equal(controller.findTask("todo-work").id, "todo-work");
  assert.equal(controller.state.todoView, "inbox");
  assert.equal(tasks.primaryTodoListId(controller.findTask("todo-work"), controller.state.todoLists.lists), null);
  controller.destroy();
});

test("controller uses encoded mutation routes and injected JSON request bodies", async () => {
  const calls = [];
  const id = "task/with path";
  const initial = task({ id, type: "habit", up: true, down: false });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (path, options) => {
      calls.push({ path, options });
      if (options.method === "GET") return { ok: true, tasks: [initial] };
      return { ok: true, task: { ...initial, value: 1, counterUp: 1 } };
    },
  });

  await controller.activate("habits");
  await controller.scoreTask("habit", id, "up");

  assert.equal(calls[1].path, "/miniapp/api/tasks/task%2Fwith%20path/score");
  assert.deepEqual(calls[1].options, { method: "POST", body: { direction: "up" } });
  controller.destroy();
});

test("checklist scoring sends the expected desired completion state", async () => {
  const calls = [];
  const initial = task({
    id: "daily-1",
    type: "daily",
    checklist: [{ id: "item/1", text: "Step", completed: false }],
  });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (path, options) => {
      calls.push({ path, options });
      if (options.method === "GET") return { ok: true, tasks: [initial] };
      return {
        ok: true,
        task: { ...initial, checklist: [{ id: "item/1", text: "Step", completed: true }] },
      };
    },
  });

  await controller.activate("dailies");
  await controller.scoreChecklist("daily", "daily-1", "item/1", "up");

  assert.equal(calls[1].path, "/miniapp/api/tasks/daily-1/checklist/item%2F1/score");
  assert.deepEqual(calls[1].options, { method: "POST", body: { completed: true } });
  controller.destroy();
});

test("a definite preflight Habitica failure rolls back and releases the control", async () => {
  const initial = task({ id: "habit-1", value: 0, counterUp: 0 });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "POST") {
        const error = new Error("lost response");
        error.code = "habitica_unavailable";
        throw error;
      }
      return { ok: true, tasks: [initial] };
    },
  });

  await controller.activate("habits");
  await assert.rejects(controller.scoreTask("habit", "habit-1", "up"));
  assert.equal(controller.state.pending.size, 0);

  assert.equal(controller.findTask("habit-1").counterUp, 0);
  controller.destroy();
});

test("an ambiguous Habit score stays session-blocked even after an immediate refresh", async () => {
  let posts = 0;
  const initial = task({ id: "habit-guard", value: 0, counterUp: 0 });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "POST") {
        posts += 1;
        const error = new Error("lost response");
        error.code = "outcome_unknown";
        error.reconcileRequired = true;
        throw error;
      }
      return { ok: true, tasks: [initial] };
    },
  });

  await controller.activate("habits");
  await assert.rejects(controller.scoreTask("habit", "habit-guard", "up"));
  assert.equal(posts, 1);
  assert.equal(controller.state.pending.size, 1);
  assert.equal(await controller.scoreTask("habit", "habit-guard", "up"), null);
  assert.equal(posts, 1);
  controller.destroy();
});

test("an ambiguous create stays blocked because a list cannot identify a late duplicate", async () => {
  let posts = 0;
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "POST") {
        posts += 1;
        const error = new Error("lost response");
        error.code = "outcome_unknown";
        error.reconcileRequired = true;
        throw error;
      }
      return { ok: true, tasks: [] };
    },
  });

  await controller.activate("todos");
  const draft = { type: "todo", text: "Could arrive late", notes: "", priority: 1, date: "", checklist: [] };
  await assert.rejects(controller.createTask(draft));
  assert.equal(controller.state.pending.size, 1);
  await assert.rejects(controller.createTask(draft), /already in progress/i);
  assert.equal(posts, 1);
  controller.destroy();
});

test("an ambiguous edit stays blocked even when a refresh succeeds", async () => {
  let patches = 0;
  const initial = task({ id: "todo-guard", type: "todo", revision: "a".repeat(32) });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "PATCH") {
        patches += 1;
        const error = new Error("lost response");
        error.code = "outcome_unknown";
        error.reconcileRequired = true;
        throw error;
      }
      return { ok: true, tasks: [initial] };
    },
  });

  await controller.activate("todos");
  const draft = { ...initial, text: "Changed" };
  await assert.rejects(controller.updateTask("todo-guard", draft));
  assert.equal(controller.state.pending.size, 1);
  await assert.rejects(controller.updateTask("todo-guard", draft), /wait/i);
  assert.equal(patches, 1);
  controller.destroy();
});

test("reloadRequired after a confirmed score reconciles before releasing the control", async () => {
  let scored = false;
  const initial = task({ id: "habit-1", value: 0, counterUp: 0 });
  const confirmed = task({ id: "habit-1", value: 2, counterUp: 1 });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "POST") {
        scored = true;
        return { ok: true, task: initial, reloadRequired: true };
      }
      return { ok: true, tasks: [scored ? confirmed : initial] };
    },
  });

  await controller.activate("habits");
  const result = await controller.scoreTask("habit", "habit-1", "up");

  assert.equal(result.counterUp, 1);
  assert.equal(controller.state.pending.size, 0);
  assert.equal(controller.findTask("habit-1").value, 2);
  controller.destroy();
});

test("Habit counters normalize nonnegative integers and retain only supported reset periods", () => {
  const normalized = tasks.normalizeTask(task({
    counterUp: 2.9,
    counterDown: -3,
    counterFrequency: "weekly",
  }));
  assert.equal(normalized.counterUp, 2);
  assert.equal(normalized.counterDown, null);
  assert.equal(normalized.counterFrequency, "weekly");

  const malformed = tasks.normalizeTask(task({
    counterUp: "2",
    counterDown: Infinity,
    counterFrequency: "yearly",
  }));
  assert.equal(malformed.counterUp, null);
  assert.equal(malformed.counterDown, null);
  assert.equal(malformed.counterFrequency, null);
});

test("Habit counter summaries respect scoring directions, zero suppression, and actual period", () => {
  assert.deepEqual(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: true,
    down: true,
    counterUp: 2,
    counterDown: 1,
    counterFrequency: "daily",
  }))), {
    text: "+2 | −1",
    ariaLabel: "2 positive scores and 1 negative score today",
    frequency: "daily",
    up: 2,
    down: 1,
  });
  assert.equal(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: true,
    down: true,
    counterUp: 0,
    counterDown: 0,
    counterFrequency: "daily",
  }))), null);
  assert.deepEqual(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: true,
    down: true,
    counterUp: 2,
    counterDown: 0,
    counterFrequency: "daily",
  }))), {
    text: "+2 | −0",
    ariaLabel: "2 positive scores and 0 negative scores today",
    frequency: "daily",
    up: 2,
    down: 0,
  });
  assert.equal(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: true,
    down: false,
    counterUp: 0,
    counterFrequency: "weekly",
  }))), null);
  assert.equal(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: true,
    down: false,
    counterUp: 3,
    counterDown: 99,
    counterFrequency: "weekly",
  }))).text, "+3");
  assert.equal(tasks.habitCounterSummary(tasks.normalizeTask(task({
    up: false,
    down: true,
    counterUp: 99,
    counterDown: 4,
    counterFrequency: "monthly",
  }))).ariaLabel, "4 negative scores this month");
});

test("day-refresh-required score rolls back completion and opens the gate without reconciliation GET", async () => {
  let gets = 0;
  let gateCalls = 0;
  const initial = task({ id: "daily-gated", type: "daily", dueToday: true, completed: false });
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async (_path, options) => {
      if (options.method === "GET") {
        gets += 1;
        return { ok: true, tasks: [initial] };
      }
      const error = new Error("Review yesterday first");
      error.code = "day_refresh_required";
      error.status = 409;
      throw error;
    },
    onDayRefreshRequired: () => { gateCalls += 1; },
  });

  await controller.activate("dailies");
  await assert.rejects(controller.scoreTask("daily", "daily-gated", "up"), /yesterday/i);
  assert.equal(controller.findTask("daily-gated").completed, false);
  assert.equal(controller.state.pending.size, 0);
  assert.equal(gets, 1, "a definite 409 must not trigger an old-day reconciliation fetch");
  assert.equal(gateCalls, 1);
  controller.destroy();
});

test("post-refresh invalidation clears stale counters before authoritative list reload", async () => {
  let counter = 5;
  const controller = tasks.createTaskController({
    root: null,
    requestJson: async () => ({
      ok: true,
      tasks: [task({ id: "daily-counter", counterUp: counter, counterFrequency: "daily" })],
    }),
  });
  await controller.activate("habits");
  assert.equal(controller.findTask("daily-counter").counterUp, 5);
  controller.invalidate(["habit"]);
  assert.equal(controller.findTask("daily-counter"), null);
  counter = 0;
  assert.equal(await controller.refreshTypes(["habit"]), true);
  assert.equal(controller.findTask("daily-counter").counterUp, 0);
  controller.destroy();
});
