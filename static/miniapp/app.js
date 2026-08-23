(() => {
  "use strict";

  const THEME_KEY = "hh_theme_mode";
  const THEME_MODES = new Set(["auto", "light", "dark"]);
  const DISPLAY_SCALE_KEY = "hh_display_scale";
  const DISPLAY_SCALES = new Set(["0.8", "0.9", "1", "1.1", "1.2"]);
  const API_BASE = "/miniapp/api";
  const telegram = window.Telegram?.WebApp ?? null;
  const gameplay = window.HabiticaGameplayUI ?? null;
  const startupMachine = gameplay?.createStartupStateMachine?.("checking_day") ?? null;
  const isTelegramLaunch = Boolean(telegram?.initData) && telegram?.platform !== "unknown";
  const systemTheme = window.matchMedia?.("(prefers-color-scheme: dark)") ?? null;

  const state = {
    themeMode: THEME_MODES.has(document.documentElement.dataset.themeMode)
      ? document.documentElement.dataset.themeMode
      : "auto",
    themeGeneration: 0,
    displayScale: DISPLAY_SCALES.has(document.documentElement.dataset.displayScale)
      ? document.documentElement.dataset.displayScale
      : "1",
    displayScaleGeneration: 0,
    avatarUrl: null,
    avatarRequest: null,
    avatarGeneration: 0,
    profilePayload: null,
    profileMutationSequence: 0,
    profileLoaded: false,
    questSummaryLoaded: false,
    questSummaryDirty: false,
    questSummaryRequest: null,
    questSummaryRevision: 0,
    taskProfileStarted: false,
    homeStarted: false,
    refreshing: false,
    taskRefreshing: false,
    startupState: "checking_day",
    startupResolved: false,
    startupRequestSequence: 0,
    requestedTab: "home",
    dayGatePending: false,
    dayReview: null,
    selectedDailyIds: new Set(),
    daySubmitting: false,
    dayOutcomeUnknown: false,
    potionLoading: false,
    potionSubmitting: false,
    potionOutcomeUnknown: false,
    potionInfo: null,
    potionPurchaseIntent: null,
    activeTab: "home",
    scrollPositions: { home: 0, habits: 0, dailies: 0, todos: 0 },
    editorTask: null,
    editorGeneration: 0,
    editorType: null,
    editorMode: null,
    editorSubmitting: false,
    editorTrigger: null,
    editorFocusKey: null,
    editorInitialDraft: "",
    editorDirty: false,
    editorReadOnly: false,
    deleteTask: null,
    deleteSubmitting: false,
    deleteTrigger: null,
    deleteRestoreFocus: true,
    deletePostFocusType: null,
    quickAddType: null,
    quickAddSubmitting: false,
    quickAddTrigger: null,
    discardConfirmed: false,
    ignoreNextPopstate: false,
  };

  let taskController = null;
  let profileCoordinator = null;
  let toastTimer = null;

  const elements = {
    appShell: document.getElementById("app-shell"),
    dayGate: document.getElementById("day-gate"),
    dayGateChecking: document.getElementById("day-gate-checking"),
    dayGateReview: document.getElementById("day-gate-review"),
    dayGateFailure: document.getElementById("day-gate-failure"),
    dayGateFailureMessage: document.getElementById("day-gate-failure-message"),
    dayGateRetry: document.getElementById("day-gate-retry"),
    dayReviewLabel: document.getElementById("day-review-label"),
    dayReviewTitle: document.getElementById("day-review-title"),
    dayReviewLead: document.getElementById("day-review-lead"),
    dayReviewWarning: document.getElementById("day-review-warning"),
    dayReviewError: document.getElementById("day-review-error"),
    dayReviewList: document.getElementById("day-review-list"),
    dayReviewSelection: document.getElementById("day-review-selection"),
    dayReviewSubmit: document.getElementById("day-review-submit"),
    profileCard: document.getElementById("profile-card"),
    displayName: document.getElementById("display-name"),
    username: document.getElementById("username"),
    level: document.getElementById("level-value"),
    classLabel: document.getElementById("class-label"),
    classMedallion: document.getElementById("class-medallion"),
    classSymbol: document.getElementById("class-symbol"),
    identityMeta: document.getElementById("identity-meta"),
    nameSkeleton: document.getElementById("name-skeleton"),
    metaSkeleton: document.getElementById("meta-skeleton"),
    manaRow: document.getElementById("mana-row"),
    goldChip: document.getElementById("gold-chip"),
    goldValue: document.getElementById("gold-value"),
    avatarImage: document.getElementById("avatar-image"),
    avatarPlaceholder: document.getElementById("avatar-placeholder"),
    questLogStatus: document.getElementById("quest-log-status"),
    miniProfile: document.getElementById("mini-profile"),
    miniProfileRetry: document.getElementById("mini-profile-retry"),
    miniAvatarImage: document.getElementById("mini-avatar-image"),
    miniAvatarPlaceholder: document.getElementById("mini-avatar-placeholder"),
    miniLevel: document.getElementById("mini-level"),
    miniClass: document.getElementById("mini-class"),
    miniGoldWrap: document.getElementById("mini-gold-wrap"),
    miniGold: document.getElementById("mini-gold"),
    miniManaRow: document.getElementById("mini-mana-row"),
    refreshButton: document.getElementById("refresh-button"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("notice-title"),
    noticeMessage: document.getElementById("notice-message"),
    noticeRetry: document.getElementById("notice-retry"),
    themeButton: document.getElementById("theme-button"),
    themeMenu: document.getElementById("theme-menu"),
    displayScaleValue: document.getElementById("display-scale-value"),
    taskToast: document.getElementById("task-toast"),
    editorDialog: document.getElementById("task-editor-dialog"),
    editorForm: document.getElementById("task-editor-form"),
    editorEyebrow: document.getElementById("task-editor-eyebrow"),
    editorTitle: document.getElementById("task-editor-title"),
    editorClose: document.getElementById("task-editor-close"),
    editorBack: document.getElementById("editor-back"),
    editorDelete: document.getElementById("editor-delete"),
    editorCancel: document.getElementById("task-editor-cancel"),
    editorSubmit: document.getElementById("task-editor-submit"),
    editorError: document.getElementById("task-editor-error"),
    taskTitle: document.getElementById("task-title"),
    taskNotes: document.getElementById("task-notes"),
    taskPriority: document.getElementById("task-priority"),
    habitUp: document.getElementById("habit-up"),
    habitDown: document.getElementById("habit-down"),
    dailyStartDate: document.getElementById("daily-start-date"),
    todoDueDate: document.getElementById("todo-due-date"),
    checklistEditor: document.getElementById("checklist-editor"),
    checklistItems: document.getElementById("checklist-editor-items"),
    checklistAdd: document.getElementById("checklist-add"),
    deleteDialog: document.getElementById("delete-dialog"),
    deleteForm: document.getElementById("delete-form"),
    deleteTitle: document.getElementById("delete-task-title"),
    deleteCancel: document.getElementById("delete-cancel"),
    deleteConfirm: document.getElementById("delete-confirm"),
    deleteError: document.getElementById("delete-error"),
    quickAddDialog: document.getElementById("quick-add-dialog"),
    quickAddForm: document.getElementById("quick-add-form"),
    quickAddInput: document.getElementById("quick-add-input"),
    quickAddTypeLabel: document.getElementById("quick-add-type-label"),
    quickAddClose: document.getElementById("quick-add-close"),
    quickAddCancel: document.getElementById("quick-add-cancel"),
    quickAddMore: document.getElementById("quick-add-more"),
    quickAddSubmit: document.getElementById("quick-add-submit"),
    quickAddError: document.getElementById("quick-add-error"),
    taskFab: document.getElementById("task-fab"),
    potionFab: document.getElementById("potion-fab"),
    potionDialog: document.getElementById("potion-dialog"),
    potionDialogTitle: document.getElementById("potion-dialog-title"),
    potionForm: document.getElementById("potion-form"),
    potionClose: document.getElementById("potion-close"),
    potionCancel: document.getElementById("potion-cancel"),
    potionSubmit: document.getElementById("potion-submit"),
    potionHealth: document.getElementById("potion-health"),
    potionGold: document.getElementById("potion-gold"),
    potionPriceRow: document.getElementById("potion-price-row"),
    potionPrice: document.getElementById("potion-price"),
    potionHealingRow: document.getElementById("potion-healing-row"),
    potionHealing: document.getElementById("potion-healing"),
    potionError: document.getElementById("potion-error"),
    discardDialog: document.getElementById("discard-dialog"),
    discardForm: document.getElementById("discard-form"),
    discardCancel: document.getElementById("discard-cancel"),
    discardConfirm: document.getElementById("discard-confirm"),
  };

  function isTelegramVersionAtLeast(version) {
    try {
      return Boolean(telegram?.isVersionAtLeast?.(version));
    } catch (_error) {
      return false;
    }
  }

  function haptic(kind, value) {
    try {
      if (!telegram?.HapticFeedback || !isTelegramVersionAtLeast("6.1")) return;
      if (kind === "selection") telegram.HapticFeedback.selectionChanged();
      if (kind === "impact") telegram.HapticFeedback.impactOccurred(value ?? "light");
      if (kind === "notification") telegram.HapticFeedback.notificationOccurred(value);
    } catch (_error) {
      // Haptics are optional enhancement only.
    }
  }

  function resolveTheme(mode = state.themeMode) {
    if (mode === "light" || mode === "dark") return mode;
    if (isTelegramLaunch && (telegram?.colorScheme === "dark" || telegram?.colorScheme === "light")) {
      return telegram.colorScheme;
    }
    return systemTheme?.matches ? "dark" : "light";
  }

  function syncTelegramChrome(effectiveTheme) {
    if (!telegram) return;
    const dark = effectiveTheme === "dark";
    const pageColor = dark ? "#0b080f" : "#e7e3ea";
    const navColor = dark ? "#14121b" : "#f2eff4";
    try {
      if (isTelegramVersionAtLeast("6.1")) {
        telegram.setBackgroundColor?.(pageColor);
        telegram.setHeaderColor?.(isTelegramVersionAtLeast("6.9") ? pageColor : "bg_color");
      }
      if (isTelegramVersionAtLeast("7.10")) telegram.setBottomBarColor?.(navColor);
    } catch (_error) {
      // Older clients can report a version before exposing every method.
    }
  }

  function updateThemeControls() {
    document.querySelectorAll("[data-theme-choice]").forEach((option) => {
      option.setAttribute("aria-checked", String(option.dataset.themeChoice === state.themeMode));
    });
  }

  function updateDisplayScaleControls() {
    const percent = `${Math.round(Number(state.displayScale) * 100)}%`;
    document.querySelectorAll("[data-display-scale]").forEach((option) => {
      option.setAttribute("aria-checked", String(option.dataset.displayScale === state.displayScale));
    });
    elements.displayScaleValue.textContent = percent;
    elements.themeButton.setAttribute("aria-label", `Open settings. Display size ${percent}`);
  }

  function applyTheme(mode, { persist = false, mirrorCloud = false } = {}) {
    if (!THEME_MODES.has(mode)) return;
    state.themeMode = mode;
    const effective = resolveTheme(mode);
    document.documentElement.dataset.themeMode = mode;
    document.documentElement.dataset.theme = effective;
    document.documentElement.style.colorScheme = effective;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      effective === "dark" ? "#0b080f" : "#e7e3ea",
    );
    updateThemeControls();
    syncTelegramChrome(effective);

    if (persist) {
      state.themeGeneration += 1;
      try {
        window.localStorage.setItem(THEME_KEY, mode);
      } catch (_error) {
        // The in-memory selection remains useful when storage is disabled.
      }
    }
    if (mirrorCloud && isTelegramVersionAtLeast("6.9")) {
      try {
        telegram?.CloudStorage?.setItem(THEME_KEY, mode, () => {});
      } catch (_error) {
        // CloudStorage is best-effort and never authoritative.
      }
    }
  }

  function loadCloudTheme() {
    if (!isTelegramVersionAtLeast("6.9") || !telegram?.CloudStorage?.getItem) return;
    const generationAtRequest = state.themeGeneration;
    try {
      telegram.CloudStorage.getItem(THEME_KEY, (error, value) => {
        if (error || generationAtRequest !== state.themeGeneration || !THEME_MODES.has(value)) return;
        if (value !== state.themeMode) applyTheme(value, { persist: true });
      });
    } catch (_error) {
      // LocalStorage remains the fallback.
    }
  }

  function applyDisplayScale(scale, { persist = false, mirrorCloud = false } = {}) {
    if (!DISPLAY_SCALES.has(scale)) return;
    state.displayScale = scale;
    document.documentElement.dataset.displayScale = scale;
    document.documentElement.style.setProperty("--display-scale", scale);
    document.documentElement.style.setProperty("--minimum-layout-width", `${280 / Number(scale)}px`);
    document.documentElement.style.zoom = scale;
    updateDisplayScaleControls();
    if (persist) {
      state.displayScaleGeneration += 1;
      try {
        window.localStorage.setItem(DISPLAY_SCALE_KEY, scale);
      } catch (_error) {
        // The in-memory selection remains useful when storage is disabled.
      }
    }
    if (mirrorCloud && isTelegramVersionAtLeast("6.9")) {
      try {
        telegram?.CloudStorage?.setItem(DISPLAY_SCALE_KEY, scale, () => {});
      } catch (_error) {
        // CloudStorage is best-effort and never authoritative.
      }
    }
  }

  function loadCloudDisplayScale() {
    if (!isTelegramVersionAtLeast("6.9") || !telegram?.CloudStorage?.getItem) return;
    const generationAtRequest = state.displayScaleGeneration;
    try {
      telegram.CloudStorage.getItem(DISPLAY_SCALE_KEY, (error, value) => {
        if (
          error
          || generationAtRequest !== state.displayScaleGeneration
          || !DISPLAY_SCALES.has(value)
        ) return;
        if (value !== state.displayScale) applyDisplayScale(value, { persist: true });
      });
    } catch (_error) {
      // LocalStorage remains the fallback.
    }
  }

  function setThemeMenuOpen(open) {
    elements.themeMenu.hidden = !open;
    elements.themeButton.setAttribute("aria-expanded", String(open));
    if (open) {
      elements.themeMenu.querySelector('[data-theme-choice][aria-checked="true"]')?.focus();
    }
  }

  function authHeaders() {
    const rawInitData = telegram?.initData;
    if (typeof rawInitData === "string" && rawInitData.length > 0) {
      return { Authorization: `tma ${rawInitData}` };
    }
    // A normal browser can work only when the server's explicit dev mode is on.
    return {};
  }

  async function parseApiError(response) {
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      // The generic fallback below intentionally reveals no server details.
    }
    const error = new Error(payload?.error?.message || "The service could not complete this request.");
    error.code = payload?.error?.code || "request_failed";
    error.status = response.status;
    error.reconcileRequired = payload?.reconcileRequired === true;
    error.reloadRequired = payload?.reloadRequired === true;
    error.day = payload?.day && typeof payload.day === "object" ? payload.day : null;
    error.dailies = Array.isArray(payload?.dailies) ? payload.dailies : null;
    error.resolvedDailyIds = gameplay?.normalizeIdList?.(
      payload?.resolvedDailyIds ?? payload?.scoredDailyIds,
    ) ?? [];
    error.unresolvedDailyIds = gameplay?.normalizeIdList?.(
      payload?.unresolvedDailyIds ?? payload?.remainingDailyIds,
    ) ?? [];
    error.profile = gameplay?.profileEnvelope?.(payload);
    return error;
  }

  async function requestJson(path, options = {}) {
    const headers = { ...authHeaders(), Accept: "application/json" };
    const requestOptions = {
      method: options.method || "GET",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      requestOptions.body = JSON.stringify(options.body);
    }
    const response = await window.fetch(path, requestOptions);
    if (!response.ok) throw await parseApiError(response);
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      const error = new Error("The service returned an invalid response.");
      error.code = "invalid_response";
      throw error;
    }
    if (!payload || payload.ok !== true) {
      const error = new Error("The service returned an incomplete response.");
      error.code = "invalid_response";
      throw error;
    }
    return payload;
  }

  function announce(message, tone = "success") {
    if (!elements.taskToast || typeof message !== "string" || !message) return;
    window.clearTimeout(toastTimer);
    elements.taskToast.textContent = message;
    elements.taskToast.dataset.tone = tone;
    elements.taskToast.hidden = false;
    toastTimer = window.setTimeout(() => {
      elements.taskToast.hidden = true;
      elements.taskToast.textContent = "";
    }, tone === "error" ? 6000 : 3200);
  }

  async function fetchProfile() {
    const response = await window.fetch(`${API_BASE}/me`, {
      method: "GET",
      headers: { ...authHeaders(), Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) throw await parseApiError(response);
    const payload = await response.json();
    if (!payload?.ok || typeof payload.profile !== "object" || typeof payload.stats !== "object") {
      throw new Error("The profile response was incomplete.");
    }
    return payload;
  }

  async function fetchAvatar(forceRefresh) {
    const suffix = forceRefresh ? "?refresh=1" : "";
    const response = await window.fetch(`${API_BASE}/avatar${suffix}`, {
      method: "GET",
      headers: { ...authHeaders(), Accept: "image/png" },
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) throw await parseApiError(response);
    if (!response.headers.get("content-type")?.toLowerCase().startsWith("image/png")) {
      throw new Error("The avatar response was invalid.");
    }
    const blob = await response.blob();
    if (!blob.size) throw new Error("The avatar response was empty.");
    return blob;
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }

  function displayStat(value) {
    const number = finiteNumber(value);
    if (number === null) return "—";
    if (number > 0 && number < 1) return "1";
    return String(Math.floor(number));
  }

  function displayGold(value) {
    const number = finiteNumber(value);
    if (number === null) return "—";
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(number);
  }

  function localIsoDate(value = new Date()) {
    const pad = (part) => String(part).padStart(2, "0");
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  }

  function questLogRows() {
    return [...document.querySelectorAll("[data-summary-tab]")];
  }

  function renderQuestLog(summary) {
    const copy = {
      habits: { noun: "Habits", verb: "scored" },
      dailies: { noun: "Dailies", verb: "completed" },
      todos: { noun: "To-Dos", verb: "completed" },
    };
    questLogRows().forEach((row) => {
      const key = row.dataset.summaryTab;
      const values = summary[key];
      const labels = copy[key];
      if (!values || !labels) return;
      row.querySelector("[data-summary-total]").textContent = String(values.total);
      row.querySelector("[data-summary-progress]").textContent = `${values.completed} / ${values.total} ${labels.verb}`;
      row.style.setProperty("--quest-progress", `${progressPercent(values.completed, values.total)}%`);
      row.setAttribute("aria-busy", "false");
      row.setAttribute("aria-label", `${values.completed} of ${values.total} ${labels.noun} ${labels.verb}`);
    });
    elements.questLogStatus.textContent = "Live";
    state.questSummaryLoaded = true;
  }

  function renderQuestLogError() {
    questLogRows().forEach((row) => {
      row.querySelector("[data-summary-total]").textContent = "—";
      row.querySelector("[data-summary-progress]").textContent = "Refresh to retry";
      row.style.setProperty("--quest-progress", "0%");
      row.setAttribute("aria-busy", "false");
    });
    elements.questLogStatus.textContent = "Unavailable";
  }

  function markQuestLogDirty() {
    state.questSummaryDirty = true;
    state.questSummaryRevision += 1;
    if (state.questSummaryLoaded) elements.questLogStatus.textContent = "Update pending";
  }

  async function loadQuestLog({ force = false } = {}) {
    // A summary request expands into four Habitica task-list reads. Always
    // share an in-flight request, including forced/manual refreshes, so rapid
    // task mutations cannot create overlapping batches.
    if (state.questSummaryRequest) return state.questSummaryRequest;
    if (!force && state.questSummaryLoaded && !state.questSummaryDirty) return true;
    const revision = state.questSummaryRevision;
    elements.questLogStatus.textContent = state.questSummaryLoaded ? "Updating" : "Loading";
    questLogRows().forEach((row) => row.setAttribute("aria-busy", "true"));
    let pending;
    let staleDuringRequest = false;
    pending = (async () => {
      try {
        const payload = await requestJson(`${API_BASE}/task-summary?today=${encodeURIComponent(localIsoDate())}`);
        const summary = window.HabiticaTaskUI.normalizeQuestLogSummary(payload);
        if (revision !== state.questSummaryRevision) {
          staleDuringRequest = true;
          if (state.questSummaryLoaded) elements.questLogStatus.textContent = "Update pending";
          return state.questSummaryLoaded;
        }
        renderQuestLog(summary);
        state.questSummaryDirty = false;
        return true;
      } catch (_error) {
        if (revision === state.questSummaryRevision) renderQuestLogError();
        return false;
      } finally {
        if (state.questSummaryRequest === pending) state.questSummaryRequest = null;
        // If data changed while Home was already loading, perform one trailing
        // refresh after the current batch instead of overlapping it.
        if (staleDuringRequest && state.activeTab === "home") void loadQuestLog();
      }
    })();
    state.questSummaryRequest = pending;
    return pending;
  }

  function progressPercent(current, maximum) {
    const safeCurrent = finiteNumber(current);
    const safeMaximum = finiteNumber(maximum);
    if (safeCurrent === null || safeMaximum === null || safeMaximum <= 0) return 0;
    return Math.min(100, Math.max(0, (safeCurrent / safeMaximum) * 100));
  }

  function updateStat(prefix, current, maximum) {
    document.getElementById(`${prefix}-value`).textContent = `${displayStat(current)} / ${displayStat(maximum)}`;
    const progress = document.getElementById(`${prefix}-progress`);
    progress.setAttribute("aria-hidden", "true");
    window.requestAnimationFrame(() => {
      progress.style.width = `${progressPercent(current, maximum)}%`;
    });
  }

  function updateMiniStat(prefix, current, maximum) {
    const value = document.getElementById(`mini-${prefix}-value`);
    const progress = document.getElementById(`mini-${prefix}-progress`);
    const track = document.getElementById(`mini-${prefix}-track`);
    if (!value || !progress || !track) return;
    const percent = progressPercent(current, maximum);
    value.textContent = `${displayStat(current)}/${displayStat(maximum)}`;
    track.setAttribute("aria-valuenow", String(Math.round(percent)));
    window.requestAnimationFrame(() => { progress.style.width = `${percent}%`; });
  }

  function renderMiniProfile(payload) {
    if (!elements.miniProfile) return;
    const profile = payload.profile;
    const stats = payload.stats;
    elements.miniLevel.textContent = Number.isInteger(profile.level) ? String(profile.level) : "0";
    elements.miniClass.textContent = typeof profile.classLabel === "string" ? profile.classLabel : "Adventurer";
    updateMiniStat("health", stats.hp, stats.maxHp);
    updateMiniStat("experience", stats.exp, stats.maxExp);
    const showMana = profile.hasClass === true && finiteNumber(stats.mp) !== null && finiteNumber(stats.maxMp) !== null;
    elements.miniManaRow.hidden = !showMana;
    if (showMana) updateMiniStat("mana", stats.mp, stats.maxMp);
    if (finiteNumber(stats.gold) !== null) {
      elements.miniGold.textContent = displayGold(stats.gold);
      elements.miniGoldWrap.hidden = false;
    } else {
      elements.miniGoldWrap.hidden = true;
    }
    elements.miniProfile.setAttribute("aria-busy", "false");
    elements.miniProfileRetry.hidden = true;
  }

  function renderProfile(payload) {
    state.profilePayload = {
      profile: { ...payload.profile },
      stats: { ...payload.stats },
    };
    const profile = payload.profile;
    const stats = payload.stats;
    elements.displayName.textContent = typeof profile.displayName === "string" ? profile.displayName : "Habitican";
    elements.username.textContent = typeof profile.username === "string" ? `@${profile.username}` : "Habitica hero";
    elements.level.textContent = Number.isInteger(profile.level) ? String(profile.level) : "0";
    elements.classLabel.textContent = typeof profile.classLabel === "string" ? profile.classLabel : "Adventurer";

    const className = typeof profile.class === "string" ? profile.class : "adventurer";
    elements.classMedallion.dataset.class = className;
    const classSymbols = { warrior: "⚔", rogue: "✦", wizard: "✧", healer: "✚", adventurer: "◆" };
    elements.classSymbol.textContent = classSymbols[className] || classSymbols.adventurer;
    elements.classMedallion.classList.remove("skeleton-pulse");
    elements.nameSkeleton.hidden = true;
    elements.metaSkeleton.hidden = true;
    elements.displayName.hidden = false;
    elements.identityMeta.hidden = false;

    updateStat("health", stats.hp, stats.maxHp);
    updateStat("experience", stats.exp, stats.maxExp);
    const showMana = profile.hasClass === true && finiteNumber(stats.mp) !== null && finiteNumber(stats.maxMp) !== null;
    elements.manaRow.hidden = !showMana;
    if (showMana) updateStat("mana", stats.mp, stats.maxMp);

    if (finiteNumber(stats.gold) !== null) {
      elements.goldValue.textContent = displayGold(stats.gold);
      elements.goldChip.hidden = false;
    } else {
      elements.goldChip.hidden = true;
    }

    elements.profileCard.setAttribute("aria-busy", "false");
    state.profileLoaded = true;
    renderMiniProfile(state.profilePayload);
  }

  function applyProfilePatch(patch) {
    if (!state.profilePayload || !patch || typeof patch !== "object") return false;
    const next = {
      profile: { ...state.profilePayload.profile },
      stats: { ...state.profilePayload.stats },
    };
    if (Number.isInteger(patch.level) && patch.level >= 0) next.profile.level = patch.level;
    ["hp", "maxHp", "exp", "maxExp", "mp", "maxMp", "gold"].forEach((key) => {
      if (finiteNumber(patch[key]) !== null) next.stats[key] = patch[key];
    });
    renderProfile(next);
    return true;
  }

  function applyProfileEnvelope(payload) {
    const envelope = gameplay?.profileEnvelope?.(payload);
    if (!envelope) return false;
    const isFullProfile = (
      typeof envelope.profile.displayName === "string"
      && typeof envelope.profile.classLabel === "string"
      && typeof envelope.profile.hasClass === "boolean"
    );
    // Gameplay mutations intentionally return only level/class and stats.  A
    // first-launch snapshot must not masquerade as a full /me response or it
    // would permanently replace the character name and class presentation
    // with fallbacks.  Once /me is loaded, the same snapshot is a safe patch.
    if (!state.profilePayload && !isFullProfile) return false;
    renderProfile(state.profilePayload ? {
      profile: { ...state.profilePayload.profile, ...envelope.profile },
      stats: { ...state.profilePayload.stats, ...envelope.stats },
    } : envelope);
    hideNotice();
    return true;
  }

  function mutationProfilePatch(payload) {
    if (payload?.profilePatch && typeof payload.profilePatch === "object") {
      return payload.profilePatch;
    }
    const envelope = gameplay?.profileEnvelope?.(payload);
    if (!envelope) return null;
    return {
      ...(Number.isInteger(envelope.profile.level) ? { level: envelope.profile.level } : {}),
      ...envelope.stats,
    };
  }

  function renderAvatar(blob) {
    if (state.avatarUrl) URL.revokeObjectURL(state.avatarUrl);
    state.avatarUrl = URL.createObjectURL(blob);
    elements.avatarImage.src = state.avatarUrl;
    elements.avatarImage.alt = `${elements.displayName.textContent || "Habitica"} avatar`;
    elements.avatarImage.hidden = false;
    elements.avatarPlaceholder.hidden = true;
    if (elements.miniAvatarImage) {
      elements.miniAvatarImage.src = state.avatarUrl;
      elements.miniAvatarImage.alt = `${elements.displayName.textContent || "Habitica"} avatar`;
      elements.miniAvatarImage.hidden = false;
      elements.miniAvatarPlaceholder.hidden = true;
    }
  }

  function renderAvatarError() {
    if (state.avatarUrl && !elements.avatarImage.hidden) return;
    if (state.avatarUrl) {
      URL.revokeObjectURL(state.avatarUrl);
      state.avatarUrl = null;
    }
    elements.avatarImage.hidden = true;
    elements.avatarPlaceholder.hidden = false;
    elements.avatarPlaceholder.classList.add("is-error");
    const label = elements.avatarPlaceholder.querySelector("span");
    if (label) label.textContent = "Avatar unavailable";
    elements.avatarPlaceholder.setAttribute("aria-label", "Avatar unavailable");
    if (elements.miniAvatarImage) {
      elements.miniAvatarImage.hidden = true;
      elements.miniAvatarPlaceholder.hidden = false;
    }
  }

  function errorCopy(error) {
    const copies = {
      invalid_telegram_session: ["Open from Telegram", "Close this page and reopen it from the bot menu."],
      habitica_not_linked: ["Connect Habitica first", "Send /start to the bot, link your account, then return here."],
      habitica_unavailable: ["Habitica is taking a break", "Your account is safe. Try refreshing in a moment."],
      service_unavailable: ["The app is busy", "Please wait a moment, then try again."],
    };
    return copies[error?.code] || ["Couldn’t load your profile", navigator.onLine ? "Please try again." : "Check your connection and try again."];
  }

  function showNotice(error) {
    const [title, message] = errorCopy(error);
    elements.noticeTitle.textContent = title;
    elements.noticeMessage.textContent = message;
    elements.notice.hidden = false;
    elements.profileCard.setAttribute("aria-busy", "false");
  }

  function hideNotice() {
    elements.notice.hidden = true;
  }

  function showProfileRefreshWarning() {
    if (elements.miniProfileRetry) elements.miniProfileRetry.hidden = false;
    announce("Task saved. Character status could not refresh yet.", "error");
  }

  function initializeProfileCoordinator() {
    if (!window.HabiticaTaskUI?.createProfileCoordinator) return;
    profileCoordinator = window.HabiticaTaskUI.createProfileCoordinator({
      fetchProfile,
      applyProfile: (payload) => {
        renderProfile(payload);
        hideNotice();
      },
      applyPatch: applyProfilePatch,
      warn: showProfileRefreshWarning,
      delay: 180,
    });
  }

  async function loadProfile() {
    try {
      if (!profileCoordinator) initializeProfileCoordinator();
      if (profileCoordinator) {
        const result = await profileCoordinator.load();
        if (!result.applied) return result.stale === true ? state.profileLoaded : false;
      } else {
        renderProfile(await fetchProfile());
      }
      hideNotice();
      return true;
    } catch (error) {
      showNotice(error);
      return false;
    }
  }

  function applyConfirmedProfilePatch(detail = {}) {
    const sequence = Number.isInteger(detail.sequence) ? detail.sequence : 0;
    const isNewest = sequence >= state.profileMutationSequence;
    if (!isNewest) return false;
    state.profileMutationSequence = sequence;
    const patch = detail.profilePatch;
    if (!patch || typeof patch !== "object" || Object.keys(patch).length === 0) {
      showProfileRefreshWarning();
      return false;
    }
    return applyProfilePatch(patch);
  }

  async function ensureTaskProfileLoaded() {
    if (state.taskProfileStarted) return;
    state.taskProfileStarted = true;
    const profileOk = state.profileLoaded || await loadProfile();
    if (!profileOk) {
      if (elements.miniProfileRetry) elements.miniProfileRetry.hidden = false;
      return;
    }
    if (!state.avatarUrl && !(await loadAvatar(false))) {
      if (elements.miniProfileRetry) elements.miniProfileRetry.hidden = false;
    }
  }

  async function loadAvatar(forceRefresh) {
    if (!forceRefresh && state.avatarRequest) return state.avatarRequest;
    const generation = state.avatarGeneration + 1;
    state.avatarGeneration = generation;
    let request;
    request = (async () => {
      try {
        const blob = await fetchAvatar(forceRefresh);
        if (generation !== state.avatarGeneration) return Boolean(state.avatarUrl);
        renderAvatar(blob);
        return true;
      } catch (_error) {
        if (generation === state.avatarGeneration) renderAvatarError();
        return false;
      } finally {
        if (state.avatarRequest === request) state.avatarRequest = null;
      }
    })();
    state.avatarRequest = request;
    return request;
  }

  async function refreshAll({ forceAvatar = false, userInitiated = false } = {}) {
    if (state.refreshing) return false;
    state.homeStarted = true;
    state.refreshing = true;
    hideNotice();
    elements.refreshButton.disabled = true;
    elements.refreshButton.classList.add("is-spinning");
    elements.profileCard.classList.add("is-refreshing");
    if (userInitiated) haptic("impact", "light");

    // PythonAnywhere free web apps may have one worker. Profile and compact task
    // counts load before the expensive first avatar render.
    const profileOk = await loadProfile();
    const summaryOk = await loadQuestLog({ force: userInitiated });
    const avatarOk = await loadAvatar(forceAvatar);

    state.refreshing = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.classList.remove("is-spinning");
    elements.profileCard.classList.remove("is-refreshing");
    if (userInitiated) haptic("notification", profileOk && summaryOk && avatarOk ? "success" : "error");
    return profileOk && summaryOk && avatarOk;
  }

  function ensureHomeLoaded() {
    if (state.homeStarted) {
      if (!state.questSummaryLoaded || state.questSummaryDirty) void loadQuestLog();
      return;
    }
    if (state.profileLoaded) {
      state.homeStarted = true;
      if (!state.questSummaryLoaded || state.questSummaryDirty) void loadQuestLog();
      if (!state.avatarUrl) void loadAvatar(false);
      return;
    }
    void refreshAll();
  }

  function setStartupState(next) {
    if (!gameplay?.STARTUP_STATES?.includes(next)) return;
    startupMachine?.transition?.(next);
    state.startupState = next;
    const ready = next === "ready";
    elements.dayGate.dataset.startupState = next;
    elements.dayGate.setAttribute("aria-busy", String([
      "checking_day",
      "submitting_review",
      "refreshing_day",
    ].includes(next)));
    elements.dayGate.hidden = ready;
    document.body.classList.toggle("is-day-gated", !ready);
    elements.appShell.toggleAttribute("inert", !ready);
    elements.appShell.setAttribute("aria-hidden", String(!ready));
    elements.potionFab.hidden = !ready;
    if (!ready) elements.taskFab.hidden = true;
    updateTelegramBackButton();
  }

  function setDayGatePanel(panel) {
    elements.dayGateChecking.hidden = panel !== "checking";
    elements.dayGateReview.hidden = panel !== "review";
    elements.dayGateFailure.hidden = panel !== "failure";
    const labels = {
      checking: "day-gate-title",
      review: "day-review-title",
      failure: "day-gate-failure-title",
    };
    if (labels[panel]) elements.dayGate.setAttribute("aria-labelledby", labels[panel]);
  }

  function dayErrorMessage(error) {
    const copies = {
      invalid_telegram_session: "Open the Mini App from Telegram, then try again.",
      habitica_not_linked: "Connect your Habitica account with /start before opening the Mini App.",
      habitica_unauthorized: "Habitica rejected the saved connection. Relink with /start.",
      unauthorized: "Habitica rejected the saved connection. Relink with /start.",
      rate_limited: "Habitica is receiving too many requests. Wait a moment and try again.",
      invalid_daily_selection: "The available Dailies changed. Review the updated list and try again.",
      batch_incomplete: "Recorded this batch. Wait about a minute, then submit the remaining Dailies.",
      daily_score_failed: "Some selected Dailies could not be recorded. Only unresolved selections remain checked.",
      cron_failed: "Your selected Dailies are safe, but Habitica did not start the new day. Try again.",
      habitica_unavailable: "Habitica could not confirm your day. Your tasks have not been changed.",
      service_unavailable: "The app is busy. Wait a moment and try again.",
    };
    return copies[error?.code] || error?.message || "Habitica could not confirm your day. Please try again.";
  }

  function updateDaySelection() {
    const eligible = new Set((state.dayReview?.dailies || []).map((daily) => daily.id));
    state.selectedDailyIds = new Set([...state.selectedDailyIds].filter((id) => eligible.has(id)));
    const count = state.selectedDailyIds.size;
    elements.dayReviewSelection.textContent = count
      ? `${count} ${count === 1 ? "Daily" : "Dailies"} selected to record as complete.`
      : "No Dailies selected. Habitica will process every unchecked due Daily as missed.";
    elements.dayReviewSubmit.textContent = state.daySubmitting
      ? (state.startupState === "refreshing_day" ? "Starting New Day…" : "Recording Activity…")
      : (state.dayOutcomeUnknown
          ? "Check Day Status"
          : (state.dayReview?.dailies?.length ? "Start My Day" : "Start New Day"));
    elements.dayReviewSubmit.disabled = state.daySubmitting;
    elements.dayReviewList.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.disabled = state.daySubmitting;
      input.checked = state.selectedDailyIds.has(input.dataset.dailyId);
    });
  }

  function createReviewChecklist(doc, daily) {
    if (!daily.checklist.length) return null;
    const list = doc.createElement("ul");
    list.className = "day-review-task__checklist";
    daily.checklist.forEach((item) => {
      const row = doc.createElement("li");
      const marker = doc.createElement("span");
      marker.setAttribute("aria-hidden", "true");
      marker.textContent = item.completed ? "✓" : "○";
      const text = doc.createElement("span");
      text.textContent = item.text;
      row.append(marker, text);
      list.append(row);
    });
    return list;
  }

  function renderDayReview(errorMessage = "") {
    const day = state.dayReview;
    if (!day) return;
    elements.dayReviewLabel.textContent = day.reviewLabel;
    elements.dayReviewLead.textContent = day.daysMissed > 1
      ? `You have been away for ${day.daysMissed} Habitica days. Record only the Dailies Habitica can still verify below.`
      : "Check any Dailies you completed yesterday but forgot to record.";
    elements.dayReviewWarning.textContent = day.daysMissed > 1
      ? "Selected tasks will be recorded before Habitica processes the missed time. The visible list does not reconstruct every missed day."
      : "Selected Dailies will be recorded as complete. Unselected due Dailies may cause Habitica damage.";
    elements.dayReviewError.textContent = errorMessage;
    elements.dayReviewError.hidden = !errorMessage;
    elements.dayReviewList.replaceChildren();

    if (!day.dailies.length) {
      const empty = document.createElement("div");
      empty.className = "day-review-empty";
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "✓";
      const heading = document.createElement("strong");
      heading.textContent = "All Dailies were completed";
      const copy = document.createElement("p");
      copy.textContent = "There is nothing left to record before Habitica starts the new day.";
      empty.append(icon, heading, copy);
      elements.dayReviewList.append(empty);
    } else {
      day.dailies.forEach((daily, index) => {
        const label = document.createElement("label");
        label.className = "day-review-task";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.dailyId = daily.id;
        input.id = `day-review-daily-${index}`;
        input.checked = state.selectedDailyIds.has(daily.id);
        input.disabled = state.daySubmitting;
        input.addEventListener("change", () => {
          if (input.checked) state.selectedDailyIds.add(daily.id);
          else state.selectedDailyIds.delete(daily.id);
          updateDaySelection();
          haptic("selection");
        });
        const copy = document.createElement("span");
        copy.className = "day-review-task__copy";
        const title = document.createElement("strong");
        title.textContent = daily.text || "Untitled Daily";
        copy.append(title);
        if (daily.notes) {
          const notes = document.createElement("span");
          notes.className = "day-review-task__notes";
          notes.textContent = daily.notes;
          copy.append(notes);
        }
        const checklist = createReviewChecklist(document, daily);
        if (checklist) copy.append(checklist);
        label.append(input, copy);
        elements.dayReviewList.append(label);
      });
    }
    updateDaySelection();
  }

  function showDayReview(day, { errorMessage = "", preserveSelection = false } = {}) {
    state.dayReview = day;
    const eligible = new Set(day.dailies.map((daily) => daily.id));
    state.selectedDailyIds = preserveSelection
      ? new Set([...state.selectedDailyIds].filter((id) => eligible.has(id)))
      : new Set();
    state.daySubmitting = false;
    setStartupState(errorMessage ? "refresh_failed" : "review_required");
    setDayGatePanel("review");
    renderDayReview(errorMessage);
    window.requestAnimationFrame(() => elements.dayReviewTitle.focus({ preventScroll: true }));
  }

  function showDayCheckFailure(error) {
    state.daySubmitting = false;
    setStartupState("refresh_failed");
    setDayGatePanel("failure");
    elements.dayGateFailureMessage.textContent = dayErrorMessage(error);
    window.requestAnimationFrame(() => elements.dayGateFailure.querySelector("h1")?.focus({ preventScroll: true }));
  }

  function invalidatedTaskTypes(payload, fallbackAll = false) {
    const flags = payload?.invalidate && typeof payload.invalidate === "object" ? payload.invalidate : {};
    const types = [];
    if (flags.habits === true || fallbackAll) types.push("habit");
    if (flags.dailies === true || fallbackAll) types.push("daily");
    if (flags.todos === true) types.push("todo");
    if (Array.isArray(payload?.refreshTasks)) {
      payload.refreshTasks.forEach((type) => {
        const normalized = window.HabiticaTaskUI?.canonicalType?.(type);
        if (normalized && !types.includes(normalized)) types.push(normalized);
      });
    }
    return types;
  }

  async function resolveStartup(payload, { afterRefresh = false, message = "" } = {}) {
    const appliedProfile = applyProfileEnvelope(payload);
    if ((payload?.invalidate?.profile === true || afterRefresh) && !appliedProfile) {
      await loadProfile();
    }
    const types = invalidatedTaskTypes(payload, afterRefresh);
    if (types.length) {
      taskController?.invalidate?.(types);
      await taskController?.refreshTypes?.(types);
      void loadQuestLog({ force: true });
    }
    state.dayReview = null;
    state.selectedDailyIds.clear();
    state.daySubmitting = false;
    state.dayOutcomeUnknown = false;
    state.dayGatePending = false;
    state.startupResolved = true;
    setStartupState("ready");
    const destination = afterRefresh ? "home" : state.requestedTab;
    activateTab(destination, { initial: true, bypassGate: true });
    if (afterRefresh) {
      announce(message || "New day started.", "success");
      haptic("notification", "success");
    }
  }

  async function checkDayStatus({ preserveSelection = false } = {}) {
    if (state.daySubmitting) return false;
    const resolvingExistingGameplay = state.startupResolved
      || state.dayReview !== null
      || state.dayOutcomeUnknown;
    const sequence = state.startupRequestSequence + 1;
    state.startupRequestSequence = sequence;
    setStartupState("checking_day");
    setDayGatePanel("checking");
    try {
      const payload = await requestJson(`${API_BASE}/day-status`, { method: "GET" });
      if (sequence !== state.startupRequestSequence) return false;
      applyProfileEnvelope(payload);
      const day = gameplay.normalizeDayPayload(payload);
      if (day.refreshRequired) {
        showDayReview(day, {
          preserveSelection,
          errorMessage: state.dayOutcomeUnknown
            ? "The previous refresh result is still unknown. Check Habitica before starting it again; no second refresh will be sent in this session."
            : "",
        });
      } else {
        await resolveStartup(payload, {
          afterRefresh: resolvingExistingGameplay,
          message: resolvingExistingGameplay ? "Day was already refreshed. Current data loaded." : "",
        });
      }
      return true;
    } catch (error) {
      if (sequence !== state.startupRequestSequence) return false;
      showDayCheckFailure(error);
      return false;
    }
  }

  async function submitDayReview() {
    if (state.daySubmitting || !state.dayReview) return;
    if (state.dayOutcomeUnknown) {
      await checkDayStatus({ preserveSelection: true });
      return;
    }
    state.daySubmitting = true;
    setStartupState("submitting_review");
    setDayGatePanel("review");
    renderDayReview();
    haptic("impact", "medium");
    try {
      const eligible = new Set(state.dayReview.dailies.map((daily) => daily.id));
      const completedDailyIds = [...state.selectedDailyIds].filter((id) => eligible.has(id));
      const payload = await requestJson(`${API_BASE}/day-refresh`, {
        method: "POST",
        body: { completedDailyIds },
      });
      setStartupState("refreshing_day");
      setDayGatePanel("review");
      updateDaySelection();
      await resolveStartup(payload, {
        afterRefresh: true,
        message: payload.status === "already_refreshed" ? "Day was already refreshed. Current data loaded." : "New day started.",
      });
    } catch (error) {
      if (error?.code === "already_refreshed") {
        setStartupState("refreshing_day");
        await resolveStartup({ profile: error.profile, invalidate: { profile: true, habits: true, dailies: true, todos: false, avatar: false } }, {
          afterRefresh: true,
          message: "Day was already refreshed. Current data loaded.",
        });
        return;
      }

      if (error?.code === "invalid_daily_selection") {
        state.daySubmitting = false;
        await checkDayStatus({ preserveSelection: true });
        return;
      }

      let dailies = error?.dailies;
      if (!dailies && error?.day?.dailies) dailies = error.day.dailies;
      if (dailies) {
        const normalized = gameplay.normalizeReviewDailies(dailies);
        state.dayReview = {
          ...state.dayReview,
          ...(error.day && Number.isFinite(error.day.daysMissed) ? { daysMissed: Math.max(1, Math.floor(error.day.daysMissed)) } : {}),
          dailies: normalized,
        };
      }
      if (error?.day) {
        state.dayReview = gameplay.normalizeDayPayload({
          day: {
            ...state.dayReview,
            ...error.day,
            refreshRequired: true,
            dailies: state.dayReview.dailies,
          },
        });
      }
      const resolved = new Set(error?.resolvedDailyIds || []);
      if (resolved.size) {
        state.dayReview = {
          ...state.dayReview,
          dailies: state.dayReview.dailies.filter((daily) => !resolved.has(daily.id)),
        };
      }
      const explicitlyUnresolved = new Set(error?.unresolvedDailyIds || []);
      if (explicitlyUnresolved.size) {
        state.dayReview = {
          ...state.dayReview,
          dailies: state.dayReview.dailies.filter((daily) => explicitlyUnresolved.has(daily.id)),
        };
      }
      state.selectedDailyIds = gameplay.reconcileReviewSelection(state.selectedDailyIds, {
        resolvedDailyIds: error?.resolvedDailyIds,
        unresolvedDailyIds: error?.unresolvedDailyIds,
        dailies: state.dayReview.dailies,
      });
      if (error?.reconcileRequired === true) state.dayOutcomeUnknown = true;
      state.daySubmitting = false;
      setStartupState("refresh_failed");
      setDayGatePanel("review");
      let message = error?.reconcileRequired === true
        ? "The refresh result could not be confirmed. Check Habitica before trying again; this Mini App will not send a second refresh in this session."
        : dayErrorMessage(error);
      const unresolvedNames = new Map(state.dayReview.dailies.map((daily) => [daily.id, daily.text]));
      const unresolved = (error?.unresolvedDailyIds || [])
        .map((id) => unresolvedNames.get(id))
        .filter(Boolean);
      if (unresolved.length) message += ` Still unresolved: ${unresolved.join(", ")}.`;
      renderDayReview(message);
      haptic("notification", "error");
    }
  }

  async function requireDayReview() {
    if (state.startupState !== "ready" || state.dayGatePending) return;
    state.requestedTab = state.activeTab;
    state.dayGatePending = true;
    closeTopOverlay();
    if (gameplayDialogsOpen()) {
      announce("Review yesterday first. Close or discard the open editor to continue.", "error");
      return;
    }
    await continuePendingDayGate();
  }

  function potionErrorMessage(error) {
    const copies = {
      health_already_full: "Your health is already full.",
      not_enough_gold: "You do not have enough gold for a Health Potion.",
      duplicate_request: "A potion purchase is already in progress.",
      rate_limited: "Habitica is receiving too many requests. Try again shortly.",
      habitica_unauthorized: "Habitica rejected the saved connection. Relink with /start.",
      unauthorized: "Habitica rejected the saved connection. Relink with /start.",
      habitica_unavailable: "Habitica could not complete the purchase. No automatic retry was made.",
      purchase_failed: "Habitica did not complete the purchase. Your stats were not changed here.",
      invalid_response: "Habitica returned an invalid potion response. Please try later.",
      invalid_purchase_intent: "This confirmation expired. Close and reopen the Potion dialog.",
      intent_unavailable: "A purchase confirmation is temporarily unavailable. Please try again shortly.",
    };
    return copies[error?.code] || error?.message || "Could not buy the Health Potion. Please try again.";
  }

  function renderPotionDialog() {
    const stats = state.potionInfo?.stats || state.profilePayload?.stats || {};
    elements.potionDialogTitle.textContent = `Use ${state.potionInfo?.name || "Health Potion"}?`;
    elements.potionHealth.textContent = `${displayStat(stats.hp)} / ${displayStat(stats.maxHp)}`;
    elements.potionGold.textContent = displayGold(stats.gold);
    const price = state.potionInfo?.price;
    const healing = state.potionInfo?.healing;
    elements.potionPriceRow.hidden = finiteNumber(price) === null;
    elements.potionHealingRow.hidden = finiteNumber(healing) === null;
    if (finiteNumber(price) !== null) elements.potionPrice.textContent = `${displayGold(price)} gold`;
    if (finiteNumber(healing) !== null) elements.potionHealing.textContent = `${displayStat(healing)} HP`;
    const hp = finiteNumber(stats.hp);
    const maxHp = finiteNumber(stats.maxHp);
    const full = state.potionInfo?.healthFull === true || (hp !== null && maxHp !== null && hp >= maxHp);
    const cannotAfford = state.potionInfo?.canAfford === false;
    const hasPurchaseIntent = typeof state.potionPurchaseIntent === "string";
    elements.potionSubmit.textContent = state.potionSubmitting
      ? "Buying…"
      : (state.potionLoading
          ? "Loading…"
          : (state.potionOutcomeUnknown
              ? "Check Habitica First"
              : (full
                  ? "Health is Full"
                  : (cannotAfford
                      ? "Not Enough Gold"
                       : (hasPurchaseIntent
                           ? `Drink Potion${finiteNumber(price) === null ? "" : ` (${displayGold(price)} GP)`}`
                           : "Reopen to Retry")))));
    elements.potionSubmit.disabled = state.potionLoading
      || state.potionSubmitting
      || state.potionOutcomeUnknown
      || !state.potionInfo
      || !hasPurchaseIntent
      || full
      || cannotAfford;
  }

  async function openPotionDialog() {
    if (state.startupState !== "ready" || state.potionLoading || state.potionSubmitting) return;
    state.potionInfo = null;
    state.potionPurchaseIntent = null;
    state.potionLoading = true;
    elements.potionError.hidden = !state.potionOutcomeUnknown;
    elements.potionError.textContent = state.potionOutcomeUnknown
      ? "The earlier purchase result is still unknown. Check Habitica directly; this Mini App will not send another purchase in this session."
      : "";
    renderPotionDialog();
    showDialog(elements.potionDialog);
    let dayReviewRequired = false;
    try {
      const payload = await requestJson(`${API_BASE}/health-potion`, { method: "GET" });
      const purchaseIntent = typeof payload.purchaseIntent === "string"
        && /^[A-Za-z0-9_-]{32,128}$/.test(payload.purchaseIntent)
        ? payload.purchaseIntent
        : null;
      if (!purchaseIntent) {
        throw Object.assign(new Error("Potion response was incomplete."), { code: "invalid_response" });
      }
      state.potionInfo = gameplay.normalizePotionPayload(payload);
      state.potionPurchaseIntent = purchaseIntent;
      if (state.profilePayload && state.potionInfo.stats) applyProfilePatch(state.potionInfo.stats);
    } catch (error) {
      if (error?.code === "day_refresh_required") dayReviewRequired = true;
      else {
        elements.potionError.textContent = potionErrorMessage(error);
        elements.potionError.hidden = false;
      }
    } finally {
      state.potionLoading = false;
      renderPotionDialog();
    }
    if (dayReviewRequired) {
      closeDialog(elements.potionDialog);
      await requireDayReview();
    }
  }

  function closePotionDialog() {
    if (state.potionSubmitting) return;
    state.potionPurchaseIntent = null;
    closeDialog(elements.potionDialog);
    elements.potionFab.focus({ preventScroll: true });
  }

  async function submitPotion(event) {
    event.preventDefault();
    if (
      state.startupState !== "ready"
      || state.potionLoading
      || state.potionSubmitting
      || state.potionOutcomeUnknown
      || !state.potionInfo
      || typeof state.potionPurchaseIntent !== "string"
    ) return;
    state.potionSubmitting = true;
    elements.potionError.hidden = true;
    renderPotionDialog();
    haptic("impact", "medium");
    try {
      const payload = await requestJson(`${API_BASE}/health-potion`, {
        method: "POST",
        body: { purchaseIntent: state.potionPurchaseIntent },
      });
      const patch = mutationProfilePatch(payload);
      // A successful Habitica response confirms the mutation and carries the
      // new character statistics. Do not spend another request re-reading /me.
      state.potionPurchaseIntent = null;
      if (patch && Object.keys(patch).length > 0 && state.profilePayload) {
        applyProfilePatch(patch);
      } else {
        showProfileRefreshWarning();
      }
      state.potionSubmitting = false;
      closeDialog(elements.potionDialog);
      announce("Health Potion used.", "success");
      haptic("notification", "success");
    } catch (error) {
      state.potionSubmitting = false;
      if (error?.code === "day_refresh_required") {
        state.potionPurchaseIntent = null;
        closeDialog(elements.potionDialog);
        await requireDayReview();
        return;
      }
      if (error?.code === "health_already_full" && state.potionInfo) state.potionInfo.healthFull = true;
      if (error?.code === "not_enough_gold" && state.potionInfo) state.potionInfo.canAfford = false;
      if (error?.reconcileRequired === true) {
        state.potionOutcomeUnknown = true;
        state.potionPurchaseIntent = null;
      } else if (error?.status && error?.code !== "duplicate_request") {
        // Valid intents are terminal after any service response. A transport
        // failure has no status and can safely retry the same intent/replay.
        state.potionPurchaseIntent = null;
      }
      elements.potionError.textContent = error?.reconcileRequired === true
        ? "The purchase result could not be confirmed. Check Habitica before trying again; this Mini App will not send another purchase in this session."
        : potionErrorMessage(error);
      elements.potionError.hidden = false;
      renderPotionDialog();
      haptic("notification", "error");
    }
  }

  function installGameplayControls() {
    elements.dayGateRetry.addEventListener("click", () => { void checkDayStatus({ preserveSelection: true }); });
    elements.dayReviewSubmit.addEventListener("click", () => { void submitDayReview(); });
    elements.potionFab.addEventListener("click", () => { void openPotionDialog(); });
    elements.potionClose.addEventListener("click", closePotionDialog);
    elements.potionCancel.addEventListener("click", closePotionDialog);
    elements.potionForm.addEventListener("submit", submitPotion);
    elements.potionDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closePotionDialog();
    });
  }

  function dialogIsOpen(dialog) {
    return Boolean(dialog?.open || dialog?.hasAttribute?.("open"));
  }

  function gameplayDialogsOpen() {
    return [
      elements.editorDialog,
      elements.deleteDialog,
      elements.quickAddDialog,
      elements.discardDialog,
      elements.potionDialog,
    ].some(dialogIsOpen);
  }

  async function continuePendingDayGate() {
    if (!state.dayGatePending || state.startupState !== "ready" || gameplayDialogsOpen()) return false;
    state.dayGatePending = false;
    return checkDayStatus({ preserveSelection: true });
  }

  function showDialog(dialog) {
    if (!dialog || dialogIsOpen(dialog)) return;
    document.body.classList.add("has-dialog");
    const shell = document.querySelector(".safe-area-shell");
    if (shell) shell.inert = true;
    try {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } catch (_error) {
      dialog.setAttribute("open", "");
    }
    if ([elements.editorDialog, elements.quickAddDialog, elements.potionDialog].includes(dialog)) {
      history.pushState({ miniappOverlay: dialog.id, tab: state.activeTab }, "", window.location.href);
    }
    updateTelegramBackButton();
  }

  function closeDialog(dialog, { unwindHistory = true } = {}) {
    if (!dialog || !dialogIsOpen(dialog)) return;
    try {
      if (typeof dialog.close === "function") dialog.close();
      else {
        dialog.removeAttribute("open");
        dialog.dispatchEvent(new Event("close"));
      }
    } catch (_error) {
      dialog.removeAttribute("open");
      dialog.dispatchEvent(new Event("close"));
    }
    if (
      unwindHistory
      && history.state?.miniappOverlay === dialog.id
      && [elements.editorDialog, elements.quickAddDialog, elements.potionDialog].includes(dialog)
    ) {
      state.ignoreNextPopstate = true;
      history.back();
    }
  }

  function updateTelegramBackButton() {
    if (!telegram?.BackButton || !isTelegramVersionAtLeast("6.1")) return;
    try {
      if (
        dialogIsOpen(elements.editorDialog)
        || dialogIsOpen(elements.deleteDialog)
        || dialogIsOpen(elements.quickAddDialog)
        || dialogIsOpen(elements.discardDialog)
        || dialogIsOpen(elements.potionDialog)
        || state.startupState !== "ready"
      ) {
        telegram.BackButton.show();
      } else {
        telegram.BackButton.hide();
      }
    } catch (_error) {
      // Telegram BackButton is a progressive enhancement.
    }
  }

  function closeTopOverlay() {
    if (dialogIsOpen(elements.potionDialog) && !state.potionSubmitting) {
      closePotionDialog();
      return true;
    }
    if (dialogIsOpen(elements.discardDialog)) {
      closeDialog(elements.discardDialog);
      return true;
    }
    if (dialogIsOpen(elements.deleteDialog) && !state.deleteSubmitting) {
      closeDialog(elements.deleteDialog);
      return true;
    }
    if (dialogIsOpen(elements.quickAddDialog) && !state.quickAddSubmitting) {
      closeDialog(elements.quickAddDialog);
      return true;
    }
    if (dialogIsOpen(elements.editorDialog) && !state.editorSubmitting) {
      requestCloseTaskEditor();
      return true;
    }
    if (!elements.themeMenu.hidden) {
      setThemeMenuOpen(false);
      elements.themeButton.focus();
      return true;
    }
    if (state.startupState !== "ready") {
      try {
        telegram?.close?.();
      } catch (_error) {
        // Leaving the review unresolved is safer than silently running cron.
      }
      return true;
    }
    return false;
  }

  function taskTypeLabel(type) {
    return { habit: "Habit", daily: "Daily", todo: "Todo" }[type] || "Task";
  }

  function setEditorError(message, fieldName = null) {
    elements.editorError.textContent = typeof message === "string" ? message : "Please review this task.";
    elements.editorError.hidden = false;
    const fieldMap = {
      text: elements.taskTitle,
      notes: elements.taskNotes,
      priority: elements.taskPriority,
      directions: elements.habitUp,
      repeatDays: document.querySelector('[name="repeat-day"]'),
      startDate: elements.dailyStartDate,
      date: elements.todoDueDate,
      checklist: elements.checklistAdd,
    };
    const direct = fieldName && fieldName.startsWith("checklist.")
      ? elements.checklistItems.querySelectorAll(".checklist-editor__text")[Number(fieldName.split(".")[1])]
      : fieldMap[fieldName];
    direct?.focus?.();
  }

  function clearEditorError() {
    elements.editorError.hidden = true;
    elements.editorError.textContent = "";
  }

  function createChecklistEditorRow(item = {}) {
    const row = document.createElement("div");
    row.className = "checklist-editor__row";
    if (typeof item.id === "string" && item.id) row.dataset.itemId = item.id;
    row.dataset.completed = String(item.completed === true);

    const completionLabel = document.createElement("label");
    completionLabel.className = "checklist-editor__complete-label";
    const completion = document.createElement("input");
    completion.className = "checklist-editor__complete";
    completion.type = "checkbox";
    completion.checked = item.completed === true;
    completion.disabled = !row.dataset.itemId;
    const completionText = document.createElement("span");
    completionText.className = "visually-hidden";
    completionText.textContent = `Mark ${typeof item.text === "string" && item.text ? item.text : "checklist item"} complete`;
    completionLabel.append(completion, completionText);

    const label = document.createElement("label");
    const labelText = document.createElement("span");
    labelText.className = "visually-hidden";
    labelText.textContent = "Checklist item";
    const input = document.createElement("input");
    input.className = "checklist-editor__text";
    input.type = "text";
    input.maxLength = 500;
    input.required = true;
    input.autocomplete = "off";
    input.value = typeof item.text === "string" ? item.text : "";
    label.append(labelText, input);

    const remove = document.createElement("button");
    remove.className = "checklist-editor__remove";
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove checklist item${input.value ? `: ${input.value}` : ""}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      const next = row.nextElementSibling?.querySelector("input")
        || row.previousElementSibling?.querySelector("input")
        || elements.checklistAdd;
      row.remove();
      updateEditorDirty();
      next?.focus();
    });
    completion.addEventListener("change", async () => {
      const itemId = row.dataset.itemId;
      const taskId = state.editorTask?.id;
      const taskType = state.editorType;
      const editorGeneration = state.editorGeneration;
      const previous = row.dataset.completed === "true";
      const desired = completion.checked;
      if (!itemId || !taskId || !taskController || !["daily", "todo"].includes(taskType)) {
        completion.checked = previous;
        return;
      }
      completion.disabled = true;
      clearEditorError();
      const sameEditorSession = () => (
        state.editorGeneration === editorGeneration
        && dialogIsOpen(elements.editorDialog)
        && state.editorTask?.id === taskId
        && state.editorType === taskType
      );
      const sameVisibleEditorRow = () => sameEditorSession() && row.isConnected;
      try {
        const updatedTask = await taskController.scoreChecklist(
          taskType,
          taskId,
          itemId,
          desired ? "up" : "down",
        );
        if (!sameEditorSession()) return;
        if (!updatedTask) {
          if (row.isConnected) completion.checked = previous;
          return;
        }
        state.editorTask = updatedTask;
        if (!row.isConnected) return;
        const updatedItem = updatedTask.checklist.find((candidate) => candidate.id === itemId);
        const completed = updatedItem ? updatedItem.completed === true : desired;
        row.dataset.completed = String(completed);
        completion.checked = completed;
        elements.editorDialog.dataset.taskColor = updatedTask.colorToken || "neutral";
      } catch (error) {
        if (!sameVisibleEditorRow()) return;
        completion.checked = previous;
        row.dataset.completed = String(previous);
        setEditorError(error?.message || "This checklist item could not be updated.");
      } finally {
        if (sameVisibleEditorRow()) completion.disabled = state.editorReadOnly || !row.dataset.itemId;
      }
    });
    row.append(completionLabel, label, remove);
    elements.checklistItems.append(row);
    return input;
  }

  function resetEditorFields(type, task) {
    elements.editorForm.reset();
    clearEditorError();
    elements.checklistItems.replaceChildren();
    elements.taskTitle.value = task?.text || "";
    elements.taskNotes.value = task?.notes || "";
    elements.taskPriority.value = String(task?.priority ?? 1);
    elements.habitUp.checked = task ? task.up === true : true;
    elements.habitDown.checked = task ? task.down === true : true;
    elements.todoDueDate.value = task?.date || "";
    elements.dailyStartDate.value = task?.startDate || "";

    document.querySelectorAll("[data-editor-fields]").forEach((section) => {
      section.hidden = section.dataset.editorFields !== type;
    });
    elements.checklistEditor.hidden = !["daily", "todo"].includes(type);

    const simpleSchedule = document.querySelector("[data-daily-simple-schedule]");
    const advancedNotice = document.querySelector("[data-daily-advanced-notice]");
    const scheduleEditable = type === "daily" && (!task || task.scheduleEditable === true);
    simpleSchedule.hidden = type !== "daily" || !scheduleEditable;
    advancedNotice.hidden = type !== "daily" || scheduleEditable;
    const selectedDays = task ? new Set(task.repeatDays || []) : new Set(window.HabiticaTaskUI.DAY_KEYS);
    document.querySelectorAll('[name="repeat-day"]').forEach((input) => {
      input.checked = selectedDays.has(input.value);
    });
    (task?.checklist || []).forEach(createChecklistEditorRow);
  }

  function openTaskEditor(type, task = null, initialText = "") {
    if (!window.HabiticaTaskUI.TYPES.includes(type)) return;
    state.editorGeneration += 1;
    state.editorType = type;
    state.editorTask = task;
    state.editorMode = task ? "edit" : "create";
    state.editorReadOnly = Boolean(task && task.canEdit !== true);
    state.editorTrigger = document.activeElement;
    state.editorFocusKey = state.editorTrigger?.dataset?.focusKey || null;
    state.editorSubmitting = false;
    const label = taskTypeLabel(type);
    elements.editorEyebrow.textContent = task ? `Edit ${label}` : "New quest";
    elements.editorTitle.textContent = task ? `Edit ${label}` : `Add ${label}`;
    elements.editorSubmit.textContent = task ? "Save changes" : `Create ${label}`;
    resetEditorFields(type, task);
    if (!task && initialText) elements.taskTitle.value = initialText;
    elements.editorDialog.dataset.taskColor = task?.colorToken || "create";
    elements.editorDelete.hidden = !task || task.canDelete !== true;
    elements.editorDelete.setAttribute("aria-label", `Delete ${task?.text || label}`);
    elements.editorSubmit.hidden = state.editorReadOnly;
    state.editorInitialDraft = canonicalEditorDraft();
    state.editorDirty = false;
    setEditorSubmitting(false);
    showDialog(elements.editorDialog);
    window.requestAnimationFrame(() => elements.taskTitle.focus());
  }

  function collectChecklistDraft() {
    return [...elements.checklistItems.querySelectorAll(".checklist-editor__row")].map((row) => {
      const item = {
        text: row.querySelector(".checklist-editor__text")?.value || "",
        completed: row.dataset.completed === "true",
      };
      if (row.dataset.itemId) item.id = row.dataset.itemId;
      return item;
    });
  }

  function collectEditorDraft() {
    const type = state.editorType;
    const draft = {
      type,
      text: elements.taskTitle.value,
      notes: elements.taskNotes.value,
      priority: Number(elements.taskPriority.value),
    };
    if (state.editorTask?.revision) draft.revision = state.editorTask.revision;
    if (type === "habit") {
      draft.up = elements.habitUp.checked;
      draft.down = elements.habitDown.checked;
    }
    if (type === "daily") {
      const scheduleEditable = !state.editorTask || state.editorTask.scheduleEditable === true;
      draft.scheduleEditable = scheduleEditable;
      if (scheduleEditable) {
        draft.repeatDays = [...document.querySelectorAll('[name="repeat-day"]:checked')]
          .map((input) => input.value);
        draft.startDate = elements.dailyStartDate.value;
      }
      draft.checklist = collectChecklistDraft();
    }
    if (type === "todo") {
      draft.date = elements.todoDueDate.value;
      draft.checklist = collectChecklistDraft();
    }
    return draft;
  }

  function canonicalEditorDraft() {
    if (!state.editorType) return "";
    const draft = collectEditorDraft();
    delete draft.revision;
    if (Array.isArray(draft.checklist)) {
      draft.checklist = draft.checklist.map((item) => ({
        ...(item.id ? { id: item.id } : {}),
        text: item.text,
      }));
    }
    return JSON.stringify(draft);
  }

  function updateEditorDirty() {
    state.editorDirty = Boolean(state.editorInitialDraft) && canonicalEditorDraft() !== state.editorInitialDraft;
    elements.editorDialog.dataset.dirty = String(state.editorDirty);
  }

  function setEditorSubmitting(submitting) {
    state.editorSubmitting = submitting;
    elements.editorDialog.setAttribute("aria-busy", String(submitting));
    elements.editorForm.querySelectorAll("button, input, textarea, select").forEach((control) => {
      const mutatesTask = control.matches("input, textarea, select")
        || control === elements.checklistAdd
        || control.classList.contains("checklist-editor__remove");
      control.disabled = submitting || (state.editorReadOnly && mutatesTask);
    });
    elements.editorSubmit.disabled = submitting || state.editorReadOnly;
    elements.editorBack.disabled = submitting;
    elements.editorDelete.disabled = submitting;
  }

  function closeTaskEditor({ discard = false } = {}) {
    if (state.editorSubmitting) return;
    if (state.editorDirty && !discard) {
      state.discardConfirmed = false;
      showDialog(elements.discardDialog);
      window.requestAnimationFrame(() => elements.discardCancel.focus());
      return;
    }
    state.editorDirty = false;
    state.editorInitialDraft = "";
    closeDialog(elements.editorDialog);
  }

  function requestCloseTaskEditor() {
    closeTaskEditor();
  }

  async function submitTaskEditor(event) {
    event.preventDefault();
    if (state.editorSubmitting || !taskController) return;
    if (!elements.editorForm.checkValidity()) {
      elements.editorForm.reportValidity();
      return;
    }
    clearEditorError();
    const draft = collectEditorDraft();
    const validation = window.HabiticaTaskUI.validateTaskDraft(draft);
    if (!validation.ok) {
      const first = Object.entries(validation.errors)[0];
      setEditorError(first?.[1], first?.[0]);
      return;
    }
    setEditorSubmitting(true);
    let outcomeUnknown = false;
    try {
      if (state.editorMode === "edit") {
        await taskController.updateTask(state.editorTask.id, draft);
      } else {
        await taskController.createTask(draft);
      }
      state.editorDirty = false;
      state.editorInitialDraft = "";
      closeDialog(elements.editorDialog);
    } catch (error) {
      outcomeUnknown = error?.outcomeUnknown === true;
      const first = error?.errors ? Object.entries(error.errors)[0] : null;
      const message = outcomeUnknown
        ? "Habitica may have saved this task. Close the editor and check the refreshed list before trying again."
        : first?.[1] || error?.message || "This task could not be saved.";
      setEditorError(message, first?.[0]);
    } finally {
      if (dialogIsOpen(elements.editorDialog)) {
        setEditorSubmitting(false);
        if (outcomeUnknown) elements.editorSubmit.disabled = true;
      }
    }
  }

  function openDeleteDialog(task) {
    if (!task || typeof task.id !== "string") return;
    state.deleteTask = task;
    state.deleteTrigger = document.activeElement;
    state.deleteRestoreFocus = true;
    state.deletePostFocusType = null;
    state.deleteSubmitting = false;
    elements.deleteTitle.textContent = task.text || "Untitled task";
    elements.deleteError.hidden = true;
    elements.deleteError.textContent = "";
    elements.deleteCancel.disabled = false;
    elements.deleteConfirm.disabled = false;
    elements.deleteDialog.setAttribute("aria-busy", "false");
    showDialog(elements.deleteDialog);
    haptic("notification", "warning");
    window.requestAnimationFrame(() => elements.deleteCancel.focus());
  }

  function setDeleteSubmitting(submitting) {
    state.deleteSubmitting = submitting;
    elements.deleteDialog.setAttribute("aria-busy", String(submitting));
    elements.deleteCancel.disabled = submitting;
    elements.deleteConfirm.disabled = submitting;
  }

  async function submitDelete(event) {
    event.preventDefault();
    if (state.deleteSubmitting || !state.deleteTask || !taskController) return;
    setDeleteSubmitting(true);
    elements.deleteError.hidden = true;
    elements.deleteError.textContent = "";
    const deletedType = state.deleteTask.type;
    const closesEditor = state.editorTask?.id === state.deleteTask.id;
    try {
      await taskController.deleteTask(deletedType, state.deleteTask.id);
      state.deleteRestoreFocus = false;
      state.deletePostFocusType = deletedType;
      closeDialog(elements.deleteDialog);
      if (closesEditor) {
        state.editorDirty = false;
        state.editorInitialDraft = "";
        closeDialog(elements.editorDialog);
      }
    } catch (error) {
      const unresolved = error?.outcomeUnknown === true && error?.reconciled !== true;
      elements.deleteError.textContent = unresolved
        ? "Habitica may have deleted this task. Close this dialog and refresh before trying again."
        : error?.message || "This task could not be deleted.";
      elements.deleteError.hidden = false;
      setDeleteSubmitting(false);
      if (unresolved) elements.deleteConfirm.disabled = true;
    }
  }

  function setQuickAddSubmitting(submitting) {
    state.quickAddSubmitting = submitting;
    elements.quickAddDialog.setAttribute("aria-busy", String(submitting));
    elements.quickAddForm.querySelectorAll("button, input").forEach((control) => {
      control.disabled = submitting;
    });
  }

  function openQuickAdd(type) {
    if (!window.HabiticaTaskUI.TYPES.includes(type) || dialogIsOpen(elements.quickAddDialog)) return;
    state.quickAddType = type;
    state.quickAddTrigger = document.activeElement;
    elements.quickAddForm.reset();
    elements.quickAddError.hidden = true;
    elements.quickAddError.textContent = "";
    elements.quickAddTypeLabel.textContent = `New ${taskTypeLabel(type)}`;
    elements.quickAddSubmit.textContent = "Scribe Task";
    elements.quickAddForm.querySelectorAll('[name="quick-add-type"]').forEach((control) => {
      control.checked = control.value === type;
    });
    setQuickAddSubmitting(false);
    showDialog(elements.quickAddDialog);
    window.requestAnimationFrame(() => elements.quickAddInput.focus());
  }

  function closeQuickAdd() {
    if (!state.quickAddSubmitting) closeDialog(elements.quickAddDialog);
  }

  async function submitQuickAdd(event) {
    event.preventDefault();
    if (state.quickAddSubmitting || !taskController || !state.quickAddType) return;
    if (!elements.quickAddForm.checkValidity()) {
      elements.quickAddForm.reportValidity();
      return;
    }
    const selectedType = elements.quickAddForm.querySelector('[name="quick-add-type"]:checked')?.value;
    const selectedPriority = elements.quickAddForm.querySelector('[name="quick-add-priority"]:checked')?.value;
    if (["habit", "daily", "todo"].includes(selectedType)) state.quickAddType = selectedType;
    const draft = window.HabiticaTaskUI.quickAddDefaults(state.quickAddType, elements.quickAddInput.value);
    if (["0.1", "1", "1.5", "2"].includes(selectedPriority)) draft.priority = selectedPriority;
    const validation = window.HabiticaTaskUI.validateTaskDraft(draft);
    if (!validation.ok) {
      elements.quickAddError.textContent = Object.values(validation.errors)[0] || "Enter a task title.";
      elements.quickAddError.hidden = false;
      return;
    }
    elements.quickAddError.hidden = true;
    setQuickAddSubmitting(true);
    try {
      await taskController.createTask(draft);
      elements.quickAddInput.value = "";
      closeDialog(elements.quickAddDialog);
    } catch (error) {
      elements.quickAddError.textContent = error?.outcomeUnknown
        ? "Habitica may have added this task. Check the list before creating it again."
        : error?.message || "This task could not be added.";
      elements.quickAddError.hidden = false;
      setQuickAddSubmitting(false);
      if (error?.outcomeUnknown) elements.quickAddSubmit.disabled = true;
    }
  }

  function openQuickAddMoreOptions() {
    if (state.quickAddSubmitting || !state.quickAddType) return;
    const type = state.quickAddType;
    const title = elements.quickAddInput.value;
    if (history.state?.miniappOverlay === elements.quickAddDialog.id) {
      history.replaceState(null, "", `#${state.activeTab}`);
    }
    closeDialog(elements.quickAddDialog, { unwindHistory: false });
    openTaskEditor(type, null, title);
  }

  function syncOverlayState() {
    const anyOpen = gameplayDialogsOpen();
    document.body.classList.toggle("has-dialog", anyOpen);
    const shell = document.querySelector(".safe-area-shell");
    if (shell) shell.inert = anyOpen;
    updateTelegramBackButton();
    if (!anyOpen && state.dayGatePending) {
      window.queueMicrotask(() => { void continuePendingDayGate(); });
    }
  }

  function installTaskDialogs() {
    elements.editorForm.addEventListener("submit", submitTaskEditor);
    elements.editorClose.addEventListener("click", requestCloseTaskEditor);
    elements.editorBack.addEventListener("click", requestCloseTaskEditor);
    elements.editorCancel.addEventListener("click", requestCloseTaskEditor);
    elements.editorDelete.addEventListener("click", () => {
      if (state.editorTask) openDeleteDialog(state.editorTask);
    });
    elements.editorForm.addEventListener("input", updateEditorDirty);
    elements.editorForm.addEventListener("change", updateEditorDirty);
    elements.checklistAdd.addEventListener("click", () => {
      createChecklistEditorRow().focus();
      updateEditorDirty();
    });
    elements.editorDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (!state.editorSubmitting) requestCloseTaskEditor();
    });
    elements.editorDialog.addEventListener("close", () => {
      syncOverlayState();
      const trigger = state.editorTrigger;
      const focusKey = state.editorFocusKey;
      state.editorTask = null;
      state.editorType = null;
      state.editorTrigger = null;
      state.editorFocusKey = null;
      state.editorInitialDraft = "";
      state.editorDirty = false;
      state.editorReadOnly = false;
      const replacement = focusKey
        ? [...document.querySelectorAll("[data-focus-key]")].find((element) => element.dataset.focusKey === focusKey)
        : null;
      if (trigger?.isConnected) trigger.focus?.();
      else replacement?.focus?.();
    });

    elements.deleteForm.addEventListener("submit", submitDelete);
    elements.deleteCancel.addEventListener("click", () => {
      if (!state.deleteSubmitting) closeDialog(elements.deleteDialog);
    });
    elements.deleteDialog.addEventListener("cancel", (event) => {
      if (state.deleteSubmitting) event.preventDefault();
    });
    elements.deleteDialog.addEventListener("close", () => {
      syncOverlayState();
      if (
        dialogIsOpen(elements.editorDialog)
        && history.state?.miniappOverlay !== elements.editorDialog.id
      ) {
        history.pushState(
          { miniappOverlay: elements.editorDialog.id, tab: state.activeTab },
          "",
          window.location.href,
        );
      }
      const trigger = state.deleteTrigger;
      const restore = state.deleteRestoreFocus;
      const postFocusType = state.deletePostFocusType;
      state.deleteTask = null;
      state.deleteTrigger = null;
      state.deletePostFocusType = null;
      if (postFocusType) elements.taskFab?.focus();
      else if (restore && trigger?.isConnected) trigger.focus?.();
    });

    elements.quickAddForm.addEventListener("submit", submitQuickAdd);
    elements.quickAddClose.addEventListener("click", closeQuickAdd);
    elements.quickAddCancel.addEventListener("click", closeQuickAdd);
    elements.quickAddMore.addEventListener("click", openQuickAddMoreOptions);
    elements.quickAddDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeQuickAdd();
    });
    elements.quickAddDialog.addEventListener("close", () => {
      syncOverlayState();
      state.quickAddSubmitting = false;
      state.quickAddType = null;
      const trigger = state.quickAddTrigger;
      state.quickAddTrigger = null;
      if (!dialogIsOpen(elements.editorDialog) && trigger?.isConnected) trigger.focus?.();
    });
    elements.potionDialog.addEventListener("close", syncOverlayState);

    elements.discardCancel.addEventListener("click", () => {
      closeDialog(elements.discardDialog);
      if (dialogIsOpen(elements.editorDialog) && history.state?.miniappOverlay !== elements.editorDialog.id) {
        history.pushState({ miniappOverlay: elements.editorDialog.id, tab: state.activeTab }, "", window.location.href);
      }
      elements.editorBack.focus();
    });
    elements.discardForm.addEventListener("submit", (event) => {
      event.preventDefault();
      state.discardConfirmed = true;
      closeDialog(elements.discardDialog);
      closeTaskEditor({ discard: true });
    });
    elements.discardDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      elements.discardCancel.click();
    });
    elements.discardDialog.addEventListener("close", () => {
      syncOverlayState();
      if (
        dialogIsOpen(elements.editorDialog)
        && history.state?.miniappOverlay !== elements.editorDialog.id
      ) {
        history.pushState(
          { miniappOverlay: elements.editorDialog.id, tab: state.activeTab },
          "",
          window.location.href,
        );
      }
    });

    document.addEventListener("miniapp:task-create", (event) => {
      openTaskEditor(event.detail?.type);
    });
    document.addEventListener("miniapp:quick-add", (event) => {
      openQuickAdd(event.detail?.type);
    });
    document.addEventListener("miniapp:task-edit", (event) => {
      openTaskEditor(event.detail?.task?.type, event.detail?.task);
    });
    document.addEventListener("miniapp:task-delete-request", (event) => {
      openDeleteDialog(event.detail?.task);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !state.editorSubmitting && !state.deleteSubmitting) closeTopOverlay();
    });
    window.addEventListener("popstate", () => {
      if (state.ignoreNextPopstate) {
        state.ignoreNextPopstate = false;
        return;
      }
      closeTopOverlay();
    });
    try {
      telegram?.onEvent?.("backButtonClicked", closeTopOverlay);
    } catch (_error) {
      // Ordinary browsers and older Telegram clients do not expose BackButton.
    }
  }

  function initializeTaskController() {
    if (!window.HabiticaTaskUI?.createTaskController) return;
    taskController = window.HabiticaTaskUI.createTaskController({
      root: document,
      requestJson,
      announce,
      haptic,
      onMutationConfirmed: (detail) => {
        if (detail.kind === "checklist") return;
        applyConfirmedProfilePatch(detail);
        // Task pages already hold the confirmed mutation. Defer the expensive
        // four-list Home summary until Home is opened or explicitly refreshed.
        markQuestLogDirty();
      },
      onDayRefreshRequired: requireDayReview,
      apiBase: API_BASE,
      limits: { title: 500, notes: 10000, checklistItems: 100, checklistText: 500 },
    });
  }

  function updateRefreshButton() {
    const labels = { home: "Refresh profile and avatar", habits: "Refresh Habits", dailies: "Refresh Dailies", todos: "Refresh Todos" };
    elements.refreshButton.setAttribute("aria-label", labels[state.activeTab] || "Refresh");
  }

  async function refreshCurrentTab() {
    if (state.activeTab === "home") {
      return refreshAll({ forceAvatar: true, userInitiated: true });
    }
    if (state.taskRefreshing || !taskController) return false;
    state.taskRefreshing = true;
    elements.refreshButton.disabled = true;
    elements.refreshButton.classList.add("is-spinning");
    haptic("impact", "light");
    const ok = await taskController.refresh(state.activeTab);
    state.taskRefreshing = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.classList.remove("is-spinning");
    haptic("notification", ok ? "success" : "error");
    return ok;
  }

  function activateTab(name, { focus = false, initial = false, bypassGate = false } = {}) {
    const target = document.querySelector(`[data-tab="${name}"]`);
    if (!target) return;
    if (state.startupState !== "ready" && !bypassGate) {
      state.requestedTab = name;
      return;
    }
    const previous = state.activeTab;
    if (!initial && previous !== name) state.scrollPositions[previous] = window.scrollY;
    state.activeTab = name;
    document.body.dataset.activeView = name;
    const brandLabel = document.querySelector(".brand > span:last-child");
    if (brandLabel) {
      brandLabel.textContent = ({ home: "Habitica", habits: "Habits", dailies: "Dailies", todos: "To-Do’s" })[name] || "Habitica";
    }
    document.querySelectorAll("[data-tab]").forEach((tab) => {
      const active = tab === target;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-view]").forEach((view) => {
      const active = view.dataset.view === name;
      view.hidden = !active;
      view.classList.toggle("is-active", active);
    });
    if (focus) target.focus();
    history.replaceState(null, "", `#${name}`);
    updateRefreshButton();
    elements.miniProfile.hidden = name === "home";
    elements.taskFab.hidden = name === "home" || state.startupState !== "ready";
    if (name === "home") ensureHomeLoaded();
    else {
      const type = { habits: "habit", dailies: "daily", todos: "todo" }[name];
      elements.taskFab.dataset.taskType = type;
      elements.taskFab.setAttribute("aria-label", `Quick add ${taskTypeLabel(type)}`);
      void taskController?.activate(name, { refresh: !initial && previous !== name });
      void ensureTaskProfileLoaded();
    }
    if (!initial && previous !== name) {
      window.requestAnimationFrame(() => window.scrollTo({ top: state.scrollPositions[name] || 0, left: 0 }));
      haptic("selection");
    }
  }

  function installTabs() {
    const tabs = [...document.querySelectorAll("[data-tab]")];
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
      tab.addEventListener("keydown", (event) => {
        let nextIndex = null;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = tabs.length - 1;
        if (nextIndex === null) return;
        event.preventDefault();
        activateTab(tabs[nextIndex].dataset.tab, { focus: true });
      });
    });
    document.querySelectorAll("[data-summary-tab]").forEach((control) => {
      control.addEventListener("click", () => activateTab(control.dataset.summaryTab));
    });
    const requestedTab = window.location.hash.slice(1);
    const initialTab = ["home", "habits", "dailies", "todos"].includes(requestedTab) ? requestedTab : "home";
    state.requestedTab = initialTab;
  }

  function installThemeControls() {
    elements.themeButton.addEventListener("click", () => {
      setThemeMenuOpen(elements.themeMenu.hidden);
    });
    document.querySelectorAll("[data-theme-choice]").forEach((option) => {
      option.addEventListener("click", () => {
        const choice = option.dataset.themeChoice;
        if (choice !== state.themeMode) {
          applyTheme(choice, { persist: true, mirrorCloud: true });
          haptic("selection");
        }
        setThemeMenuOpen(false);
        elements.themeButton.focus();
      });
    });
    document.querySelectorAll("[data-display-scale]").forEach((option) => {
      option.addEventListener("click", () => {
        const choice = option.dataset.displayScale;
        if (choice !== state.displayScale) {
          applyDisplayScale(choice, { persist: true, mirrorCloud: true });
          haptic("selection");
        }
      });
    });
    const settingsOptions = [
      ...document.querySelectorAll("[data-theme-choice], [data-display-scale]"),
    ];
    elements.themeMenu.addEventListener("keydown", (event) => {
      const index = settingsOptions.indexOf(document.activeElement);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % settingsOptions.length;
      if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index - 1 + settingsOptions.length) % settingsOptions.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = settingsOptions.length - 1;
      if (next === null) return;
      event.preventDefault();
      settingsOptions[next].focus();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!elements.themeMenu.hidden && !event.target.closest(".theme-control")) setThemeMenuOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !elements.themeMenu.hidden) {
        setThemeMenuOpen(false);
        elements.themeButton.focus();
      }
    });
    telegram?.onEvent?.("themeChanged", () => {
      if (state.themeMode === "auto") applyTheme("auto");
    });
    systemTheme?.addEventListener?.("change", () => {
      if (state.themeMode === "auto" && !isTelegramLaunch) applyTheme("auto");
    });
    updateThemeControls();
    updateDisplayScaleControls();
    loadCloudTheme();
    loadCloudDisplayScale();
  }

  function initialize() {
    applyTheme(state.themeMode);
    applyDisplayScale(state.displayScale);
    installThemeControls();
    initializeProfileCoordinator();
    initializeTaskController();
    installTaskDialogs();
    elements.quickAddForm.querySelectorAll('[name="quick-add-type"]').forEach((control) => {
      control.addEventListener("change", () => {
        if (!control.checked || !["habit", "daily", "todo"].includes(control.value)) return;
        state.quickAddType = control.value;
        elements.quickAddTypeLabel.textContent = `New ${taskTypeLabel(control.value)}`;
      });
    });
    installGameplayControls();
    installTabs();
    document.querySelector(".brand")?.addEventListener("click", (event) => {
      event.preventDefault();
      activateTab("home");
    });
    elements.refreshButton.addEventListener("click", refreshCurrentTab);
    elements.noticeRetry.addEventListener("click", () => refreshAll({ forceAvatar: true, userInitiated: true }));
    elements.miniProfileRetry.addEventListener("click", () => {
      elements.miniProfileRetry.hidden = true;
      void loadProfile().then(async (profileOk) => {
        const avatarOk = !profileOk || state.avatarUrl ? true : await loadAvatar(false);
        if (!profileOk || !avatarOk) elements.miniProfileRetry.hidden = false;
      });
    });
    window.addEventListener("online", () => {
      if (state.startupState !== "ready") return;
      if (state.activeTab === "home" && !state.profileLoaded) refreshAll();
      else if (state.activeTab !== "home") void taskController?.refresh(state.activeTab);
    });
    window.addEventListener("pagehide", (event) => {
      if (!event.persisted) profileCoordinator?.destroy();
      if (!event.persisted && state.avatarUrl) {
        URL.revokeObjectURL(state.avatarUrl);
        state.avatarUrl = null;
        elements.avatarImage.removeAttribute("src");
        elements.miniAvatarImage?.removeAttribute("src");
      }
    });

    try {
      telegram?.ready?.();
      telegram?.expand?.();
    } catch (_error) {
      // Ordinary browsers and older Telegram clients remain supported.
    }

    void checkDayStatus();

  }

  initialize();
})();
