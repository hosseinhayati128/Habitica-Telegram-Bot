(function taskUiModule(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && typeof root === "object") root.HabiticaTaskUI = api;
})(typeof globalThis === "object" ? globalThis : this, function buildTaskUi() {
  "use strict";

  const TYPES = Object.freeze(["habit", "daily", "todo"]);
  const TYPE_SET = new Set(TYPES);
  const DAY_KEYS = Object.freeze(["su", "m", "t", "w", "th", "f", "s"]);
  const DAY_SET = new Set(DAY_KEYS);
  const PRIORITIES = Object.freeze([0.1, 1, 1.5, 2]);
  const PRIORITY_LABELS = Object.freeze({
    "0.1": "Trivial",
    "1": "Easy",
    "1.5": "Medium",
    "2": "Hard",
  });
  const DEFAULT_LIMITS = Object.freeze({
    title: 500,
    notes: 10000,
    checklistItems: 100,
    checklistText: 500,
  });
  const VIEW_TO_TYPE = Object.freeze({ habits: "habit", dailies: "daily", todos: "todo" });
  const TYPE_TO_VIEW = Object.freeze({ habit: "habits", daily: "dailies", todo: "todos" });
  const FILTERS = Object.freeze({
    habit: Object.freeze(["all", "positive", "negative"]),
    daily: Object.freeze(["due", "all", "completed"]),
    todo: Object.freeze(["active", "completed"]),
  });
  const TASK_COLOR_TOKENS = Object.freeze([
    "worst",
    "worse",
    "bad",
    "neutral",
    "good",
    "better",
    "best",
  ]);
  const TASK_COLOR_SET = new Set(TASK_COLOR_TOKENS);

  function canonicalType(value) {
    const aliases = {
      habit: "habit",
      habits: "habit",
      daily: "daily",
      dailies: "daily",
      todo: "todo",
      todos: "todo",
    };
    return typeof value === "string" ? aliases[value.toLowerCase()] || null : null;
  }

  function finiteNumber(value, fallback = null) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  }

  function normalizeHabitCounter(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? Math.floor(value)
      : null;
  }

  function normalizeCounterFrequency(value) {
    return ["daily", "weekly", "monthly"].includes(value) ? value : null;
  }

  function habitCounterSummary(task) {
    if (!task || task.type !== "habit") return null;
    const frequency = normalizeCounterFrequency(task.counterFrequency);
    if (!frequency) return null;
    const up = task.up === true ? normalizeHabitCounter(task.counterUp) ?? 0 : null;
    const down = task.down === true ? normalizeHabitCounter(task.counterDown) ?? 0 : null;
    if ((up ?? 0) === 0 && (down ?? 0) === 0) return null;

    const pieces = [];
    // Once a two-direction Habit has activity, keep both configured sides in
    // view (for example +2 | −0).  The entire display remains hidden when all
    // supported counters are zero.
    if (up !== null && (up > 0 || (down !== null && down > 0))) pieces.push(`+${up}`);
    if (down !== null && (down > 0 || (up !== null && up > 0))) pieces.push(`−${down}`);
    if (!pieces.length) return null;
    const period = { daily: "today", weekly: "this week", monthly: "this month" }[frequency];
    const descriptions = [];
    if (up !== null && pieces.some((piece) => piece.startsWith("+"))) {
      descriptions.push(`${up} positive ${up === 1 ? "score" : "scores"}`);
    }
    if (down !== null && pieces.some((piece) => piece.startsWith("−"))) {
      descriptions.push(`${down} negative ${down === 1 ? "score" : "scores"}`);
    }
    return {
      text: pieces.join(" | "),
      ariaLabel: `${descriptions.join(" and ")} ${period}`,
      frequency,
      up,
      down,
    };
  }

  function normalizeQuestLogSummary(payload) {
    if (!payload || payload.ok !== true || !payload.summary || typeof payload.summary !== "object") {
      throw new TypeError("Quest log response is incomplete.");
    }
    const summary = {};
    ["habits", "dailies", "todos"].forEach((key) => {
      const row = payload.summary[key];
      if (
        !row
        || !Number.isSafeInteger(row.total)
        || row.total < 0
        || !Number.isSafeInteger(row.completed)
        || row.completed < 0
        || row.completed > row.total
      ) {
        throw new TypeError("Quest log counts are invalid.");
      }
      summary[key] = Object.freeze({ total: row.total, completed: row.completed });
    });
    return Object.freeze(summary);
  }

  // Mirrors Habitica's current getTaskColor thresholds. Invalid values are
  // deliberately neutral instead of falling through to the strongest color.
  function taskColorToken(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "neutral";
    if (value < -20) return "worst";
    if (value < -10) return "worse";
    if (value < -1) return "bad";
    if (value < 1) return "neutral";
    if (value < 5) return "good";
    if (value < 10) return "better";
    return "best";
  }

  function displayText(value, fallback = "") {
    return typeof value === "string" ? value : fallback;
  }

  function setSafeText(node, value, fallback = "") {
    if (!node || !("textContent" in node)) throw new TypeError("A text-capable node is required.");
    node.textContent = displayText(value, fallback);
    return node;
  }

  function normalizeRepeatDays(value) {
    if (Array.isArray(value)) {
      const seen = new Set();
      return value.filter((day) => DAY_SET.has(day) && !seen.has(day) && seen.add(day));
    }
    if (value && typeof value === "object") {
      return DAY_KEYS.filter((day) => value[day] === true);
    }
    return [];
  }

  function normalizeChecklist(value) {
    if (value == null) return [];
    if (!Array.isArray(value)) throw new TypeError("Task checklist must be an array.");
    return value.map((item) => {
      if (!item || typeof item !== "object") throw new TypeError("Invalid checklist item.");
      if (typeof item.id !== "string" || !item.id) throw new TypeError("Checklist item id is missing.");
      if (typeof item.text !== "string") throw new TypeError("Checklist item text is missing.");
      return { id: item.id, text: item.text, completed: item.completed === true };
    });
  }

  /**
   * Validate the browser-facing, already-whitelisted task shape. This is not a
   * substitute for server validation; it keeps malformed responses out of UI state.
   */
  function normalizeTask(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new TypeError("Task response must be an object.");
    }
    const type = canonicalType(raw.type);
    if (!type || typeof raw.id !== "string" || !raw.id) {
      throw new TypeError("Task response is missing its type or id.");
    }
    if (typeof raw.text !== "string") throw new TypeError("Task response is missing its title.");

    const priority = PRIORITIES.includes(raw.priority) ? raw.priority : 1;
    const value = finiteNumber(raw.value);
    const colorToken = TASK_COLOR_SET.has(raw.colorToken)
      ? raw.colorToken
      : taskColorToken(value);
    return {
      id: raw.id,
      type,
      text: raw.text,
      notes: displayText(raw.notes),
      priority,
      value,
      colorToken,
      up: raw.up === true,
      down: raw.down === true,
      counterUp: normalizeHabitCounter(raw.counterUp),
      counterDown: normalizeHabitCounter(raw.counterDown),
      counterFrequency: normalizeCounterFrequency(raw.counterFrequency ?? raw.frequency),
      completed: raw.completed === true,
      dueToday: raw.dueToday === true,
      streak: finiteNumber(raw.streak),
      date: typeof raw.date === "string" && raw.date ? raw.date : null,
      dateCompleted: typeof raw.dateCompleted === "string" && raw.dateCompleted ? raw.dateCompleted : null,
      startDate: typeof raw.startDate === "string" && raw.startDate ? raw.startDate : null,
      repeatDays: normalizeRepeatDays(raw.repeatDays == null ? raw.repeat : raw.repeatDays),
      scheduleEditable: raw.scheduleEditable === true,
      checklist: normalizeChecklist(raw.checklist),
      revision: typeof raw.revision === "string" ? raw.revision : "",
      canEdit: raw.canEdit !== false,
      canDelete: raw.canDelete !== false,
    };
  }

  function cloneTask(task) {
    return {
      ...task,
      repeatDays: [...task.repeatDays],
      checklist: task.checklist.map((item) => ({ ...item })),
    };
  }

  /** Keep the first occurrence's position and the final occurrence's data. */
  function dedupeTasks(tasks) {
    if (!Array.isArray(tasks)) throw new TypeError("Task list must be an array.");
    const byId = new Map();
    const order = [];
    tasks.forEach((raw) => {
      const task = normalizeTask(raw);
      if (!byId.has(task.id)) order.push(task.id);
      byId.set(task.id, task);
    });
    return { byId, order };
  }

  function createCollection() {
    return {
      byId: new Map(),
      order: [],
      loaded: false,
      loading: false,
      refreshing: false,
      error: null,
      requestSequence: 0,
      appliedSequence: 0,
      revision: 0,
      needsRefresh: false,
    };
  }

  function collectionTasks(collection) {
    return collection.order.map((id) => collection.byId.get(id)).filter(Boolean);
  }

  function beginCollectionRequest(collection, { refresh = false } = {}) {
    const token = {
      sequence: collection.requestSequence + 1,
      revision: collection.revision,
    };
    collection.requestSequence = token.sequence;
    collection.loading = !collection.loaded;
    collection.refreshing = collection.loaded && refresh;
    collection.error = null;
    return token;
  }

  function markCollectionChanged(collection) {
    collection.revision += 1;
  }

  function applyCollectionResponse(collection, token, rawTasks) {
    if (!token || token.sequence !== collection.requestSequence) {
      return { applied: false, reason: "stale_request" };
    }
    if (token.revision !== collection.revision) {
      collection.loading = false;
      collection.refreshing = false;
      collection.needsRefresh = true;
      return { applied: false, reason: "concurrent_mutation" };
    }

    const normalized = dedupeTasks(rawTasks);
    collection.byId = normalized.byId;
    collection.order = normalized.order;
    collection.loaded = true;
    collection.loading = false;
    collection.refreshing = false;
    collection.error = null;
    collection.needsRefresh = false;
    collection.appliedSequence = token.sequence;
    return { applied: true };
  }

  function failCollectionRequest(collection, token, error) {
    if (!token || token.sequence !== collection.requestSequence) return false;
    collection.loading = false;
    collection.refreshing = false;
    if (token.revision !== collection.revision) {
      collection.needsRefresh = true;
      return false;
    }
    collection.error = error || new Error("Task request failed.");
    return true;
  }

  function upsertCollectionTask(collection, task, { append = true } = {}) {
    const normalized = normalizeTask(task);
    if (!collection.byId.has(normalized.id)) {
      if (append) collection.order.push(normalized.id);
      else collection.order.unshift(normalized.id);
    }
    collection.byId.set(normalized.id, normalized);
    return normalized;
  }

  function removeCollectionTask(collection, taskId) {
    const existed = collection.byId.delete(taskId);
    if (existed) collection.order = collection.order.filter((id) => id !== taskId);
    return existed;
  }

  function visibleHabitDirections(task) {
    const directions = [];
    if (task && task.up === true) directions.push("up");
    if (task && task.down === true) directions.push("down");
    return directions;
  }

  function filterTasks(typeValue, tasks, filter) {
    const type = canonicalType(typeValue);
    if (!type || !Array.isArray(tasks)) return [];
    if (type === "habit") {
      if (filter === "positive") return tasks.filter((task) => task.up === true);
      if (filter === "negative") return tasks.filter((task) => task.down === true);
      return [...tasks];
    }
    if (type === "daily") {
      if (filter === "completed") return tasks.filter((task) => task.completed === true);
      if (filter === "due") return tasks.filter((task) => task.dueToday === true && task.completed !== true);
      return [...tasks];
    }
    if (filter === "completed") return tasks.filter((task) => task.completed === true);
    return tasks.filter((task) => task.completed !== true);
  }

  function groupDailyTasks(tasks) {
    const groups = { due: [], notDue: [], completed: [] };
    (Array.isArray(tasks) ? tasks : []).forEach((task) => {
      if (task.completed === true) groups.completed.push(task);
      else if (task.dueToday === true) groups.due.push(task);
      else groups.notDue.push(task);
    });
    return groups;
  }

  function difficultyLabel(priority) {
    return PRIORITY_LABELS[String(priority)] || "Easy";
  }

  function compactTaskMetadata(task) {
    if (!task || typeof task !== "object") return [];
    const metadata = [];
    if (Array.isArray(task.checklist) && task.checklist.length) {
      const completed = task.checklist.filter((item) => item.completed === true).length;
      metadata.push(`${completed}/${task.checklist.length} checklist`);
    }
    if (task.type === "daily") {
      if (task.completed === true) metadata.push("Completed today");
      else if (task.dueToday === true) metadata.push("Due today");
      else metadata.push("Not due today");
      if (typeof task.streak === "number" && Number.isFinite(task.streak) && task.streak > 0) {
        metadata.push(`${Math.floor(task.streak)} day streak`);
      }
    }
    if (task.type === "todo" && task.date) metadata.push(`Due ${task.date}`);
    return metadata;
  }

  function quickAddDefaults(typeValue, text = "") {
    const type = canonicalType(typeValue);
    if (!type) throw new TypeError("Quick-add task type is invalid.");
    const draft = { type, text: displayText(text), notes: "", priority: 1 };
    if (type === "habit") Object.assign(draft, { up: true, down: true });
    if (type === "daily") Object.assign(draft, {
      scheduleEditable: true,
      repeatDays: [...DAY_KEYS],
      startDate: "",
      checklist: [],
    });
    if (type === "todo") Object.assign(draft, { date: null, checklist: [] });
    return draft;
  }

  function isIsoDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [year, month, day] = value.split("-").map(Number);
    if (month < 1 || month > 12 || day < 1) return false;
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return day <= days[month - 1];
  }

  function validateTaskDraft(rawDraft, limitsValue = DEFAULT_LIMITS) {
    const draft = rawDraft && typeof rawDraft === "object" ? rawDraft : {};
    const limits = { ...DEFAULT_LIMITS, ...(limitsValue || {}) };
    const errors = {};
    const type = canonicalType(draft.type);
    const text = typeof draft.text === "string" ? draft.text.trim() : "";
    const notes = typeof draft.notes === "string" ? draft.notes.trim() : "";
    const priority = typeof draft.priority === "string" && draft.priority.trim()
      ? Number(draft.priority)
      : draft.priority;

    if (!type || !TYPE_SET.has(type)) errors.type = "Choose a supported task type.";
    if (!text) errors.text = "Title is required.";
    else if (text.length > limits.title) errors.text = `Title must be ${limits.title} characters or fewer.`;
    if (notes.length > limits.notes) errors.notes = `Notes must be ${limits.notes} characters or fewer.`;
    if (!PRIORITIES.includes(priority)) errors.priority = "Choose a valid difficulty.";

    const checklist = [];
    if (type === "daily" || type === "todo") {
      if (!Array.isArray(draft.checklist)) {
        errors.checklist = "Checklist data is invalid.";
      } else if (draft.checklist.length > limits.checklistItems) {
        errors.checklist = `Use no more than ${limits.checklistItems} checklist items.`;
      } else {
        draft.checklist.forEach((rawItem, index) => {
          if (!rawItem || typeof rawItem !== "object" || typeof rawItem.text !== "string") {
            errors.checklist = "Checklist data is invalid.";
            return;
          }
          const itemText = rawItem.text.trim();
          if (!itemText) errors[`checklist.${index}`] = "Checklist item text is required.";
          else if (itemText.length > limits.checklistText) {
            errors[`checklist.${index}`] = `Checklist items must be ${limits.checklistText} characters or fewer.`;
          }
          const item = { text: itemText, completed: rawItem.completed === true };
          if (typeof rawItem.id === "string" && rawItem.id) item.id = rawItem.id;
          checklist.push(item);
        });
      }
    } else if (draft.checklist != null && (!Array.isArray(draft.checklist) || draft.checklist.length)) {
      errors.checklist = "Habits do not support checklists.";
    }

    const value = { type, text, notes, priority };
    if (typeof draft.revision === "string" && draft.revision) value.revision = draft.revision;
    if (type === "habit") {
      value.up = draft.up === true;
      value.down = draft.down === true;
      if (!value.up && !value.down) errors.directions = "Enable positive, negative, or both.";
    }
    if (type === "daily") {
      // Advanced Habitica schedules are intentionally opaque to this milestone.
      // Omitting schedule fields on edit preserves them upstream instead of
      // flattening them into the simple weekly editor.
      if (draft.scheduleEditable === true) {
        value.repeatDays = normalizeRepeatDays(draft.repeatDays);
        if (!Array.isArray(draft.repeatDays) || value.repeatDays.length !== draft.repeatDays.length || !value.repeatDays.length) {
          errors.repeatDays = "Choose at least one valid repeat day.";
        }
        const startDate = typeof draft.startDate === "string" ? draft.startDate.trim() : "";
        if (startDate && !isIsoDate(startDate)) errors.startDate = "Enter a valid start date.";
        if (startDate) value.startDate = startDate;
      }
      value.checklist = checklist;
    }
    if (type === "todo") {
      const date = typeof draft.date === "string" ? draft.date.trim() : "";
      if (date && !isIsoDate(date)) errors.date = "Enter a valid due date.";
      value.date = date || null;
      value.checklist = checklist;
    }

    return { ok: Object.keys(errors).length === 0, errors, value };
  }

  function pendingKey(kind, taskId = "", itemId = "") {
    return [kind, taskId, itemId].map((part) => encodeURIComponent(String(part))).join(":");
  }

  function claimPending(pending, key, metadata = {}) {
    if (!(pending instanceof Map)) throw new TypeError("Pending registry must be a Map.");
    if (pending.has(key)) return false;
    pending.set(key, { key, ...metadata });
    return true;
  }

  function releasePending(pending, key) {
    return pending instanceof Map ? pending.delete(key) : false;
  }

  function taskHasPending(pending, taskId) {
    if (!(pending instanceof Map)) return false;
    return [...pending.values()].some((entry) => entry.taskId === taskId);
  }

  function mergeTaskPreservingPending(current, incoming, pending, taskId, ownedKey = null) {
    if (!current) return normalizeTask(incoming);
    const merged = normalizeTask(incoming);
    if (!(pending instanceof Map)) return merged;

    pending.forEach((entry, key) => {
      if (key === ownedKey || entry.taskId !== taskId) return;
      if (entry.kind === "completion") merged.completed = current.completed;
      if (entry.kind === "score") {
        merged.value = current.value;
        merged.colorToken = taskColorToken(current.value);
        merged.counterUp = current.counterUp;
        merged.counterDown = current.counterDown;
      }
      if (entry.kind === "checklist") {
        const currentItem = current.checklist.find((item) => item.id === entry.itemId);
        const mergedItem = merged.checklist.find((item) => item.id === entry.itemId);
        if (currentItem && mergedItem) mergedItem.completed = currentItem.completed;
      }
    });
    return merged;
  }

  function createTaskState() {
    return {
      activeView: null,
      filters: { habit: "all", daily: "due", todo: "active" },
      collections: {
        habit: createCollection(),
        daily: createCollection(),
        todoActive: createCollection(),
        todoCompleted: createCollection(),
      },
      pending: new Map(),
      completionOrigins: new Map(),
      expandedChecklists: new Set(),
    };
  }

  function taskError(message, code = "invalid_task") {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function publicErrorMessage(error, operation = "update this task") {
    const copies = {
      invalid_telegram_session: "Open the Mini App from Telegram and try again.",
      habitica_not_linked: "Connect your Habitica account with /start first.",
      day_refresh_required: "Review yesterday’s Dailies before scoring today’s tasks.",
      unauthorized: "Habitica rejected the saved connection. Relink your account with /start.",
      task_not_found: "This task no longer exists. Refresh the list.",
      conflict: "This task changed elsewhere. Refresh and try again.",
      rate_limited: "Habitica is receiving too many requests. Try again shortly.",
      habitica_unavailable: `Habitica could not ${operation}. Refresh to verify its current state.`,
      service_unavailable: `The app could not ${operation}. Try again shortly.`,
    };
    return copies[error && error.code] || `Could not ${operation}. Please try again.`;
  }

  function createProfileCoordinator(options) {
    if (!options || typeof options.fetchProfile !== "function" || typeof options.applyProfile !== "function") {
      throw new TypeError("Profile coordinator requires fetchProfile and applyProfile callbacks.");
    }
    const applyPatch = typeof options.applyPatch === "function" ? options.applyPatch : function noop() {};
    const warn = typeof options.warn === "function" ? options.warn : function noop() {};
    const scheduleTimer = typeof options.setTimer === "function" ? options.setTimer : setTimeout;
    const cancelTimer = typeof options.clearTimer === "function" ? options.clearTimer : clearTimeout;
    const delay = Number.isFinite(options.delay) && options.delay >= 0 ? options.delay : 180;
    let generation = 0;
    let timer = null;
    let destroyed = false;

    async function run(token, { quiet = false } = {}) {
      try {
        const payload = await options.fetchProfile();
        if (destroyed || token !== generation) return { applied: false, stale: true };
        options.applyProfile(payload);
        return { applied: true, stale: false };
      } catch (error) {
        if (!destroyed && token === generation && quiet) warn(error);
        if (!quiet && !destroyed && token === generation) throw error;
        return { applied: false, stale: token !== generation, error };
      }
    }

    function load(loadOptions = {}) {
      if (timer !== null) {
        cancelTimer(timer);
        timer = null;
      }
      generation += 1;
      return run(generation, loadOptions);
    }

    function scheduleMutationRefresh(profilePatch = null) {
      generation += 1;
      const token = generation;
      if (profilePatch && typeof profilePatch === "object") applyPatch(profilePatch);
      if (timer !== null) cancelTimer(timer);
      timer = scheduleTimer(() => {
        timer = null;
        void run(token, { quiet: true });
      }, delay);
      return token;
    }

    function destroy() {
      destroyed = true;
      generation += 1;
      if (timer !== null) cancelTimer(timer);
      timer = null;
    }

    return Object.freeze({ load, scheduleMutationRefresh, destroy, get generation() { return generation; } });
  }

  function createTaskController(options) {
    if (!options || typeof options.requestJson !== "function") {
      throw new TypeError("createTaskController requires requestJson(path, options).");
    }
    const requestJson = options.requestJson;
    const rootNode = options.root || (typeof document === "object" ? document : null);
    const announce = typeof options.announce === "function" ? options.announce : function noop() {};
    const haptic = typeof options.haptic === "function" ? options.haptic : function noop() {};
    const onMutationConfirmed = typeof options.onMutationConfirmed === "function"
      ? options.onMutationConfirmed
      : function noop() {};
    const onDayRefreshRequired = typeof options.onDayRefreshRequired === "function"
      ? options.onDayRefreshRequired
      : function noop() {};
    const apiBase = typeof options.apiBase === "string" ? options.apiBase.replace(/\/$/, "") : "/miniapp/api";
    const limits = { ...DEFAULT_LIMITS, ...(options.limits || {}) };
    const state = createTaskState();
    const requests = new Map();
    let destroyed = false;
    let mutationSequence = 0;

    function notifyMutationConfirmed(detail) {
      try {
        const result = onMutationConfirmed(detail);
        if (result && typeof result.catch === "function") result.catch(() => {});
      } catch (_error) {
        // Profile synchronization is auxiliary; it must never undo a task mutation.
      }
    }

    function notifyDayRefreshRequired(error) {
      try {
        const result = onDayRefreshRequired(error);
        if (result && typeof result.catch === "function") result.catch(() => {});
      } catch (_error) {
        // The startup gate owns recovery; a callback failure must not retry a score.
      }
    }

    function collectionKey(typeValue) {
      const type = canonicalType(typeValue);
      if (type === "todo") return state.filters.todo === "completed" ? "todoCompleted" : "todoActive";
      return type;
    }

    function relevantCollections(typeValue) {
      const type = canonicalType(typeValue);
      if (type === "todo") return [state.collections.todoActive, state.collections.todoCompleted];
      return type ? [state.collections[type]] : [];
    }

    function findTask(taskId) {
      for (const collection of Object.values(state.collections)) {
        if (collection.byId.has(taskId)) return collection.byId.get(taskId);
      }
      return null;
    }

    function taskRoute(typeValue) {
      const type = canonicalType(typeValue);
      if (type === "todo") {
        return `${apiBase}/tasks?type=todo&completed=${state.filters.todo === "completed" ? "true" : "false"}`;
      }
      return `${apiBase}/tasks?type=${encodeURIComponent(type)}`;
    }

    function responseTasks(payload) {
      if (!payload || payload.ok !== true || !Array.isArray(payload.tasks)) {
        throw taskError("Task list response was incomplete.", "invalid_response");
      }
      return payload.tasks;
    }

    function responseTask(payload, expectedType, expectedId = null) {
      if (payload?.ok === true && payload.reloadRequired === true) {
        const error = taskError("The task was saved and the list must be reloaded.", "reload_required");
        error.reloadRequired = true;
        error.mutationConfirmed = true;
        if (payload.profilePatch && typeof payload.profilePatch === "object") {
          error.profilePatch = payload.profilePatch;
        }
        throw error;
      }
      if (!payload || payload.ok !== true || !payload.task) {
        throw taskError("Task response was incomplete.", "invalid_response");
      }
      const task = normalizeTask(payload.task);
      if (task.type !== expectedType || (expectedId && task.id !== expectedId)) {
        throw taskError("Task response did not match the request.", "invalid_response");
      }
      return task;
    }

    function pageFor(type) {
      if (!rootNode || typeof rootNode.querySelector !== "function") return null;
      return rootNode.querySelector(`[data-task-page="${type}"]`)
        || rootNode.querySelector(`[data-view="${TYPE_TO_VIEW[type]}"]`);
    }

    function pendingEntry(kind, taskId, itemId = "") {
      return state.pending.get(pendingKey(kind, taskId, itemId));
    }

    function classificationTask(task) {
      if (!state.completionOrigins.has(task.id)) return task;
      return { ...task, completed: state.completionOrigins.get(task.id) };
    }

    function visibleTasks(type, collection) {
      const tasks = collectionTasks(collection);
      const filter = state.filters[type];
      return tasks.filter((task) => filterTasks(type, [classificationTask(task)], filter).length === 1);
    }

    function createElement(doc, name, className, text) {
      const element = doc.createElement(name);
      if (className) element.className = className;
      if (text !== undefined) setSafeText(element, text);
      return element;
    }

    function makeActionButton(doc, label, action, task, extra = {}) {
      const visibleText = extra.visibleText !== undefined ? extra.visibleText : label;
      const button = createElement(doc, "button", `task-action task-action--${action}`, visibleText);
      button.type = "button";
      button.dataset.taskAction = action;
      button.dataset.taskType = task.type;
      button.dataset.taskId = task.id;
      button.dataset.focusKey = `${action}:${task.id}:${extra.direction || extra.itemId || ""}`;
      button.setAttribute("aria-label", `${label}: ${task.text || "Untitled task"}`);
      if (extra.direction) button.dataset.direction = extra.direction;
      if (extra.itemId) button.dataset.itemId = extra.itemId;
      if (extra.disabled) button.disabled = true;
      return button;
    }

    function appendCompactMetadata(doc, content, task) {
      const items = compactTaskMetadata(task);
      if (!items.length) return;
      const metadata = createElement(doc, "span", "task-row__meta");
      items.forEach((item) => metadata.append(createElement(doc, "span", "task-row__meta-item", item)));
      content.append(metadata);
    }

    function appendHabitCounter(doc, content, task) {
      const summary = habitCounterSummary(task);
      if (!summary) return false;
      const counter = createElement(doc, "span", "task-row__counter", summary.text);
      counter.setAttribute("aria-label", summary.ariaLabel);
      counter.dataset.counterFrequency = summary.frequency;
      content.append(counter);
      return true;
    }

    function unavailableHabitEdge(doc, task, direction) {
      const positive = direction === "up";
      const button = createElement(doc, "button", `task-row__edge task-row__edge--${positive ? "positive" : "negative"} is-unavailable`, positive ? "+" : "−");
      button.type = "button";
      button.disabled = true;
      button.setAttribute("aria-label", `${positive ? "Positive" : "Negative"} scoring unavailable for ${task.text || "this Habit"}`);
      return button;
    }

    function habitScoreEdge(doc, task, direction) {
      if (task[direction] !== true) return unavailableHabitEdge(doc, task, direction);
      const positive = direction === "up";
      const button = makeActionButton(
        doc,
        `Score ${task.text || "this Habit"} ${positive ? "positively" : "negatively"}`,
        "score",
        task,
        {
          direction,
          disabled: Boolean(pendingEntry("score", task.id) || pendingEntry("write", task.id)),
          visibleText: positive ? "+" : "−",
        },
      );
      button.className = `task-row__edge task-row__edge--${positive ? "positive" : "negative"}`;
      button.setAttribute("aria-label", `Score ${task.text || "this Habit"} ${positive ? "positively" : "negatively"}`);
      return button;
    }

    function taskContentButton(doc, task) {
      const content = createElement(doc, "button", "task-row__content");
      content.type = "button";
      content.dataset.taskAction = "open";
      content.dataset.taskType = task.type;
      content.dataset.taskId = task.id;
      content.dataset.focusKey = `open:${task.id}`;
      const counterSummary = habitCounterSummary(task);
      const counterLabel = counterSummary ? `. ${counterSummary.ariaLabel}` : "";
      content.setAttribute(
        "aria-label",
        `${task.canEdit ? "Edit" : "View"} ${task.text || "task"}${counterLabel}`,
      );
      content.disabled = Boolean(pendingEntry("write", task.id));
      content.append(createElement(doc, "span", "task-row__title", task.text || "Untitled task"));
      if (task.notes) content.append(createElement(doc, "span", "task-row__notes", task.notes));
      appendCompactMetadata(doc, content, task);
      return content;
    }

    function completionControl(doc, task) {
      const direction = task.completed ? "down" : "up";
      const label = `${task.completed ? "Uncomplete" : "Complete"} ${task.text || "task"}`;
      const button = makeActionButton(doc, label, "completion", task, {
        direction,
        disabled: Boolean(pendingEntry("completion", task.id) || pendingEntry("write", task.id)),
        visibleText: task.completed ? "✓" : "",
      });
      button.className = "task-row__completion";
      button.setAttribute("aria-pressed", String(task.completed));
      button.setAttribute("aria-label", label);
      return button;
    }

    function taskCard(doc, task) {
      const classes = ["task-row", `task-row--${task.type}`, `task-row--${task.colorToken}`];
      if (task.completed) classes.push("is-completed");
      if (task.type === "daily" && !task.dueToday && !task.completed) classes.push("is-not-due");
      if (task.notes) classes.push("has-notes");
      if (compactTaskMetadata(task).length) classes.push("has-meta");
      if (habitCounterSummary(task)) classes.push("has-counter");
      const card = createElement(doc, "article", classes.join(" "));
      card.dataset.taskId = task.id;
      card.dataset.taskType = task.type;
      card.dataset.taskColor = task.colorToken;
      card.setAttribute("aria-busy", String(taskHasPending(state.pending, task.id)));

      if (task.type === "habit") {
        card.append(habitScoreEdge(doc, task, "up"));
        card.append(taskContentButton(doc, task));
        appendHabitCounter(doc, card, task);
        card.append(habitScoreEdge(doc, task, "down"));
      } else {
        card.append(completionControl(doc, task));
        card.append(taskContentButton(doc, task));
      }
      return card;
    }

    function appendTaskList(doc, parent, tasks) {
      const list = createElement(doc, "ul", "task-list");
      list.setAttribute("role", "list");
      tasks.forEach((task) => {
        const item = createElement(doc, "li", "task-list__item");
        item.append(taskCard(doc, task));
        list.append(item);
      });
      parent.append(list);
    }

    function renderPage(typeValue) {
      const type = canonicalType(typeValue);
      if (!type) return;
      const page = pageFor(type);
      if (!page || !page.ownerDocument) return;
      const doc = page.ownerDocument;
      const key = collectionKey(type);
      const collection = state.collections[key];
      const tasks = visibleTasks(type, collection);
      const focused = doc.activeElement && doc.activeElement.dataset
        ? doc.activeElement.dataset.focusKey
        : null;

      page.setAttribute("aria-busy", String(collection.loading || collection.refreshing));
      const count = page.querySelector("[data-task-count]");
      if (count) {
        const countLabel = `${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}`;
        setSafeText(count, String(tasks.length));
        count.setAttribute("aria-label", countLabel);
      }
      const collectionPending = taskHasCollectionPending(type);
      page.querySelectorAll("[data-task-filter]").forEach((button) => {
        const active = button.dataset.taskFilter === state.filters[type];
        button.setAttribute("aria-pressed", String(active));
        button.classList.toggle("is-active", active);
        button.disabled = collectionPending;
      });
      const addButton = page.querySelector("[data-task-add]");
      if (addButton) addButton.disabled = state.pending.has(pendingKey("create", type));

      const loading = page.querySelector("[data-task-loading]");
      if (loading) loading.hidden = !collection.loading;
      const errorBox = page.querySelector("[data-task-error]");
      if (errorBox) errorBox.hidden = !collection.error;
      const errorMessage = page.querySelector("[data-task-error-message]");
      if (errorMessage && collection.error) setSafeText(errorMessage, publicErrorMessage(collection.error, `load ${TYPE_TO_VIEW[type]}`));
      const empty = page.querySelector("[data-task-empty]");
      if (empty) empty.hidden = !collection.loaded || collection.loading || tasks.length > 0;

      const container = page.querySelector("[data-task-list]");
      if (!container) return;
      container.replaceChildren();
      if (type === "daily" && state.filters.daily === "all") {
        const classified = tasks.map((task) => classificationTask(task));
        const groups = groupDailyTasks(classified);
        const actualById = new Map(tasks.map((task) => [task.id, task]));
        [
          ["Due today", groups.due],
          ["Not due today", groups.notDue],
          ["Completed today", groups.completed],
        ].forEach(([label, group]) => {
          if (!group.length) return;
          const section = createElement(doc, "section", "task-group");
          section.append(createElement(doc, "h2", "task-group__title", `${label} · ${group.length}`));
          appendTaskList(doc, section, group.map((task) => actualById.get(task.id)));
          container.append(section);
        });
      } else {
        appendTaskList(doc, container, tasks);
      }

      if (focused) {
        const candidate = [...page.querySelectorAll("[data-focus-key]")]
          .find((element) => element.dataset.focusKey === focused);
        if (candidate && typeof candidate.focus === "function") candidate.focus({ preventScroll: true });
      }
    }

    function renderTypeAndTodoPeers(type) {
      renderPage(type);
    }

    function pulseTaskSuccess(type, taskId) {
      const page = pageFor(type);
      if (!page || typeof page.querySelectorAll !== "function") return;
      const row = [...page.querySelectorAll(".task-row")]
        .find((candidate) => candidate.dataset?.taskId === taskId);
      if (!row) return;
      row.classList.add("is-score-success");
      setTimeout(() => row.classList.remove("is-score-success"), 420);
    }

    function clearUncertainForType(type) {
      [...state.pending.entries()].forEach(([key, entry]) => {
        if (
          entry.type === type
          && entry.uncertain === true
          && entry.requiresNewIntent !== true
        ) state.pending.delete(key);
      });
    }

    function tasksPreservingPending(rawTasks) {
      return rawTasks.map((raw) => {
        const incoming = normalizeTask(raw);
        const current = findTask(incoming.id);
        return mergeTaskPreservingPending(current, incoming, state.pending, incoming.id);
      });
    }

    async function load(typeValue, { force = false, authoritative = false } = {}) {
      const type = canonicalType(typeValue);
      if (!type || destroyed) return false;
      const key = collectionKey(type);
      const collection = state.collections[key];
      if ([...state.pending.values()].some((entry) => entry.type === type && entry.uncertain === true)) {
        authoritative = true;
      }
      if (collection.needsRefresh) force = true;
      if (collection.loaded && !force) {
        renderPage(type);
        return true;
      }
      if (requests.has(key)) {
        const existing = requests.get(key);
        if (!authoritative) return existing;
        await existing;
        return load(type, { force: true, authoritative: true });
      }

      const token = beginCollectionRequest(collection, { refresh: force });
      renderPage(type);
      const pendingRequest = (async () => {
        try {
          const payload = await requestJson(taskRoute(type), { method: "GET" });
          const rawTasks = responseTasks(payload);
          const result = applyCollectionResponse(
            collection,
            token,
            authoritative ? rawTasks : tasksPreservingPending(rawTasks),
          );
          if (result.applied) {
            if (authoritative) clearUncertainForType(type);
            renderPage(type);
          }
          return result.applied;
        } catch (error) {
          if (failCollectionRequest(collection, token, error)) {
            renderPage(type);
            announce(publicErrorMessage(error, `load ${TYPE_TO_VIEW[type]}`), "error");
          }
          return false;
        } finally {
          requests.delete(key);
          if (collection.needsRefresh && !relevantCollections(type).some((item) => item === collection && taskHasCollectionPending(type))) {
            collection.needsRefresh = false;
            void load(type, { force: true });
          }
        }
      })();
      requests.set(key, pendingRequest);
      return pendingRequest;
    }

    function taskHasCollectionPending(typeValue) {
      const type = canonicalType(typeValue);
      return [...state.pending.values()].some((entry) => entry.type === type);
    }

    function markTypeChanged(type) {
      relevantCollections(type).forEach(markCollectionChanged);
    }

    function refreshAfterSettledMutation(type) {
      if (taskHasCollectionPending(type)) return;
      const current = state.collections[collectionKey(type)];
      if (current?.needsRefresh) void load(type, { force: true });
    }

    function outcomeMayBeUnknown(error) {
      if (error?.reconcileRequired === true || error?.code === "outcome_unknown") return true;
      return ![
        "invalid_request",
        "invalid_telegram_session",
        "habitica_not_linked",
        "day_refresh_required",
        "task_not_found",
        "conflict",
        "rate_limited",
        "unauthorized",
        "habitica_unavailable",
        "service_unavailable",
      ].includes(error && error.code);
    }

    function resetCollection(collection) {
      collection.requestSequence += 1;
      collection.revision += 1;
      collection.byId = new Map();
      collection.order = [];
      collection.loaded = false;
      collection.loading = false;
      collection.refreshing = false;
      collection.error = null;
      collection.needsRefresh = false;
    }

    function invalidate(typeValues = TYPES) {
      const requested = new Set(
        (Array.isArray(typeValues) ? typeValues : [typeValues])
          .map(canonicalType)
          .filter(Boolean),
      );
      requested.forEach((type) => relevantCollections(type).forEach(resetCollection));
      requested.forEach(renderPage);
      return requested.size;
    }

    async function refreshTypes(typeValues = TYPES) {
      const requested = [...new Set(
        (Array.isArray(typeValues) ? typeValues : [typeValues])
          .map(canonicalType)
          .filter(Boolean),
      )];
      const results = [];
      for (const type of requested) {
        results.push(await load(type, { force: true, authoritative: true }));
      }
      return results.every(Boolean);
    }

    function markPendingUncertain(type, key, { requiresNewIntent = false } = {}) {
      const entry = state.pending.get(key);
      if (entry) {
        entry.uncertain = true;
        entry.requiresNewIntent = requiresNewIntent;
      }
      relevantCollections(type).forEach((collection) => { collection.needsRefresh = true; });
    }

    async function reconcileAfterMutation(type, key, options = {}) {
      markPendingUncertain(type, key, options);
      return load(type, { force: true, authoritative: true });
    }

    async function reconcileUnknownMutation(type, key) {
      const reconciled = await reconcileAfterMutation(type, key);
      if (!reconciled) {
        announce("The result is still unknown. Refresh the list before trying that action again.", "error");
      }
      return reconciled;
    }

    function currentForMerge(taskId) {
      const task = findTask(taskId);
      return task ? cloneTask(task) : null;
    }

    function applyTask(taskValue, ownedKey = null) {
      const incoming = normalizeTask(taskValue);
      const current = currentForMerge(incoming.id);
      const task = mergeTaskPreservingPending(current, incoming, state.pending, incoming.id, ownedKey);
      if (task.type === "todo") {
        removeCollectionTask(state.collections.todoActive, task.id);
        removeCollectionTask(state.collections.todoCompleted, task.id);
        upsertCollectionTask(task.completed ? state.collections.todoCompleted : state.collections.todoActive, task);
      } else {
        upsertCollectionTask(state.collections[task.type], task);
      }
      renderTypeAndTodoPeers(task.type);
      return task;
    }

    function assertIdleForWrite(task) {
      if (taskHasPending(state.pending, task.id)) {
        throw taskError("Wait for the current task update to finish.", "busy");
      }
    }

    async function createTask(draft) {
      const typeHint = canonicalType(draft && draft.type);
      const validation = validateTaskDraft(
        typeHint === "daily" ? { ...draft, scheduleEditable: true } : draft,
        limits,
      );
      if (!validation.ok) throw Object.assign(taskError("Review the highlighted fields.", "invalid_request"), { errors: validation.errors });
      const type = validation.value.type;
      const key = pendingKey("create", type);
      if (!claimPending(state.pending, key, { kind: "create", type })) throw taskError("Task creation is already in progress.", "busy");
      markTypeChanged(type);
      let keepPending = false;
      try {
        const payload = await requestJson(`${apiBase}/tasks`, { method: "POST", body: validation.value });
        const task = applyTask(responseTask(payload, type), key);
        announce(`${task.text || "Task"} created.`, "success");
        haptic("notification", "success");
        return task;
      } catch (error) {
        if (error?.reloadRequired === true) {
          const reconciled = await reconcileAfterMutation(type, key);
          keepPending = !reconciled;
          if (reconciled) {
            announce("Task created and list refreshed.", "success");
            haptic("notification", "success");
            return null;
          }
        } else if (outcomeMayBeUnknown(error)) {
          error.outcomeUnknown = true;
          error.reconciled = await reconcileAfterMutation(type, key, { requiresNewIntent: true });
          // A list cannot prove whether an ID-less create arrived late. Keep
          // this session-level guard even after a successful refresh.
          keepPending = true;
        }
        announce(publicErrorMessage(error, "create this task"), "error");
        throw error;
      } finally {
        if (!keepPending) releasePending(state.pending, key);
        renderPage(type);
        refreshAfterSettledMutation(type);
      }
    }

    async function updateTask(taskId, draft) {
      const current = findTask(taskId);
      if (!current) throw taskError("Task is not available.", "task_not_found");
      assertIdleForWrite(current);
      const validation = validateTaskDraft({
        ...draft,
        type: current.type,
        revision: typeof draft?.revision === "string" ? draft.revision : current.revision,
        scheduleEditable: current.type === "daily" && current.scheduleEditable === true,
      }, limits);
      if (!validation.ok) throw Object.assign(taskError("Review the highlighted fields.", "invalid_request"), { errors: validation.errors });
      const key = pendingKey("write", taskId);
      claimPending(state.pending, key, { kind: "write", type: current.type, taskId });
      markTypeChanged(current.type);
      renderPage(current.type);
      let keepPending = false;
      try {
        const payload = await requestJson(`${apiBase}/tasks/${encodeURIComponent(taskId)}`, {
          method: "PATCH",
          body: validation.value,
        });
        const task = applyTask(responseTask(payload, current.type, taskId), key);
        announce(`${task.text || "Task"} updated.`, "success");
        haptic("notification", "success");
        return task;
      } catch (error) {
        if (error?.reloadRequired === true) {
          const reconciled = await reconcileAfterMutation(current.type, key);
          keepPending = !reconciled;
          if (reconciled) {
            const task = findTask(taskId);
            announce("Task updated and list refreshed.", "success");
            haptic("notification", "success");
            return task;
          }
        } else if (outcomeMayBeUnknown(error)) {
          error.outcomeUnknown = true;
          error.reconciled = await reconcileAfterMutation(
            current.type,
            key,
            { requiresNewIntent: true },
          );
          keepPending = true;
        }
        announce(publicErrorMessage(error, "update this task"), "error");
        throw error;
      } finally {
        if (!keepPending) releasePending(state.pending, key);
        renderPage(current.type);
        refreshAfterSettledMutation(current.type);
      }
    }

    async function deleteTask(typeValue, taskId) {
      const type = canonicalType(typeValue);
      const current = findTask(taskId);
      if (!type || !current || current.type !== type) throw taskError("Task is not available.", "task_not_found");
      assertIdleForWrite(current);
      const key = pendingKey("write", taskId);
      claimPending(state.pending, key, { kind: "write", type, taskId });
      markTypeChanged(type);
      renderPage(type);
      let keepPending = false;
      try {
        const payload = await requestJson(`${apiBase}/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
        if (!payload || payload.ok !== true) throw taskError("Delete response was incomplete.", "invalid_response");
        relevantCollections(type).forEach((collection) => removeCollectionTask(collection, taskId));
        announce(`${current.text || "Task"} deleted.`, "success");
        haptic("notification", "success");
        return true;
      } catch (error) {
        if (outcomeMayBeUnknown(error)) {
          error.outcomeUnknown = true;
          error.reconciled = await reconcileAfterMutation(type, key, { requiresNewIntent: true });
          keepPending = true;
          if (error.reconciled && !findTask(taskId)) {
            keepPending = false;
            announce(`${current.text || "Task"} deleted.`, "success");
            haptic("notification", "success");
            return true;
          }
        }
        announce(publicErrorMessage(error, "delete this task"), "error");
        throw error;
      } finally {
        if (!keepPending) releasePending(state.pending, key);
        renderPage(type);
        refreshAfterSettledMutation(type);
      }
    }

    async function scoreTask(typeValue, taskId, direction) {
      const type = canonicalType(typeValue);
      const current = findTask(taskId);
      if (!type || !current || current.type !== type) throw taskError("Task is not available.", "task_not_found");
      if (direction !== "up" && direction !== "down") throw taskError("Scoring direction is invalid.", "invalid_request");
      if (type === "habit" && !visibleHabitDirections(current).includes(direction)) {
        throw taskError("This Habit does not support that direction.", "invalid_request");
      }
      if (type !== "habit" && direction !== (current.completed ? "down" : "up")) {
        throw taskError("Completion state changed. Refresh and try again.", "conflict");
      }
      const kind = type === "habit" ? "score" : "completion";
      const key = pendingKey(kind, taskId);
      if (!claimPending(state.pending, key, { kind, type, taskId })) return null;
      const snapshot = cloneTask(current);
      const profileSequence = mutationSequence + 1;
      mutationSequence = profileSequence;
      let keepPending = false;
      markTypeChanged(type);
      if (kind === "completion") {
        state.completionOrigins.set(taskId, snapshot.completed);
        current.completed = direction === "up";
      }
      renderPage(type);
      try {
        const payload = await requestJson(`${apiBase}/tasks/${encodeURIComponent(taskId)}/score`, {
          method: "POST",
          body: { direction },
        });
        const task = applyTask(responseTask(payload, type, taskId), key);
        announce(type === "habit" ? "Habit scored." : `${type === "daily" ? "Daily" : "Todo"} ${task.completed ? "completed" : "uncompleted"}.`, "success");
        haptic("notification", "success");
        notifyMutationConfirmed({
          kind: type === "habit" ? "habit-score" : "completion",
          type,
          direction,
          sequence: profileSequence,
          task: cloneTask(task),
          profilePatch: payload.profilePatch && typeof payload.profilePatch === "object"
            ? payload.profilePatch
            : null,
        });
        pulseTaskSuccess(type, taskId);
        return task;
      } catch (error) {
        if (error?.code === "day_refresh_required") {
          applyTask(snapshot, key);
          notifyDayRefreshRequired(error);
        } else if (error?.reloadRequired === true) {
          const reconciled = await reconcileAfterMutation(type, key);
          keepPending = !reconciled;
          if (reconciled) {
            const task = findTask(taskId);
            announce(type === "habit" ? "Habit scored." : `${type === "daily" ? "Daily" : "Todo"} updated.`, "success");
            haptic("notification", "success");
            notifyMutationConfirmed({
              kind: type === "habit" ? "habit-score" : "completion",
              type,
              direction,
              sequence: profileSequence,
              task: task ? cloneTask(task) : null,
              profilePatch: error.profilePatch || null,
            });
            pulseTaskSuccess(type, taskId);
            return task;
          }
        } else if (outcomeMayBeUnknown(error)) {
          announce("The update was sent, but its result could not be confirmed. Checking Habitica now.", "error");
          await reconcileAfterMutation(type, key, { requiresNewIntent: true });
          // A quick GET can finish before Habitica applies a timed-out score.
          // Keep every ambiguous score blocked until a genuinely new session.
          keepPending = true;
        } else {
          applyTask(snapshot, key);
        }
        announce(publicErrorMessage(error, type === "habit" ? "score this Habit" : "change completion"), "error");
        haptic("notification", "error");
        throw error;
      } finally {
        state.completionOrigins.delete(taskId);
        if (!keepPending) releasePending(state.pending, key);
        renderPage(type);
        refreshAfterSettledMutation(type);
      }
    }

    async function scoreChecklist(typeValue, taskId, itemId, direction) {
      const type = canonicalType(typeValue);
      const current = findTask(taskId);
      if (!type || !current || current.type !== type || !["daily", "todo"].includes(type)) {
        throw taskError("Task is not available.", "task_not_found");
      }
      const item = current.checklist.find((candidate) => candidate.id === itemId);
      if (!item) throw taskError("Checklist item is not available.", "task_not_found");
      if (direction !== "up" && direction !== "down") throw taskError("Checklist direction is invalid.", "invalid_request");
      if (direction !== (item.completed ? "down" : "up")) throw taskError("Checklist item changed. Refresh and try again.", "conflict");
      const key = pendingKey("checklist", taskId, itemId);
      if (!claimPending(state.pending, key, { kind: "checklist", type, taskId, itemId })) return null;
      const profileSequence = mutationSequence + 1;
      mutationSequence = profileSequence;
      const previous = item.completed;
      let keepPending = false;
      markTypeChanged(type);
      item.completed = direction === "up";
      renderPage(type);
      try {
        const payload = await requestJson(
          `${apiBase}/tasks/${encodeURIComponent(taskId)}/checklist/${encodeURIComponent(itemId)}/score`,
          { method: "POST", body: { completed: direction === "up" } },
        );
        const task = applyTask(responseTask(payload, type, taskId), key);
        announce("Checklist updated.", "success");
        haptic("selection");
        notifyMutationConfirmed({
          kind: "checklist",
          type,
          itemId,
          sequence: profileSequence,
          task: cloneTask(task),
          profilePatch: null,
        });
        pulseTaskSuccess(type, taskId);
        return task;
      } catch (error) {
        if (error?.code === "day_refresh_required") {
          const latest = findTask(taskId);
          const latestItem = latest && latest.checklist.find((candidate) => candidate.id === itemId);
          if (latestItem) latestItem.completed = previous;
          notifyDayRefreshRequired(error);
        } else if (error?.reloadRequired === true) {
          const reconciled = await reconcileAfterMutation(type, key);
          keepPending = !reconciled;
          if (reconciled) {
            const task = findTask(taskId);
            announce("Checklist updated.", "success");
            haptic("selection");
            notifyMutationConfirmed({
              kind: "checklist",
              type,
              itemId,
              sequence: profileSequence,
              task: task ? cloneTask(task) : null,
              profilePatch: null,
            });
            pulseTaskSuccess(type, taskId);
            return task;
          }
        } else if (outcomeMayBeUnknown(error)) {
          announce("The checklist update was sent, but its result could not be confirmed. Checking Habitica now.", "error");
          await reconcileAfterMutation(type, key, { requiresNewIntent: true });
          keepPending = true;
        } else {
          const latest = findTask(taskId);
          const latestItem = latest && latest.checklist.find((candidate) => candidate.id === itemId);
          if (latestItem) latestItem.completed = previous;
        }
        announce(publicErrorMessage(error, "update this checklist item"), "error");
        haptic("notification", "error");
        throw error;
      } finally {
        if (!keepPending) releasePending(state.pending, key);
        renderPage(type);
        refreshAfterSettledMutation(type);
      }
    }

    async function setFilter(typeValue, filter) {
      const type = canonicalType(typeValue);
      if (!type || !FILTERS[type].includes(filter)) throw taskError("Filter is invalid.", "invalid_request");
      state.filters[type] = filter;
      renderPage(type);
      return load(type);
    }

    async function activate(view) {
      const type = canonicalType(view);
      state.activeView = type ? TYPE_TO_VIEW[type] : view;
      if (!type) return false;
      return load(type);
    }

    async function refresh(view) {
      const type = canonicalType(view || state.activeView);
      return type ? load(type, { force: true }) : false;
    }

    function dispatch(name, detail) {
      if (!rootNode || typeof rootNode.dispatchEvent !== "function" || typeof CustomEvent !== "function") return;
      rootNode.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function eventActionTarget(event) {
      const target = event && event.target;
      return target && typeof target.closest === "function" ? target.closest("[data-task-action]") : null;
    }

    function handleClick(event) {
      const filter = event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-task-filter]")
        : null;
      if (filter) {
        const page = filter.closest("[data-task-page], [data-view]");
        const type = canonicalType(filter.dataset.taskType || (page && (page.dataset.taskPage || page.dataset.view)));
        if (type) void setFilter(type, filter.dataset.taskFilter).catch(() => {});
        return;
      }
      const refreshButton = event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-task-refresh]")
        : null;
      if (refreshButton) {
        void refresh(refreshButton.dataset.taskType).catch(() => {});
        return;
      }
      const addButton = event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-task-add]")
        : null;
      if (addButton) {
        dispatch(
          addButton.dataset.quickAdd === "true" ? "miniapp:quick-add" : "miniapp:task-create",
          { type: canonicalType(addButton.dataset.taskType) },
        );
        return;
      }
      const action = eventActionTarget(event);
      if (!action || action.disabled) return;
      const type = canonicalType(action.dataset.taskType);
      const taskId = action.dataset.taskId;
      const task = findTask(taskId);
      if (!type || !task) return;
      if (action.dataset.taskAction === "score") {
        void scoreTask(type, taskId, action.dataset.direction).catch(() => {});
      } else if (action.dataset.taskAction === "completion") {
        void scoreTask(type, taskId, action.dataset.direction).catch(() => {});
      } else if (action.dataset.taskAction === "open") {
        dispatch("miniapp:task-edit", { task: cloneTask(task) });
      } else if (action.dataset.taskAction === "edit") {
        dispatch("miniapp:task-edit", { task: cloneTask(task) });
      } else if (action.dataset.taskAction === "delete") {
        dispatch("miniapp:task-delete-request", { task: cloneTask(task) });
      }
    }

    function handleChange(event) {
      const action = eventActionTarget(event);
      if (!action || action.disabled) return;
      const type = canonicalType(action.dataset.taskType);
      const taskId = action.dataset.taskId;
      if (action.dataset.taskAction === "completion") {
        void scoreTask(type, taskId, action.checked ? "up" : "down").catch(() => {});
      }
      if (action.dataset.taskAction === "checklist") {
        void scoreChecklist(type, taskId, action.dataset.itemId, action.checked ? "up" : "down").catch(() => {});
      }
    }

    if (rootNode && typeof rootNode.addEventListener === "function") {
      rootNode.addEventListener("click", handleClick);
      rootNode.addEventListener("change", handleChange);
    }

    function destroy() {
      destroyed = true;
      if (rootNode && typeof rootNode.removeEventListener === "function") {
        rootNode.removeEventListener("click", handleClick);
        rootNode.removeEventListener("change", handleChange);
      }
    }

    return Object.freeze({
      state,
      activate,
      refresh,
      load,
      setFilter,
      createTask,
      updateTask,
      deleteTask,
      scoreTask,
      scoreChecklist,
      invalidate,
      refreshTypes,
      render: renderPage,
      findTask,
      destroy,
    });
  }

  return Object.freeze({
    TYPES,
    DAY_KEYS,
    PRIORITIES,
    DEFAULT_LIMITS,
    FILTERS,
    TASK_COLOR_TOKENS,
    canonicalType,
    taskColorToken,
    normalizeHabitCounter,
    normalizeCounterFrequency,
    habitCounterSummary,
    normalizeQuestLogSummary,
    displayText,
    setSafeText,
    normalizeRepeatDays,
    normalizeTask,
    cloneTask,
    dedupeTasks,
    createCollection,
    collectionTasks,
    beginCollectionRequest,
    markCollectionChanged,
    applyCollectionResponse,
    failCollectionRequest,
    upsertCollectionTask,
    removeCollectionTask,
    visibleHabitDirections,
    filterTasks,
    groupDailyTasks,
    difficultyLabel,
    compactTaskMetadata,
    quickAddDefaults,
    isIsoDate,
    validateTaskDraft,
    pendingKey,
    claimPending,
    releasePending,
    taskHasPending,
    mergeTaskPreservingPending,
    createTaskState,
    createProfileCoordinator,
    createTaskController,
  });
});
