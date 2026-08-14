(function gameplayModule(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && typeof root === "object") root.HabiticaGameplayUI = api;
})(typeof globalThis === "object" ? globalThis : this, function buildGameplayUi() {
  "use strict";

  const STARTUP_STATES = Object.freeze([
    "checking_day",
    "ready",
    "review_required",
    "submitting_review",
    "refreshing_day",
    "refresh_failed",
  ]);
  const STARTUP_STATE_SET = new Set(STARTUP_STATES);

  function safeNonnegativeInteger(value, fallback = null) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? Math.floor(value)
      : fallback;
  }

  function safeOptionalNumber(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }

  function displayText(value, fallback = "") {
    return typeof value === "string" ? value : fallback;
  }

  function normalizeChecklist(value) {
    if (!Array.isArray(value)) return [];
    return value.flatMap((raw, index) => {
      if (!raw || typeof raw !== "object" || typeof raw.text !== "string") return [];
      const id = typeof raw.id === "string" && raw.id ? raw.id : `checklist-${index}`;
      return [{ id, text: raw.text, completed: raw.completed === true }];
    });
  }

  function normalizeReviewDaily(raw) {
    if (!raw || typeof raw !== "object" || typeof raw.id !== "string" || !raw.id) return null;
    if (typeof raw.text !== "string") return null;
    return {
      id: raw.id,
      text: raw.text,
      notes: displayText(raw.notes),
      priority: typeof raw.priority === "number" && Number.isFinite(raw.priority) ? raw.priority : 1,
      checklist: normalizeChecklist(raw.checklist),
      completed: raw.completed === true,
    };
  }

  function normalizeReviewDailies(value) {
    if (!Array.isArray(value)) return [];
    const byId = new Map();
    value.forEach((raw) => {
      const daily = normalizeReviewDaily(raw);
      if (daily && !daily.completed && !byId.has(daily.id)) byId.set(daily.id, daily);
    });
    return [...byId.values()];
  }

  function normalizeDayPayload(payload) {
    const raw = payload && typeof payload === "object" && payload.day && typeof payload.day === "object"
      ? payload.day
      : payload;
    if (!raw || typeof raw !== "object" || typeof raw.refreshRequired !== "boolean") {
      throw new TypeError("Day status response was incomplete.");
    }
    const refreshRequired = raw.refreshRequired === true;
    return {
      refreshRequired,
      daysMissed: refreshRequired ? Math.max(1, safeNonnegativeInteger(raw.daysMissed, 1)) : 0,
      reviewLabel: displayText(raw.reviewLabel, "Yesterday") || "Yesterday",
      dailies: refreshRequired ? normalizeReviewDailies(raw.dailies) : [],
    };
  }

  function normalizeIdList(value) {
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter((item) => typeof item === "string" && item))];
  }

  function reconcileReviewSelection(selectedValue, outcome = {}) {
    const selected = new Set(normalizeIdList(selectedValue instanceof Set ? [...selectedValue] : selectedValue));
    normalizeIdList(outcome.resolvedDailyIds).forEach((id) => selected.delete(id));
    const unresolved = normalizeIdList(outcome.unresolvedDailyIds || outcome.remainingDailyIds);
    const eligible = new Set(normalizeReviewDailies(outcome.dailies).map((daily) => daily.id));
    const allowed = unresolved.length ? new Set(unresolved) : eligible;
    return new Set([...selected].filter((id) => allowed.has(id)));
  }

  function normalizePotionPayload(payload) {
    const raw = payload && typeof payload === "object" ? payload.potion : null;
    if (!raw || typeof raw !== "object") throw new TypeError("Potion response was incomplete.");
    const stats = payload?.stats && typeof payload.stats === "object"
      ? {
          hp: safeOptionalNumber(payload.stats.hp),
          maxHp: safeOptionalNumber(payload.stats.maxHp),
          gold: safeOptionalNumber(payload.stats.gold),
        }
      : null;
    return {
      name: displayText(raw.name, "Health Potion") || "Health Potion",
      price: safeOptionalNumber(raw.price),
      healing: safeOptionalNumber(raw.healing),
      healthFull: payload?.healthFull === true,
      canAfford: typeof payload?.canAfford === "boolean" ? payload.canAfford : null,
      stats,
    };
  }

  function profileEnvelope(payload) {
    const candidate = payload && typeof payload === "object" ? payload.profile : null;
    if (
      candidate
      && typeof candidate === "object"
      && candidate.profile
      && typeof candidate.profile === "object"
      && candidate.stats
      && typeof candidate.stats === "object"
    ) {
      return { profile: { ...candidate.profile }, stats: { ...candidate.stats } };
    }
    if (
      payload
      && typeof payload === "object"
      && payload.profile
      && typeof payload.profile === "object"
      && payload.stats
      && typeof payload.stats === "object"
    ) {
      return { profile: { ...payload.profile }, stats: { ...payload.stats } };
    }
    return null;
  }

  function createStartupStateMachine(initial = "checking_day") {
    if (!STARTUP_STATE_SET.has(initial)) throw new TypeError("Startup state is invalid.");
    let current = initial;
    let generation = 0;
    return Object.freeze({
      transition(next) {
        if (!STARTUP_STATE_SET.has(next)) throw new TypeError("Startup state is invalid.");
        current = next;
        generation += 1;
        return generation;
      },
      is(...states) {
        return states.includes(current);
      },
      get current() {
        return current;
      },
      get generation() {
        return generation;
      },
    });
  }

  return Object.freeze({
    STARTUP_STATES,
    safeNonnegativeInteger,
    normalizeReviewDaily,
    normalizeReviewDailies,
    normalizeDayPayload,
    normalizeIdList,
    reconcileReviewSelection,
    normalizePotionPayload,
    profileEnvelope,
    createStartupStateMachine,
  });
});
