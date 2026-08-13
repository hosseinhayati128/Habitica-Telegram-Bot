(() => {
  "use strict";

  const THEME_KEY = "hh_theme_mode";
  const THEME_MODES = new Set(["auto", "light", "dark"]);
  const API_BASE = "/miniapp/api";
  const telegram = window.Telegram?.WebApp ?? null;
  const isTelegramLaunch = Boolean(telegram?.initData) && telegram?.platform !== "unknown";
  const systemTheme = window.matchMedia?.("(prefers-color-scheme: dark)") ?? null;

  const state = {
    themeMode: THEME_MODES.has(document.documentElement.dataset.themeMode)
      ? document.documentElement.dataset.themeMode
      : "auto",
    themeGeneration: 0,
    avatarUrl: null,
    profileLoaded: false,
    refreshing: false,
  };

  const elements = {
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
    refreshButton: document.getElementById("refresh-button"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("notice-title"),
    noticeMessage: document.getElementById("notice-message"),
    noticeRetry: document.getElementById("notice-retry"),
    themeButton: document.getElementById("theme-button"),
    themeMenu: document.getElementById("theme-menu"),
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
    const pageColor = dark ? "#15111c" : "#f5f3f8";
    const navColor = dark ? "#211a2e" : "#ffffff";
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
    const names = { auto: "Auto", light: "Light", dark: "Dark" };
    elements.themeButton.setAttribute("aria-label", `Appearance: ${names[state.themeMode]}`);
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
      effective === "dark" ? "#15111c" : "#f5f3f8",
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

  function setThemeMenuOpen(open) {
    elements.themeMenu.hidden = !open;
    elements.themeButton.setAttribute("aria-expanded", String(open));
    if (open) {
      elements.themeMenu.querySelector('[aria-checked="true"]')?.focus();
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
    return error;
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

  function renderProfile(payload) {
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
  }

  function renderAvatar(blob) {
    if (state.avatarUrl) URL.revokeObjectURL(state.avatarUrl);
    state.avatarUrl = URL.createObjectURL(blob);
    elements.avatarImage.src = state.avatarUrl;
    elements.avatarImage.alt = `${elements.displayName.textContent || "Habitica"} avatar`;
    elements.avatarImage.hidden = false;
    elements.avatarPlaceholder.hidden = true;
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

  async function loadProfile() {
    try {
      const payload = await fetchProfile();
      renderProfile(payload);
      hideNotice();
      return true;
    } catch (error) {
      showNotice(error);
      return false;
    }
  }

  async function loadAvatar(forceRefresh) {
    try {
      renderAvatar(await fetchAvatar(forceRefresh));
      return true;
    } catch (_error) {
      renderAvatarError();
      return false;
    }
  }

  async function refreshAll({ forceAvatar = false, userInitiated = false } = {}) {
    if (state.refreshing) return;
    state.refreshing = true;
    hideNotice();
    elements.refreshButton.disabled = true;
    elements.refreshButton.classList.add("is-spinning");
    elements.profileCard.classList.add("is-refreshing");
    if (userInitiated) haptic("impact", "light");

    // PythonAnywhere free web apps may have one worker.  Profile-first ordering
    // guarantees an expensive first avatar render cannot sit ahead of the text.
    const profileOk = await loadProfile();
    const avatarOk = await loadAvatar(forceAvatar);

    state.refreshing = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.classList.remove("is-spinning");
    elements.profileCard.classList.remove("is-refreshing");
    if (userInitiated) haptic("notification", profileOk && avatarOk ? "success" : "error");
  }

  function activateTab(name, { focus = false } = {}) {
    const target = document.querySelector(`[data-tab="${name}"]`);
    if (!target || target.classList.contains("is-active")) return;
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
    haptic("selection");
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
    const initialTab = window.location.hash.slice(1);
    if (["habits", "dailies", "todos"].includes(initialTab)) activateTab(initialTab);
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
    const themeOptions = [...document.querySelectorAll("[data-theme-choice]")];
    elements.themeMenu.addEventListener("keydown", (event) => {
      const index = themeOptions.indexOf(document.activeElement);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowDown") next = (index + 1) % themeOptions.length;
      if (event.key === "ArrowUp") next = (index - 1 + themeOptions.length) % themeOptions.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = themeOptions.length - 1;
      if (next === null) return;
      event.preventDefault();
      themeOptions[next].focus();
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
    loadCloudTheme();
  }

  function initialize() {
    applyTheme(state.themeMode);
    installThemeControls();
    installTabs();
    document.querySelector(".brand")?.addEventListener("click", (event) => {
      event.preventDefault();
      activateTab("home");
    });
    elements.refreshButton.addEventListener("click", () => refreshAll({ forceAvatar: true, userInitiated: true }));
    elements.noticeRetry.addEventListener("click", () => refreshAll({ forceAvatar: true, userInitiated: true }));
    window.addEventListener("online", () => {
      if (!state.profileLoaded) refreshAll();
    });
    window.addEventListener("pagehide", (event) => {
      if (!event.persisted && state.avatarUrl) {
        URL.revokeObjectURL(state.avatarUrl);
        state.avatarUrl = null;
        elements.avatarImage.removeAttribute("src");
      }
    });

    try {
      telegram?.ready?.();
      telegram?.expand?.();
    } catch (_error) {
      // Ordinary browsers and older Telegram clients remain supported.
    }

    refreshAll();
  }

  initialize();
})();
