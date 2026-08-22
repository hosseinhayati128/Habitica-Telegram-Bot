"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { URL } = require("node:url");
const zlib = require("node:zlib");

const puppeteer = require("puppeteer");

const REPOSITORY_ROOT = path.resolve(__dirname, "..");
const TEMPLATE_PATH = path.join(REPOSITORY_ROOT, "templates", "miniapp", "index.html");
const STATIC_ROOT = path.join(REPOSITORY_ROOT, "static", "miniapp");
const OUTPUT_ROOT = path.join(REPOSITORY_ROOT, ".runtime", "visual-redesign");
const VIEWPORT_HEIGHT = 844;
const VALID_SCENARIOS = new Set([
  "normal",
  "loading",
  "error",
  "day-loading",
  "day-one",
  "day-review",
  "day-empty",
  "day-multiple",
  "day-batch",
  "day-partial",
  "day-unknown",
  "potion-unknown",
  "score-day-gate",
]);
const VALID_THEMES = new Set(["light", "dark"]);

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])));
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function syntheticAvatarPng() {
  const width = 16;
  const height = 16;
  const stride = 1 + width * 4;
  const pixels = Buffer.alloc(stride * height);
  const paint = (x, y, color) => {
    const offset = y * stride + 1 + x * 4;
    color.forEach((channel, index) => { pixels[offset + index] = channel; });
  };
  const fill = (x1, y1, x2, y2, color) => {
    for (let y = y1; y <= y2; y += 1) {
      for (let x = x1; x <= x2; x += 1) paint(x, y, color);
    }
  };
  const outline = [39, 27, 61, 255];
  const blue = [80, 181, 233, 255];
  const teal = [59, 202, 215, 255];
  const purple = [146, 92, 243, 255];
  const gold = [255, 190, 93, 255];
  const white = [238, 235, 247, 255];

  fill(1, 1, 14, 14, outline);
  fill(2, 12, 13, 13, teal);
  fill(5, 7, 11, 12, purple);
  fill(4, 8, 6, 11, blue);
  fill(7, 4, 10, 7, gold);
  fill(6, 2, 11, 4, blue);
  fill(8, 5, 10, 6, white);
  fill(12, 4, 12, 11, gold);
  paint(13, 3, gold);
  paint(5, 3, teal);
  paint(11, 8, white);

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", zlib.deflateSync(pixels)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

const SYNTHETIC_AVATAR_PNG = syntheticAvatarPng();

const sessions = new Map();
let generatedTaskId = 0;

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function fixtureProfile() {
  return {
    profile: {
      displayName: "h128",
      username: "h128",
      level: 112,
      class: "warrior",
      classLabel: "Warrior",
      hasClass: true,
    },
    stats: {
      hp: 40,
      maxHp: 50,
      exp: 4072,
      maxExp: 4400,
      mp: 244,
      maxMp: 330,
      gold: 3847,
    },
  };
}

function baseTask(id, type, text, overrides = {}) {
  return {
    id,
    type,
    text,
    notes: "",
    priority: 1,
    value: 0,
    up: type === "habit",
    down: type === "habit",
    counterUp: 0,
    counterDown: 0,
    counterFrequency: type === "habit" ? "daily" : null,
    completed: false,
    dueToday: type === "daily",
    streak: 0,
    date: null,
    startDate: type === "daily" ? "2026-08-01" : null,
    repeatDays: type === "daily" ? ["su", "m", "t", "w", "th", "f", "s"] : [],
    scheduleEditable: type === "daily",
    checklist: [],
    revision: "a".repeat(32),
    canEdit: true,
    canDelete: true,
    ...overrides,
  };
}

function fixtureTasks() {
  return [
    baseTask("habit-body", "habit", "Body", { value: 3, counterUp: 2, counterDown: 1 }),
    baseTask("habit-exercise", "habit", "Exercise", {
      notes: "For every 15 min · No exercise in a day −5",
      value: 7,
    }),
    baseTask("habit-apply", "habit", "Apply", {
      notes: "For every 15 min",
      value: 12,
      down: false,
      counterUp: 4,
      counterFrequency: "weekly",
    }),
    baseTask("habit-look", "habit", "Look", { value: 6, down: false }),
    baseTask("habit-pose", "habit", "Pose", { value: 2 }),
    baseTask("habit-face", "habit", "Face", { value: -21 }),
    baseTask("habit-cracking", "habit", "Cracking", { value: -12 }),
    baseTask("habit-skin", "habit", "Skin", {
      notes: "Get five negatives each day with no care at all",
      value: -3,
    }),
    baseTask("habit-release", "habit", "Release tension", {
      value: -6,
      up: false,
      down: true,
      counterDown: 1,
      counterFrequency: "monthly",
    }),
    baseTask("habit-plants", "habit", "Plants", { value: 0, up: true, down: false }),
    baseTask("daily-morning", "daily", "Morning routine", { streak: 19 }),
    baseTask("daily-english", "daily", "Practice English", {
      notes: "First 5 min, then every 15 min",
      streak: 8,
    }),
    baseTask("daily-medicine", "daily", "Take medicine", {
      checklist: [
        { id: "daily-check-1", text: "With breakfast", completed: true },
        { id: "daily-check-2", text: "Drink water", completed: false },
      ],
    }),
    baseTask("daily-drawing", "daily", "Drawing practice", { streak: 3 }),
    baseTask("daily-reading", "daily", "Read ten pages", { notes: "Any book counts" }),
    baseTask("daily-review", "daily", "Review the day"),
    baseTask(
      "daily-long",
      "daily",
      "A deliberately long Daily title that must stay compact on a narrow phone",
      {
        notes: "This longer note also wraps, while useful checklist and streak metadata remain visible.",
        streak: 123,
        checklist: [
          { id: "daily-long-1", text: "First step", completed: true },
          { id: "daily-long-2", text: "Second step", completed: false },
        ],
      },
    ),
    baseTask("daily-optional", "daily", "Weekly planning", { dueToday: false }),
    baseTask("daily-complete", "daily", "Make the bed", { completed: true }),
    baseTask("todo-appointment", "todo", "Book dentist appointment", { date: "2026-08-20" }),
    baseTask("todo-report", "todo", "Finish project report", {
      notes: "Send the final PDF after one careful review",
      checklist: [
        { id: "todo-check-1", text: "Proofread", completed: true },
        { id: "todo-check-2", text: "Export PDF", completed: false },
      ],
    }),
    baseTask("todo-groceries", "todo", "Buy groceries"),
    baseTask("todo-call", "todo", "Call family"),
    baseTask("todo-repair", "todo", "Repair the desk lamp"),
    baseTask("todo-backup", "todo", "Back up documents"),
    baseTask("todo-tickets", "todo", "Reserve train tickets", { date: "2026-08-24" }),
    baseTask("todo-complete", "todo", "Renew library card", { completed: true }),
  ];
}

function newSession(scenario) {
  return {
    scenario,
    profile: fixtureProfile(),
    tasks: fixtureTasks(),
    meRequests: 0,
    avatarRequests: 0,
    scoreRequests: 0,
    checklistRequests: 0,
    taskListRequests: 0,
    dayStatusRequests: 0,
    dayRefreshRequests: 0,
    potionGets: 0,
    potionPosts: 0,
    potionIntent: null,
    potionBodies: [],
    dayRefreshRequired: scenario.startsWith("day-") && scenario !== "day-loading",
  };
}

function parseCookies(request) {
  return Object.fromEntries(
    String(request.headers.cookie || "")
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const separator = part.indexOf("=");
        return separator < 0
          ? [part, ""]
          : [part.slice(0, separator), decodeURIComponent(part.slice(separator + 1))];
      }),
  );
}

function sessionFor(request) {
  const id = parseCookies(request)["visual-session"];
  return typeof id === "string" ? sessions.get(id) : null;
}

function sendJson(response, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
  });
  response.end(body);
}

function sendBytes(response, status, contentType, body, extraHeaders = {}) {
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": contentType,
    "Content-Length": body.length,
    ...extraHeaders,
  });
  response.end(body);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 128 * 1024) throw new Error("Fixture request body is too large.");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

function telegramBootstrap(theme) {
  return `<script>
    (() => {
      localStorage.setItem("hh_theme_mode", ${JSON.stringify(theme)});
      const noop = () => {};
      window.Telegram = { WebApp: {
        initData: "visual-fixture-only", platform: "web", version: "9.0", colorScheme: ${JSON.stringify(theme)},
        isVersionAtLeast: () => true, ready: noop, expand: noop, onEvent: noop,
        setBackgroundColor: noop, setHeaderColor: noop, setBottomBarColor: noop,
        BackButton: { show: noop, hide: noop },
        HapticFeedback: { selectionChanged: noop, impactOccurred: noop, notificationOccurred: noop },
        CloudStorage: { getItem: (_key, callback) => callback(null, null), setItem: (_key, _value, callback) => callback(null, true) }
      } };
    })();
  </script>`;
}

async function renderTemplate(theme) {
  let html = await fs.promises.readFile(TEMPLATE_PATH, "utf8");
  html = html.replace(
    /<script src="https:\/\/telegram\.org\/js\/telegram-web-app\.js\?\d+"><\/script>/,
    telegramBootstrap(theme),
  );
  html = html.replace(
    /\{\{\s*url_for\('static',\s*filename='miniapp\/([^']+)'(?:,\s*v='[^']+')?\)\s*\}\}/g,
    "/static/miniapp/$1",
  );
  return Buffer.from(html);
}

function taskById(session, id) {
  return session.tasks.find((task) => task.id === id) || null;
}

function reviewDailies(session) {
  if (session.scenario === "day-empty") return [];
  if (session.scenario === "day-batch") {
    return Array.from({ length: 12 }, (_value, index) => ({
      id: `daily-batch-${index + 1}`,
      text: `Batch Daily ${index + 1}`,
      notes: "",
      priority: 1,
      checklist: [],
      completed: false,
    }));
  }
  const ids = session.scenario === "day-one"
    ? ["daily-english"]
    : ["daily-english", "daily-medicine", "daily-long"];
  return ids.map((id) => taskById(session, id)).filter(Boolean).map((task) => ({
    id: task.id,
    text: task.text,
    notes: task.notes,
    priority: task.priority,
    checklist: copy(task.checklist),
    completed: false,
  }));
}

async function handleApi(request, response, url, session) {
  if (!session) {
    sendJson(response, 401, { ok: false, error: { code: "invalid_fixture_session", message: "Missing fixture session." } });
    return;
  }

  if (url.pathname === "/miniapp/api/day-status" && request.method === "GET") {
    session.dayStatusRequests += 1;
    if (session.scenario === "day-loading") await delay(1600);
    sendJson(response, 200, {
      ok: true,
      day: session.dayRefreshRequired
        ? {
            refreshRequired: true,
            daysMissed: session.scenario === "day-multiple" ? 4 : 1,
            reviewLabel: session.scenario === "day-multiple" ? "Last active day" : "Yesterday",
            dailies: reviewDailies(session),
          }
        : { refreshRequired: false },
    });
    return;
  }

  if (url.pathname === "/miniapp/api/day-refresh" && request.method === "POST") {
    session.dayRefreshRequests += 1;
    const body = await readJsonBody(request);
    const selected = Array.isArray(body.completedDailyIds) ? body.completedDailyIds : [];
    await delay(650);
    if (session.scenario === "day-unknown") {
      sendJson(response, 504, {
        ok: false,
        status: "refresh_failed",
        error: { code: "cron_failed", message: "The visual fixture could not confirm cron." },
        reconcileRequired: true,
        unresolvedDailyIds: selected,
      });
      return;
    }
    if (session.scenario === "day-batch" && session.dayRefreshRequests === 1 && selected.length > 8) {
      sendJson(response, 429, {
        ok: false,
        status: "batch_incomplete",
        error: { code: "batch_incomplete", message: "A safe visual fixture batch was recorded." },
        scoredDailyIds: selected.slice(0, 8),
        unresolvedDailyIds: selected.slice(8),
        day: { refreshRequired: true },
      });
      return;
    }
    if (session.scenario === "day-partial" && session.dayRefreshRequests === 1 && selected.length > 1) {
      const scoredDailyIds = [selected[0]];
      const unresolvedDailyIds = selected.slice(1);
      sendJson(response, 502, {
        ok: false,
        status: "partial_failure",
        error: { code: "daily_score_failed", message: "A visual fixture Daily failed." },
        scoredDailyIds,
        unresolvedDailyIds,
        day: { refreshRequired: true },
        dailies: reviewDailies(session).filter((daily) => !scoredDailyIds.includes(daily.id)),
      });
      return;
    }
    session.dayRefreshRequired = false;
    session.tasks.forEach((task) => {
      if (task.type === "habit" && task.counterFrequency === "daily") {
        task.counterUp = 0;
        task.counterDown = 0;
      }
      if (task.type === "daily") task.completed = false;
    });
    sendJson(response, 200, {
      ok: true,
      status: "refreshed",
      day: { refreshRequired: false },
      profile: {
        profile: {
          level: session.profile.profile.level,
          class: session.profile.profile.class,
        },
        stats: copy(session.profile.stats),
      },
      invalidate: { profile: true, habits: true, dailies: true, todos: false, avatar: false },
    });
    return;
  }

  if (url.pathname === "/miniapp/api/health-potion" && request.method === "GET") {
    session.potionGets += 1;
    session.potionIntent = `visual-intent-${String(session.potionGets).padStart(32, "0")}`;
    const stats = session.profile.stats;
    sendJson(response, 200, {
      ok: true,
      purchaseIntent: session.potionIntent,
      potion: { name: "Health Potion", price: 25, healing: 15 },
      stats: { hp: stats.hp, maxHp: stats.maxHp, gold: stats.gold },
      healthFull: stats.hp >= stats.maxHp,
      canAfford: stats.gold >= 25,
    });
    return;
  }

  if (url.pathname === "/miniapp/api/health-potion" && request.method === "POST") {
    session.potionPosts += 1;
    const body = await readJsonBody(request);
    session.potionBodies.push(body);
    if (
      !body
      || Object.keys(body).length !== 1
      || body.purchaseIntent !== session.potionIntent
    ) {
      sendJson(response, 400, {
        ok: false,
        error: { code: "invalid_purchase_intent", message: "Invalid visual purchase intent." },
      });
      return;
    }
    if (session.scenario === "potion-unknown") {
      sendJson(response, 504, {
        ok: false,
        error: { code: "purchase_failed", message: "The visual fixture lost the purchase response." },
        reconcileRequired: true,
      });
      return;
    }
    session.profile.stats.hp = Math.min(session.profile.stats.maxHp, session.profile.stats.hp + 15);
    session.profile.stats.gold -= 25;
    sendJson(response, 200, {
      ok: true,
      potion: { name: "Health Potion", price: 25, healing: 15 },
      profile: {
        profile: {
          level: session.profile.profile.level,
          class: session.profile.profile.class,
        },
        stats: copy(session.profile.stats),
      },
      invalidate: { profile: true, avatar: false },
    });
    return;
  }

  if (url.pathname === "/miniapp/api/me" && request.method === "GET") {
    session.meRequests += 1;
    sendJson(response, 200, { ok: true, ...copy(session.profile) });
    return;
  }

  if (url.pathname === "/miniapp/api/avatar" && request.method === "GET") {
    session.avatarRequests += 1;
    sendBytes(response, 200, "image/png", SYNTHETIC_AVATAR_PNG);
    return;
  }

  if (url.pathname === "/miniapp/api/task-summary" && request.method === "GET") {
    const habits = session.tasks.filter((task) => task.type === "habit");
    const dailies = session.tasks.filter((task) => task.type === "daily" && task.dueToday);
    const openTodos = session.tasks.filter((task) => task.type === "todo" && !task.completed);
    const completedTodos = session.tasks.filter((task) => task.type === "todo" && task.completed);
    sendJson(response, 200, {
      ok: true,
      summary: {
        habits: {
          total: habits.length,
          completed: habits.filter((task) => task.counterUp > 0 || task.counterDown > 0).length,
        },
        dailies: { total: dailies.length, completed: dailies.filter((task) => task.completed).length },
        todos: { total: openTodos.length + completedTodos.length, completed: completedTodos.length },
      },
    });
    return;
  }

  if (url.pathname === "/miniapp/api/tasks" && request.method === "GET") {
    session.taskListRequests += 1;
    if (session.scenario === "loading") await delay(1800);
    if (session.scenario === "error") {
      sendJson(response, 503, {
        ok: false,
        error: { code: "habitica_unavailable", message: "The visual fixture is simulating a temporary outage." },
      });
      return;
    }
    const type = url.searchParams.get("type");
    const completed = url.searchParams.get("completed");
    const selected = session.tasks.filter((task) => {
      if (task.type !== type) return false;
      if (type === "todo" && completed !== null) return task.completed === (completed === "true");
      return true;
    });
    sendJson(response, 200, { ok: true, tasks: copy(selected) });
    return;
  }

  if (url.pathname === "/miniapp/api/tasks" && request.method === "POST") {
    const draft = await readJsonBody(request);
    generatedTaskId += 1;
    const created = baseTask(`fixture-created-${generatedTaskId}`, draft.type, draft.text, {
      ...draft,
      up: draft.type === "habit" ? draft.up === true : false,
      down: draft.type === "habit" ? draft.down === true : false,
      dueToday: draft.type === "daily",
      date: draft.date || null,
      startDate: draft.startDate || null,
      checklist: (draft.checklist || []).map((item, index) => ({
        id: `created-check-${index + 1}`,
        text: item.text,
        completed: item.completed === true,
      })),
    });
    session.tasks.push(created);
    sendJson(response, 200, { ok: true, task: copy(created) });
    return;
  }

  const scoreMatch = url.pathname.match(/^\/miniapp\/api\/tasks\/([^/]+)\/score$/);
  if (scoreMatch && request.method === "POST") {
    const task = taskById(session, decodeURIComponent(scoreMatch[1]));
    const body = await readJsonBody(request);
    if (!task) {
      sendJson(response, 404, { ok: false, error: { code: "task_not_found", message: "Task not found." } });
      return;
    }
    session.scoreRequests += 1;
    if (task.type === "habit") {
      task.value += body.direction === "down" ? -1 : 1;
      const counter = body.direction === "down" ? "counterDown" : "counterUp";
      task[counter] = (task[counter] || 0) + 1;
    } else {
      task.completed = body.direction === "up";
    }
    session.profile.stats.exp += 1;
    session.profile.stats.gold += 1;
    sendJson(response, 200, {
      ok: true,
      task: copy(task),
      profilePatch: {
        hp: session.profile.stats.hp,
        exp: session.profile.stats.exp,
        mp: session.profile.stats.mp,
        gold: session.profile.stats.gold,
        level: session.profile.profile.level,
      },
    });
    return;
  }

  const checklistScoreMatch = url.pathname.match(
    /^\/miniapp\/api\/tasks\/([^/]+)\/checklist\/([^/]+)\/score$/,
  );
  if (checklistScoreMatch && request.method === "POST") {
    const task = taskById(session, decodeURIComponent(checklistScoreMatch[1]));
    const itemId = decodeURIComponent(checklistScoreMatch[2]);
    const item = task?.checklist.find((candidate) => candidate.id === itemId);
    const body = await readJsonBody(request);
    if (!task || !item || typeof body.completed !== "boolean") {
      sendJson(response, 404, { ok: false, error: { code: "task_not_found", message: "Checklist item not found." } });
      return;
    }
    session.checklistRequests += 1;
    if (session.scenario === "score-day-gate") {
      session.dayRefreshRequired = true;
      sendJson(response, 409, {
        ok: false,
        error: { code: "day_refresh_required", message: "Review yesterday before scoring." },
      });
      return;
    }
    item.completed = body.completed;
    sendJson(response, 200, { ok: true, task: copy(task) });
    return;
  }

  const taskMatch = url.pathname.match(/^\/miniapp\/api\/tasks\/([^/]+)$/);
  if (taskMatch && request.method === "PATCH") {
    const task = taskById(session, decodeURIComponent(taskMatch[1]));
    if (!task) {
      sendJson(response, 404, { ok: false, error: { code: "task_not_found", message: "Task not found." } });
      return;
    }
    Object.assign(task, await readJsonBody(request));
    task.id = decodeURIComponent(taskMatch[1]);
    sendJson(response, 200, { ok: true, task: copy(task) });
    return;
  }

  if (taskMatch && request.method === "DELETE") {
    const id = decodeURIComponent(taskMatch[1]);
    session.tasks = session.tasks.filter((task) => task.id !== id);
    sendJson(response, 200, { ok: true });
    return;
  }

  sendJson(response, 404, { ok: false, error: { code: "fixture_not_found", message: "Unknown fixture route." } });
}

async function fixtureHandler(request, response) {
  try {
    const url = new URL(request.url, "http://fixture.invalid");
    if (url.pathname === "/") {
      const theme = VALID_THEMES.has(url.searchParams.get("theme")) ? url.searchParams.get("theme") : "dark";
      const scenario = VALID_SCENARIOS.has(url.searchParams.get("scenario")) ? url.searchParams.get("scenario") : "normal";
      const requestedId = String(url.searchParams.get("sid") || "visual").toLowerCase();
      const sessionId = /^[a-z0-9-]{1,80}$/.test(requestedId) ? requestedId : "visual";
      sessions.set(sessionId, newSession(scenario));
      sendBytes(response, 200, "text/html; charset=utf-8", await renderTemplate(theme), {
        "Set-Cookie": `visual-session=${encodeURIComponent(sessionId)}; HttpOnly; SameSite=Strict; Path=/`,
      });
      return;
    }

    if (url.pathname.startsWith("/static/miniapp/")) {
      const fileName = path.basename(url.pathname);
      const applicationFiles = ["theme-init.js", "app.css", "gameplay.js", "tasks.js", "app.js"];
      const fontFiles = [
        "InstrumentSerif-Regular.ttf",
        "Geist-Regular.woff2",
        "Geist-Medium.woff2",
        "Geist-SemiBold.woff2",
        "Geist-Bold.woff2",
      ];
      if (!applicationFiles.includes(fileName) && !fontFiles.includes(fileName)) {
        sendJson(response, 404, { ok: false });
        return;
      }
      const filePath = fontFiles.includes(fileName)
        ? path.join(STATIC_ROOT, "fonts", fileName)
        : path.join(STATIC_ROOT, fileName);
      const body = await fs.promises.readFile(filePath);
      const contentType = fileName.endsWith(".css")
        ? "text/css; charset=utf-8"
        : fileName.endsWith(".woff2")
          ? "font/woff2"
          : fileName.endsWith(".ttf")
            ? "font/ttf"
            : "text/javascript; charset=utf-8";
      sendBytes(response, 200, contentType, body);
      return;
    }

    if (url.pathname.startsWith("/miniapp/api/")) {
      await handleApi(request, response, url, sessionFor(request));
      return;
    }

    sendJson(response, 404, { ok: false });
  } catch (error) {
    sendJson(response, 500, {
      ok: false,
      error: { code: "fixture_failure", message: error instanceof Error ? error.message : "Fixture failure." },
    });
  }
}

async function startServer() {
  const server = http.createServer((request, response) => { void fixtureHandler(request, response); });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object");
  return { server, origin: `http://127.0.0.1:${address.port}` };
}

function findExecutable() {
  const explicit = String(process.env.PUPPETEER_EXECUTABLE_PATH || "").trim();
  if (explicit) return explicit;
  try {
    const bundled = puppeteer.executablePath();
    if (bundled && fs.existsSync(bundled)) return bundled;
  } catch (_error) {
    // Continue through the ordinary system browser candidates.
  }
  const programFiles = String(process.env.ProgramFiles || process.env.PROGRAMFILES || "").trim();
  const localAppData = String(process.env.LOCALAPPDATA || "").trim();
  return [
    programFiles && path.join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
    localAppData && path.join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
  ]
    .filter(Boolean)
    .find((candidate) => fs.existsSync(candidate));
}

function diagnosticsFor(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  return errors;
}

async function disableMotion(page) {
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.addStyleTag({
    content: "*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}input,textarea{caret-color:transparent!important}",
  });
}

async function openFixture(browser, origin, options) {
  const page = await browser.newPage();
  await page.setViewport({ width: options.width, height: options.height || VIEWPORT_HEIGHT, deviceScaleFactor: 1 });
  const errors = diagnosticsFor(page);
  const sid = options.sid.replace(/[^a-z0-9-]/g, "-");
  const url = `${origin}/?theme=${options.theme}&scenario=${options.scenario || "normal"}&sid=${sid}#${options.view || "habits"}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await disableMotion(page);
  return { page, errors, sid };
}

async function waitForTaskView(page) {
  await page.waitForSelector(".task-row");
  await page.waitForFunction(() => document.querySelector("[data-task-mini-profile]")?.getAttribute("aria-busy") === "false");
  await page.waitForFunction(() => document.querySelector(".task-row__content")?.getBoundingClientRect().height >= 58);
}

async function captureHomeViews(browser, origin, manifest, metricsLog) {
  const configurations = [
    { width: 320, theme: "light" },
    { width: 390, theme: "dark" },
  ];
  for (const configuration of configurations) {
    const label = `home-${configuration.width}-${configuration.theme}`;
    const { page, errors } = await openFixture(browser, origin, {
      ...configuration,
      view: "home",
      sid: `responsive-${label}`,
    });
    try {
      await page.waitForFunction(() => document.querySelector("#profile-card")?.getAttribute("aria-busy") === "false");
      const homeMetrics = await page.evaluate(() => {
        const profile = document.querySelector("#profile-card")?.getBoundingClientRect();
        const stats = document.querySelector(".stats-list")?.getBoundingClientRect();
        const quest = document.querySelector(".quest-log")?.getBoundingClientRect();
        return {
          horizontalOverflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
          profileLeft: profile?.left ?? -1,
          profileRight: profile?.right ?? -1,
          statsHeight: stats?.height ?? 0,
          questHeight: quest?.height ?? 0,
        };
      });
      assert(homeMetrics.horizontalOverflow <= 1, `Home overflows horizontally by ${homeMetrics.horizontalOverflow}px.`);
      assert(homeMetrics.profileLeft >= 0 && homeMetrics.profileRight <= configuration.width + 0.5, "Home profile escapes the viewport.");
      assert(homeMetrics.statsHeight >= 128, `Home stats collapsed to ${homeMetrics.statsHeight}px.`);
      assert(homeMetrics.questHeight >= 180, `Quest log collapsed to ${homeMetrics.questHeight}px.`);
      metricsLog[label] = homeMetrics;
      await screenshot(page, `home-${configuration.width}-${configuration.theme}.png`, manifest, {
        state: "home",
        ...configuration,
      });
      assertClean(errors, label);
    } finally {
      await page.close();
    }
  }
}

async function taskLayoutMetrics(page) {
  return page.evaluate(() => {
    const rows = [...document.querySelectorAll(".task-row")];
    const simpleRows = rows.filter((row) => !row.classList.contains("has-notes"));
    const noteRows = rows.filter((row) => row.classList.contains("has-notes"));
    const controls = [...document.querySelectorAll(".task-row__edge, .task-row__completion")];
    const mini = document.querySelector(".task-mini-profile");
    const miniInner = document.querySelector(".task-mini-profile__inner");
    const toolbar = document.querySelector("[data-view]:not([hidden]) .task-toolbar");
    const nav = document.querySelector(".bottom-nav");
    const potion = document.querySelector("#potion-fab");
    const add = document.querySelector("#task-fab");
    const miniRect = mini?.getBoundingClientRect();
    const innerRect = miniInner?.getBoundingClientRect();
    const toolbarRect = toolbar?.getBoundingClientRect();
    const navRect = nav?.getBoundingClientRect();
    const potionRect = potion && !potion.hidden ? potion.getBoundingClientRect() : null;
    const addRect = add && !add.hidden ? add.getBoundingClientRect() : null;
    const counterPlacements = [...document.querySelectorAll(".task-row__counter")].map((counter) => {
      const row = counter.closest(".task-row");
      const trailingAction = row?.querySelector(".task-row__edge--negative");
      const counterRect = counter.getBoundingClientRect();
      const rowRect = row?.getBoundingClientRect();
      const trailingRect = trailingAction?.getBoundingClientRect();
      return {
        parentIsRow: counter.parentElement === row,
        rightGap: trailingRect ? trailingRect.left - counterRect.right : null,
        counterCenter: counterRect.left + counterRect.width / 2,
        rowCenter: rowRect ? rowRect.left + rowRect.width / 2 : null,
      };
    });
    return {
      rowHeights: rows.map((row) => row.getBoundingClientRect().height),
      simpleHeights: simpleRows.map((row) => row.getBoundingClientRect().height),
      noteHeights: noteRows.map((row) => row.getBoundingClientRect().height),
      actionSizes: controls.map((control) => {
        const rect = control.getBoundingClientRect();
        return { width: rect.width, height: rect.height };
      }),
      miniHeight: innerRect?.height || 0,
      miniToolbarGap: miniRect && toolbarRect ? toolbarRect.top - miniRect.bottom : null,
      visibleRows: navRect
        ? rows.filter((row) => {
          const rect = row.getBoundingClientRect();
          return rect.top >= 0 && rect.bottom <= navRect.top + 0.5;
        }).length
        : 0,
      horizontalOverflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
      navTop: navRect?.top || null,
      potionVisible: Boolean(potionRect && potionRect.width > 0),
      addVisible: Boolean(addRect && addRect.width > 0),
      counterPlacements,
      floatingOverlap: potionRect && addRect
        ? !(potionRect.right <= addRect.left || addRect.right <= potionRect.left || potionRect.bottom <= addRect.top || addRect.bottom <= potionRect.top)
        : false,
    };
  });
}

async function taskBottomClearance(page) {
  await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" }));
  await page.waitForFunction(() => (
    window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2
  ));
  return page.evaluate(() => {
    const rows = [...document.querySelectorAll("[data-view]:not([hidden]) .task-row")];
    const lastRow = rows.at(-1)?.getBoundingClientRect();
    const nav = document.querySelector(".bottom-nav")?.getBoundingClientRect();
    const floatingTops = ["#potion-fab", "#task-fab"]
      .map((selector) => document.querySelector(selector))
      .filter((node) => node && !node.hidden)
      .map((node) => node.getBoundingClientRect().top);
    const obstructionTop = Math.min(nav?.top ?? window.innerHeight, ...floatingTops);
    return {
      clearance: lastRow ? obstructionTop - lastRow.bottom : null,
      lastRowBottom: lastRow?.bottom ?? null,
      obstructionTop,
    };
  });
}

function assertTaskLayout(metrics, { requireDensity = false } = {}) {
  assert(metrics.rowHeights.length > 0, "A representative task list must render.");
  assert(metrics.simpleHeights.every((height) => height >= 58 && height <= 76), `Simple rows escaped 58–76px: ${metrics.simpleHeights}`);
  assert(metrics.noteHeights.every((height) => height >= 58 && height <= 92), `Rows with notes exceeded 92px: ${metrics.noteHeights}`);
  assert(metrics.actionSizes.every(({ width, height }) => width >= 44 && height >= 44), `Task actions are smaller than 44px: ${JSON.stringify(metrics.actionSizes)}`);
  assert(metrics.miniHeight >= 104 && metrics.miniHeight <= 122, `Mini profile is ${metrics.miniHeight}px, outside 104–122px.`);
  assert(metrics.miniToolbarGap === null || metrics.miniToolbarGap >= -0.5, `Mini profile overlaps the task toolbar by ${-metrics.miniToolbarGap}px.`);
  assert(metrics.horizontalOverflow <= 1, `Page overflows horizontally by ${metrics.horizontalOverflow}px.`);
  assert.equal(metrics.potionVisible, true, "The Potion action must remain visible on task pages.");
  assert.equal(metrics.addVisible, true, "The Add action must remain visible on task pages.");
  assert.equal(metrics.floatingOverlap, false, "Potion and Add actions must not overlap.");
  assert(metrics.counterPlacements.every(({ parentIsRow, rightGap, counterCenter, rowCenter }) => (
    parentIsRow
    && rightGap !== null
    && rightGap >= 0
    && rightGap <= 12
    && rowCenter !== null
    && counterCenter > rowCenter
  )), `Habit counters are not aligned at the row's trailing edge: ${JSON.stringify(metrics.counterPlacements)}`);
  if (requireDensity) assert(metrics.visibleRows >= 5, `Only ${metrics.visibleRows} task rows are fully visible at 390px.`);
}

async function screenshot(page, fileName, manifest, metadata = {}) {
  const output = path.join(OUTPUT_ROOT, fileName);
  await page.screenshot({ path: output, fullPage: false });
  manifest.push({ file: path.relative(REPOSITORY_ROOT, output), ...metadata });
}

function assertClean(errors, label, {
  allowHttp429 = false,
  allowHttp409 = false,
  allowHttp503 = false,
  allowHttp502 = false,
  allowHttp504 = false,
} = {}) {
  const unexpected = errors.filter((message) => {
    if (allowHttp429 && (message.includes("429") || message.includes("Too Many Requests"))) return false;
    if (allowHttp503 && (message.includes("503") || message.includes("Service Unavailable"))) return false;
    if (allowHttp502 && (message.includes("502") || message.includes("Bad Gateway"))) return false;
    if (allowHttp504 && (message.includes("504") || message.includes("Gateway Timeout"))) return false;
    if (allowHttp409 && (message.includes("409") || message.includes("Conflict"))) return false;
    return true;
  });
  assert.deepEqual(unexpected, [], `${label} emitted browser errors:\n${unexpected.join("\n")}`);
}

async function captureResponsiveViews(browser, origin, manifest, metricsLog) {
  const configurations = [
    { width: 320, view: "habits" },
    { width: 360, view: "dailies" },
    { width: 390, view: "habits" },
    { width: 430, view: "todos" },
    { width: 568, height: 900, view: "habits" },
    { width: 768, view: "dailies" },
  ];
  for (const theme of ["light", "dark"]) {
    for (const configuration of configurations) {
      const label = `${configuration.view}-${configuration.width}-${theme}`;
      const { page, errors } = await openFixture(browser, origin, {
        ...configuration,
        theme,
        sid: `responsive-${label}`,
      });
      try {
        await waitForTaskView(page);
        const metrics = await taskLayoutMetrics(page);
        assertTaskLayout(metrics, { requireDensity: configuration.width === 390 });
        const bottomClearance = await taskBottomClearance(page);
        assert(
          bottomClearance.clearance >= 16,
          `The final task has only ${bottomClearance.clearance}px clearance above floating controls.`,
        );
        metrics.bottomClearance = bottomClearance;
        metricsLog[label] = metrics;
        await screenshot(page, `task-${label}.png`, manifest, { state: "task-list", ...configuration, theme });
        assertClean(errors, label);
      } finally {
        await page.close();
      }
    }
  }
}

async function captureDisplaySettings(browser, origin, manifest) {
  const { page, errors } = await openFixture(browser, origin, {
    width: 568,
    height: 900,
    theme: "dark",
    view: "habits",
    sid: "display-settings",
  });
  try {
    await waitForTaskView(page);
    const defaultHeight = await page.$eval(".task-row", (row) => row.getBoundingClientRect().height);
    await page.click("#theme-button");
    await page.waitForSelector("#theme-menu:not([hidden])");
    await page.click('[data-display-scale="0.8"]');
    const compact = await page.evaluate(() => ({
      scale: document.documentElement.dataset.displayScale,
      zoom: document.documentElement.style.zoom,
      stored: localStorage.getItem("hh_display_scale"),
      rowHeight: document.querySelector(".task-row")?.getBoundingClientRect().height,
      selected: document.querySelector('[data-display-scale="0.8"]')?.getAttribute("aria-checked"),
      output: document.querySelector("#display-scale-value")?.textContent,
    }));
    assert.equal(compact.scale, "0.8");
    assert.equal(compact.zoom, "0.8");
    assert.equal(compact.stored, "0.8");
    assert.equal(compact.selected, "true");
    assert.equal(compact.output, "80%");
    assert(compact.rowHeight < defaultHeight * 0.85, "The 80% setting did not visibly compact the task rows.");
    await screenshot(page, "state-display-settings-80-dark-568.png", manifest, {
      state: "display-settings",
      width: 568,
      height: 900,
      theme: "dark",
      displayScale: 0.8,
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.documentElement.dataset.displayScale === "0.8");
    assert.equal(
      await page.evaluate(() => document.documentElement.dataset.displayScale),
      "0.8",
      "Display size must persist across reloads.",
    );
    await page.evaluate(() => localStorage.setItem("hh_display_scale", "1.2"));
    await page.setViewport({ width: 320, height: VIEWPORT_HEIGHT, deviceScaleFactor: 1 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTaskView(page);
    await page.click("#theme-button");
    await page.waitForSelector("#theme-menu:not([hidden])");
    const large = await page.evaluate(() => {
      const menu = document.querySelector("#theme-menu")?.getBoundingClientRect();
      return {
        scale: document.documentElement.dataset.displayScale,
        zoom: document.documentElement.style.zoom,
        horizontalOverflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
        menuLeft: menu?.left ?? -1,
        menuRight: menu?.right ?? -1,
      };
    });
    assert.equal(large.scale, "1.2");
    assert.equal(large.zoom, "1.2");
    assert(large.horizontalOverflow <= 1, `The 120% setting overflows by ${large.horizontalOverflow}px.`);
    assert(
      large.menuLeft >= 0 && large.menuRight <= 320 + 0.5,
      `The enlarged Settings panel escapes the viewport (${large.menuLeft}–${large.menuRight}px).`,
    );
    const largeClearance = await taskBottomClearance(page);
    assert(largeClearance.clearance >= 16, "The 120% setting puts the final task beneath the floating controls.");
    await screenshot(page, "state-display-settings-120-dark-320.png", manifest, {
      state: "display-settings",
      width: 320,
      theme: "dark",
      displayScale: 1.2,
    });
    await page.evaluate(() => localStorage.setItem("hh_display_scale", "1"));
    assertClean(errors, "display settings");
  } finally {
    await page.close();
  }
}

async function captureQuickAdd(browser, origin, manifest) {
  const { page, errors } = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "habits",
    sid: "quick-add",
  });
  try {
    await waitForTaskView(page);
    await page.click("#task-fab");
    await page.waitForSelector("#quick-add-dialog[open]");
    await page.waitForFunction(() => document.activeElement?.id === "quick-add-input");
    await screenshot(page, "state-quick-add-dark-390.png", manifest, { state: "quick-add", width: 390, theme: "dark" });

    // A reduced visual viewport approximates the space left above a mobile
    // keyboard and catches sheets that become unreachable while typing.
    await page.setViewport({ width: 390, height: 520, deviceScaleFactor: 1 });
    const keyboardLayout = await page.$eval("#quick-add-dialog", (dialog) => {
      const rect = dialog.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom, viewportHeight: innerHeight };
    });
    assert(keyboardLayout.top >= -1, `Quick add starts above the compact viewport: ${keyboardLayout.top}`);
    assert(keyboardLayout.bottom <= keyboardLayout.viewportHeight + 1, `Quick add extends below the compact viewport: ${keyboardLayout.bottom}`);
    await screenshot(page, "state-quick-add-keyboard-dark-390.png", manifest, { state: "quick-add-keyboard", width: 390, height: 520, theme: "dark" });
    await page.setViewport({ width: 390, height: VIEWPORT_HEIGHT, deviceScaleFactor: 1 });

    await page.type("#quick-add-input", "Visual quick Habit");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.querySelector("#quick-add-dialog")?.hasAttribute("open"));
    await page.waitForFunction(() => [...document.querySelectorAll(".task-row__title")].some((node) => node.textContent === "Visual quick Habit"));
    await page.evaluate(() => {
      [...document.querySelectorAll(".task-row__title")]
        .find((node) => node.textContent === "Visual quick Habit")
        ?.closest(".task-row")
        ?.scrollIntoView({ block: "center" });
    });
    await screenshot(page, "state-quick-add-created-dark-390.png", manifest, { state: "quick-add-created", width: 390, theme: "dark" });

    await page.click("#task-fab");
    await page.waitForSelector("#quick-add-dialog[open]");
    await page.type("#quick-add-input", "Created through more options");
    await page.click("#quick-add-more");
    await page.waitForSelector("#task-editor-dialog[open]");
    assert.equal(await page.$eval("#task-title", (input) => input.value), "Created through more options");
    await screenshot(page, "state-editor-create-more-options-dark-390.png", manifest, { state: "editor-create", width: 390, theme: "dark" });
    assertClean(errors, "quick add and create editor");
  } finally {
    await page.close();
  }
}

async function captureEditorAndDelete(browser, origin, manifest) {
  const { page, errors } = await openFixture(browser, origin, {
    width: 390,
    theme: "light",
    view: "habits",
    sid: "editor-delete",
  });
  try {
    await waitForTaskView(page);
    await page.click('[data-task-id="habit-body"] .task-row__content');
    await page.waitForSelector("#task-editor-dialog[open]");
    assert.equal(await page.$eval("#task-title", (input) => input.value), "Body");
    const editorCoverage = await page.$eval("#task-editor-dialog", (dialog) => {
      const rect = dialog.getBoundingClientRect();
      return { width: rect.width, height: rect.height, viewportWidth: innerWidth, viewportHeight: innerHeight };
    });
    assert(editorCoverage.width >= editorCoverage.viewportWidth - 2, `Editor width is only ${editorCoverage.width}px.`);
    assert(editorCoverage.height >= editorCoverage.viewportHeight - 2, `Editor height is only ${editorCoverage.height}px.`);
    await screenshot(page, "state-editor-edit-light-390.png", manifest, { state: "editor-edit", width: 390, theme: "light" });

    await page.click("#editor-delete");
    await page.waitForSelector("#delete-dialog[open]");
    assert.equal(await page.$eval("#delete-task-title", (node) => node.textContent), "Body");
    await screenshot(page, "state-delete-confirmation-light-390.png", manifest, { state: "delete-confirmation", width: 390, theme: "light" });

    await page.evaluate(() => history.back());
    await page.waitForFunction(() => (
      !document.querySelector("#delete-dialog")?.hasAttribute("open")
      && document.querySelector("#task-editor-dialog")?.hasAttribute("open")
      && history.state?.miniappOverlay === "task-editor-dialog"
    ));
    await page.type("#task-notes", "Unsaved visual note");
    await page.click("#editor-back");
    await page.waitForSelector("#discard-dialog[open]");
    await screenshot(page, "state-discard-warning-light-390.png", manifest, { state: "discard-warning", width: 390, theme: "light" });
    await page.evaluate(() => history.back());
    await page.waitForFunction(() => (
      !document.querySelector("#discard-dialog")?.hasAttribute("open")
      && document.querySelector("#task-editor-dialog")?.hasAttribute("open")
      && history.state?.miniappOverlay === "task-editor-dialog"
    ));
    assertClean(errors, "editor and delete confirmation");
  } finally {
    await page.close();
  }
}

async function captureTaskVariantsAndEditors(browser, origin, manifest) {
  const daily = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "dailies",
    sid: "daily-variants",
  });
  try {
    await waitForTaskView(daily.page);
    await daily.page.click('[data-task-page="daily"] [data-task-filter="all"]');
    await daily.page.waitForSelector('[data-task-id="daily-optional"]');
    await daily.page.waitForSelector('[data-task-id="daily-complete"]');
    await daily.page.$eval('[data-task-id="daily-complete"]', (row) => {
      row.scrollIntoView({ block: "center" });
    });
    await screenshot(daily.page, "state-dailies-all-dark-390.png", manifest, {
      state: "dailies-due-not-due-completed",
      width: 390,
      theme: "dark",
    });

    await daily.page.click('[data-task-id="daily-medicine"] .task-row__content');
    await daily.page.waitForSelector("#task-editor-dialog[open]");
    await screenshot(daily.page, "state-editor-daily-dark-390.png", manifest, {
      state: "editor-daily",
      width: 390,
      theme: "dark",
    });
    const unchecked = await daily.page.$(".checklist-editor__complete:not(:checked)");
    assert(unchecked, "The Daily editor must expose checklist scoring controls.");
    await unchecked.click();
    await daily.page.waitForFunction(() => (
      document.querySelector(".checklist-editor__complete:not(:checked)") === null
    ));
    assert.equal(sessions.get(daily.sid)?.checklistRequests, 1, "Checklist scoring must send one request.");
    assertClean(daily.errors, "Daily variants and editor");
  } finally {
    await daily.page.close();
  }

  const todo = await openFixture(browser, origin, {
    width: 390,
    theme: "light",
    view: "todos",
    sid: "todo-variants",
  });
  try {
    await waitForTaskView(todo.page);
    await todo.page.click('[data-task-page="todo"] [data-task-filter="completed"]');
    await todo.page.waitForSelector('[data-task-id="todo-complete"]');
    await screenshot(todo.page, "state-todos-completed-light-390.png", manifest, {
      state: "todos-completed",
      width: 390,
      theme: "light",
    });

    await todo.page.click('[data-task-page="todo"] [data-task-filter="active"]');
    await todo.page.waitForSelector('[data-task-id="todo-report"]');
    await todo.page.click('[data-task-id="todo-report"] .task-row__content');
    await todo.page.waitForSelector("#task-editor-dialog[open]");
    await screenshot(todo.page, "state-editor-todo-light-390.png", manifest, {
      state: "editor-todo",
      width: 390,
      theme: "light",
    });
    assertClean(todo.errors, "Todo completed view and editor");
  } finally {
    await todo.page.close();
  }
}

async function captureLoadingAndError(browser, origin, manifest) {
  const loading = await openFixture(browser, origin, {
    width: 390,
    theme: "light",
    view: "habits",
    scenario: "loading",
    sid: "loading-state",
  });
  try {
    await loading.page.waitForFunction(() => {
      const node = document.querySelector('[data-task-page="habit"] [data-task-loading]');
      return node && !node.hidden;
    });
    await screenshot(loading.page, "state-loading-light-390.png", manifest, { state: "loading", width: 390, theme: "light" });
    assertClean(loading.errors, "loading state");
  } finally {
    await loading.page.close();
  }

  const failed = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "habits",
    scenario: "error",
    sid: "error-state",
  });
  try {
    await failed.page.waitForFunction(() => {
      const node = document.querySelector('[data-task-page="habit"] [data-task-error]');
      return node && !node.hidden;
    });
    await screenshot(failed.page, "state-error-dark-390.png", manifest, { state: "error", width: 390, theme: "dark" });
    assertClean(failed.errors, "error state", { allowHttp503: true });
  } finally {
    await failed.page.close();
  }
}

async function captureProfileBeforeAfter(browser, origin, manifest) {
  const { page, errors, sid } = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "habits",
    sid: "profile-score",
  });
  try {
    await waitForTaskView(page);
    await screenshot(page, "state-profile-before-score-dark-390.png", manifest, { state: "profile-before-score", width: 390, theme: "dark" });
    await page.click('[data-task-id="habit-body"] .task-row__edge--positive');
    await page.waitForFunction(() => document.querySelector("#mini-gold")?.textContent.replace(/\D/g, "") === "3848");
    await page.waitForFunction(() => document.querySelector("#mini-experience-value")?.textContent.startsWith("4073"));
    await screenshot(page, "state-profile-after-score-dark-390.png", manifest, { state: "profile-after-score", width: 390, theme: "dark" });

    // The inline score patch is immediate; the authoritative /me refresh is
    // deliberately debounced so rapid scores share one follow-up request.
    await new Promise((resolve) => setTimeout(resolve, 350));

    const session = sessions.get(sid);
    assert(session, "Profile scoring fixture session disappeared.");
    assert.equal(session.scoreRequests, 1, "One score tap must produce one score request.");
    assert.equal(session.meRequests, 2, "A confirmed score must coalesce into one follow-up profile request.");
    assert.equal(session.avatarRequests, 1, "Scoring must not rerender or refetch the avatar.");
    assertClean(errors, "profile before/after score");
  } finally {
    await page.close();
  }
}

async function waitForStartupReady(page) {
  await page.waitForFunction(() => (
    document.querySelector("#day-gate")?.hidden === true
    && document.querySelector("#app-shell")?.hasAttribute("inert") === false
  ));
}

async function waitForDayReview(page) {
  await page.waitForFunction(() => (
    document.querySelector("#day-gate")?.dataset.startupState === "review_required"
    && document.querySelector("#day-gate-review")?.hidden === false
  ));
}

async function capturePotionAndCounters(browser, origin, manifest) {
  const home = await openFixture(browser, origin, {
    width: 320,
    theme: "light",
    view: "home",
    sid: "potion-home-light",
  });
  try {
    await waitForStartupReady(home.page);
    await home.page.waitForFunction(() => document.querySelector("#profile-card")?.getAttribute("aria-busy") === "false");
    await home.page.evaluate(() => {
      document.documentElement.style.setProperty("--tg-safe-area-inset-left", "12px");
      document.documentElement.style.setProperty("--tg-safe-area-inset-bottom", "10px");
      document.documentElement.style.setProperty("--tg-content-safe-area-inset-left", "4px");
      document.documentElement.style.setProperty("--tg-content-safe-area-inset-bottom", "6px");
    });
    const homeFloating = await home.page.evaluate(() => {
      const potion = document.querySelector("#potion-fab");
      const add = document.querySelector("#task-fab");
      const nav = document.querySelector(".bottom-nav");
      const potionRect = potion.getBoundingClientRect();
      const navRect = nav.getBoundingClientRect();
      return {
        potionVisible: !potion.hidden && potionRect.width > 0,
        addHidden: add.hidden,
        left: potionRect.left,
        bottomGap: navRect.top - potionRect.bottom,
      };
    });
    assert.equal(homeFloating.potionVisible, true, "Potion must be visible on Home.");
    assert.equal(homeFloating.addHidden, true, "Task Add must remain hidden on Home.");
    assert(homeFloating.left >= 25, `Potion ignored the simulated left safe area: ${homeFloating.left}`);
    assert(homeFloating.bottomGap >= 10, `Potion overlaps the bottom navigation: ${homeFloating.bottomGap}`);
    await screenshot(home.page, "state-home-potion-light-320.png", manifest, {
      state: "home-potion-safe-area",
      width: 320,
      theme: "light",
    });
    await home.page.click("#potion-fab");
    await home.page.waitForSelector("#potion-dialog[open]");
    await home.page.waitForFunction(() => document.querySelector("#potion-submit")?.disabled === false);
    assert.equal(await home.page.$eval("#potion-health", (node) => node.textContent), "40 / 50");
    assert.equal(await home.page.$eval("#potion-price", (node) => node.textContent), "25 gold");
    await screenshot(home.page, "state-potion-confirm-light-320.png", manifest, {
      state: "potion-confirm",
      width: 320,
      theme: "light",
    });
    const homeSession = sessions.get(home.sid);
    assert(homeSession, "Home Potion fixture session disappeared.");
    const firstIntent = homeSession.potionIntent;
    await home.page.click("#potion-cancel");
    await home.page.waitForFunction(() => !document.querySelector("#potion-dialog")?.hasAttribute("open"));
    await home.page.click("#potion-fab");
    await home.page.waitForFunction(() => document.querySelector("#potion-submit")?.disabled === false);
    assert.equal(homeSession.potionGets, 2, "Reopening Potion must fetch a new intent.");
    assert.notEqual(homeSession.potionIntent, firstIntent, "Reopening Potion must replace the old intent.");
    assertClean(home.errors, "Home Potion light");
  } finally {
    await home.page.close();
  }

  const taskPage = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "habits",
    sid: "potion-counters-dark",
  });
  try {
    await waitForTaskView(taskPage.page);
    const counters = await taskPage.page.evaluate(() => ({
      both: document.querySelector('[data-task-id="habit-body"] .task-row__counter')?.textContent,
      bothLabel: document.querySelector('[data-task-id="habit-body"] .task-row__content')?.getAttribute("aria-label"),
      positive: document.querySelector('[data-task-id="habit-apply"] .task-row__counter')?.textContent,
      positiveFrequency: document.querySelector('[data-task-id="habit-apply"] .task-row__counter')?.dataset.counterFrequency,
      negative: document.querySelector('[data-task-id="habit-release"] .task-row__counter')?.textContent,
      negativeFrequency: document.querySelector('[data-task-id="habit-release"] .task-row__counter')?.dataset.counterFrequency,
      zeroHidden: document.querySelector('[data-task-id="habit-plants"] .task-row__counter') === null,
    }));
    assert.equal(counters.both, "+2 | −1");
    assert.match(counters.bothLabel, /2 positive scores and 1 negative score today/);
    assert.equal(counters.positive, "+4");
    assert.equal(counters.positiveFrequency, "weekly");
    assert.equal(counters.negative, "−1");
    assert.equal(counters.negativeFrequency, "monthly");
    assert.equal(counters.zeroHidden, true);

    const session = sessions.get(taskPage.sid);
    assert(session, "Potion fixture session disappeared.");
    const avatarRequestsBefore = session.avatarRequests;
    await taskPage.page.click("#potion-fab");
    await taskPage.page.waitForSelector("#potion-dialog[open]");
    await taskPage.page.waitForFunction(() => document.querySelector("#potion-submit")?.disabled === false);
    await screenshot(taskPage.page, "state-potion-confirm-dark-390.png", manifest, {
      state: "potion-confirm",
      width: 390,
      theme: "dark",
    });
    await taskPage.page.click("#potion-submit");
    await taskPage.page.waitForFunction(() => !document.querySelector("#potion-dialog")?.hasAttribute("open"));
    await taskPage.page.waitForFunction(() => (
      document.querySelector("#mini-health-value")?.textContent === "50/50"
      && document.querySelector("#mini-gold")?.textContent.replace(/\D/g, "") === "3822"
    ));
    assert.equal(session.potionPosts, 1, "Potion confirmation must send one POST.");
    assert.deepEqual(session.potionBodies, [{ purchaseIntent: session.potionIntent }]);
    assert.equal(session.avatarRequests, avatarRequestsBefore, "Potion use must not refetch the avatar.");
    await taskPage.page.click('[data-tab="home"]');
    await taskPage.page.waitForFunction(() => (
      document.querySelector("#health-value")?.textContent === "50 / 50"
      && document.querySelector("#gold-value")?.textContent.replace(/\D/g, "") === "3822"
    ));
    await screenshot(taskPage.page, "state-potion-success-home-dark-390.png", manifest, {
      state: "potion-success-shared-profile",
      width: 390,
      theme: "dark",
    });
    assert.equal(session.avatarRequests, avatarRequestsBefore, "Home synchronization must reuse the avatar.");
    assertClean(taskPage.errors, "Potion purchase and Habit counters");
  } finally {
    await taskPage.page.close();
  }

  const unknown = await openFixture(browser, origin, {
    width: 390,
    theme: "light",
    view: "home",
    scenario: "potion-unknown",
    sid: "potion-unknown-outcome",
  });
  try {
    await waitForStartupReady(unknown.page);
    await unknown.page.click("#potion-fab");
    await unknown.page.waitForSelector("#potion-dialog[open]");
    await unknown.page.waitForFunction(() => document.querySelector("#potion-submit")?.disabled === false);
    await unknown.page.click("#potion-submit");
    await unknown.page.waitForFunction(() => (
      document.querySelector("#potion-submit")?.textContent === "Check Habitica First"
      && document.querySelector("#potion-submit")?.disabled === true
    ));
    const session = sessions.get(unknown.sid);
    assert(session, "Unknown Potion fixture session disappeared.");
    assert.equal(session.potionPosts, 1);
    assert.deepEqual(session.potionBodies, [{ purchaseIntent: session.potionIntent }]);
    await unknown.page.$eval("#potion-submit", (button) => button.click());
    await delay(100);
    assert.equal(session.potionPosts, 1, "An unknown Potion outcome must stay session-blocked.");
    await screenshot(unknown.page, "state-potion-unknown-light-390.png", manifest, {
      state: "potion-unknown-outcome",
      width: 390,
      theme: "light",
    });
    assertClean(unknown.errors, "Potion unknown outcome", { allowHttp504: true });
  } finally {
    await unknown.page.close();
  }

  const dirtyEditor = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "dailies",
    scenario: "score-day-gate",
    sid: "dirty-editor-day-gate",
  });
  try {
    await waitForTaskView(dirtyEditor.page);
    await dirtyEditor.page.click('[data-task-id="daily-medicine"] .task-row__content');
    await dirtyEditor.page.waitForSelector("#task-editor-dialog[open]");
    await dirtyEditor.page.type("#task-notes", " unsaved");
    await dirtyEditor.page.click(".checklist-editor__complete:not(:checked)");
    await dirtyEditor.page.waitForSelector("#discard-dialog[open]");
    assert.equal(await dirtyEditor.page.$eval("#day-gate", (gate) => gate.hidden), true, "The day gate must not open behind a dirty-editor decision.");
    assert.equal(await dirtyEditor.page.$eval("#task-editor-dialog", (dialog) => dialog.hasAttribute("open")), true);
    await screenshot(dirtyEditor.page, "state-day-gate-dirty-editor-dark-390.png", manifest, {
      state: "day-gate-dirty-editor-decision",
      width: 390,
      theme: "dark",
    });
    await dirtyEditor.page.click("#discard-cancel");
    assert.equal(await dirtyEditor.page.$eval("#task-editor-dialog", (dialog) => dialog.hasAttribute("open")), true, "Keep editing must preserve the draft.");
    assert.match(await dirtyEditor.page.$eval("#task-notes", (input) => input.value), /unsaved/);
    await dirtyEditor.page.click("#editor-back");
    await dirtyEditor.page.waitForSelector("#discard-dialog[open]");
    await dirtyEditor.page.click("#discard-confirm");
    await waitForDayReview(dirtyEditor.page);
    assert.equal(await dirtyEditor.page.$eval("#task-editor-dialog", (dialog) => dialog.hasAttribute("open")), false);
    assertClean(dirtyEditor.errors, "dirty editor day gate", { allowHttp409: true });
  } finally {
    await dirtyEditor.page.close();
  }
}

async function captureDayGateFlows(browser, origin, manifest) {
  const loading = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "habits",
    scenario: "day-loading",
    sid: "day-loading-gate",
  });
  try {
    await loading.page.waitForFunction(() => document.querySelector("#day-gate")?.dataset.startupState === "checking_day");
    await delay(180);
    const session = sessions.get(loading.sid);
    assert(session, "Day loading fixture session disappeared.");
    assert.equal(session.meRequests, 0, "Profile must not load before day status resolves.");
    assert.equal(session.avatarRequests, 0, "Avatar must not load before day status resolves.");
    assert.equal(session.taskListRequests, 0, "Tasks must not load before day status resolves.");
    await screenshot(loading.page, "state-day-checking-dark-390.png", manifest, {
      state: "checking-day",
      width: 390,
      theme: "dark",
    });
    assertClean(loading.errors, "day loading gate");
  } finally {
    await loading.page.close();
  }

  const one = await openFixture(browser, origin, {
    width: 320,
    theme: "light",
    view: "habits",
    scenario: "day-one",
    sid: "day-one-review",
  });
  try {
    await waitForDayReview(one.page);
    const session = sessions.get(one.sid);
    assert(session, "One-day fixture session disappeared.");
    assert.equal(session.meRequests, 0);
    assert.equal(session.avatarRequests, 0);
    assert.equal(session.taskListRequests, 0);
    await screenshot(one.page, "state-day-one-light-320.png", manifest, {
      state: "record-yesterday-one",
      width: 320,
      theme: "light",
    });
    await one.page.click('.day-review-task input[type="checkbox"]');
    await one.page.click("#day-review-submit");
    await one.page.waitForFunction(() => document.querySelector("#day-gate")?.dataset.startupState === "submitting_review");
    await screenshot(one.page, "state-day-submitting-light-320.png", manifest, {
      state: "record-yesterday-submitting",
      width: 320,
      theme: "light",
    });
    await waitForStartupReady(one.page);
    await one.page.waitForFunction(() => document.querySelector("#display-name")?.textContent === "h128");
    assert.equal(await one.page.$eval("#class-label", (node) => node.textContent), "Warrior", "Partial cron snapshots must not replace full /me identity.");
    assert.equal(session.dayRefreshRequests, 1);
    assert(session.meRequests >= 1, "A successful refresh must load authoritative /me.");
    assert(session.taskListRequests >= 2, "A successful refresh must invalidate Habits and Dailies.");
    await screenshot(one.page, "state-new-day-started-light-320.png", manifest, {
      state: "new-day-started",
      width: 320,
      theme: "light",
    });
    assertClean(one.errors, "one-day review and success");
  } finally {
    await one.page.close();
  }

  for (const configuration of [
    { scenario: "day-review", width: 390, theme: "dark", file: "state-day-several-dark-390.png", state: "record-yesterday-several" },
    { scenario: "day-empty", width: 360, theme: "light", file: "state-day-empty-light-360.png", state: "record-yesterday-empty" },
    { scenario: "day-multiple", width: 430, theme: "dark", file: "state-day-multiple-dark-430.png", state: "record-yesterday-multiple" },
  ]) {
    const fixture = await openFixture(browser, origin, {
      width: configuration.width,
      theme: configuration.theme,
      view: "todos",
      scenario: configuration.scenario,
      sid: `${configuration.scenario}-${configuration.theme}`,
    });
    try {
      await waitForDayReview(fixture.page);
      if (configuration.scenario === "day-review") {
        const boxes = await fixture.page.$$('.day-review-task input[type="checkbox"]');
        await boxes[0].click();
        await boxes[boxes.length - 1].click();
      }
      if (configuration.scenario === "day-empty") {
        assert.equal(await fixture.page.$eval("#day-review-submit", (button) => button.textContent), "Start New Day");
      }
      if (configuration.scenario === "day-multiple") {
        assert.match(await fixture.page.$eval("#day-review-lead", (node) => node.textContent), /4 Habitica days/);
      }
      await screenshot(fixture.page, configuration.file, manifest, {
        state: configuration.state,
        width: configuration.width,
        theme: configuration.theme,
      });
      assertClean(fixture.errors, configuration.state);
    } finally {
      await fixture.page.close();
    }
  }

  const batched = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "dailies",
    scenario: "day-batch",
    sid: "day-batch-continuation",
  });
  try {
    await waitForDayReview(batched.page);
    assert.equal(await batched.page.$$eval('.day-review-task input[type="checkbox"]', (inputs) => inputs.length), 12);
    await batched.page.$$eval('.day-review-task input[type="checkbox"]', (inputs) => inputs.forEach((input) => input.click()));
    assert.equal(await batched.page.$$eval('.day-review-task input[type="checkbox"]', (inputs) => inputs.filter((input) => input.checked).length), 12, "Record Yesterday must allow every visible selection.");
    await batched.page.click("#day-review-submit");
    await batched.page.waitForFunction(() => (
      document.querySelector("#day-gate")?.dataset.startupState === "refresh_failed"
      && document.querySelectorAll('.day-review-task input[type="checkbox"]').length === 4
      && /wait about a minute/i.test(document.querySelector("#day-review-error")?.textContent)
    ));
    assert.equal(await batched.page.$$eval('.day-review-task input[type="checkbox"]', (inputs) => inputs.every((input) => input.checked)), true, "Only unresolved selections must remain checked.");
    await delay(900);
    assert.equal(sessions.get(batched.sid)?.dayRefreshRequests, 1, "The continuation must never auto-retry.");
    await screenshot(batched.page, "state-day-batch-incomplete-dark-390.png", manifest, {
      state: "record-yesterday-batch-incomplete",
      width: 390,
      theme: "dark",
    });
    assertClean(batched.errors, "Record Yesterday bounded continuation", { allowHttp429: true });
  } finally {
    await batched.page.close();
  }

  const partial = await openFixture(browser, origin, {
    width: 390,
    theme: "dark",
    view: "dailies",
    scenario: "day-partial",
    sid: "day-partial-retry",
  });
  try {
    await waitForDayReview(partial.page);
    const boxes = await partial.page.$$('.day-review-task input[type="checkbox"]');
    await boxes[0].click();
    await boxes[1].click();
    const resolvedTitle = await partial.page.$eval(".day-review-task strong", (node) => node.textContent);
    await partial.page.click("#day-review-submit");
    await partial.page.waitForFunction(() => (
      document.querySelector("#day-gate")?.dataset.startupState === "refresh_failed"
      && document.querySelector("#day-review-error")?.hidden === false
    ));
    assert.equal(await partial.page.$eval("#day-review-error", (node) => /Still unresolved:/.test(node.textContent)), true);
    assert.equal(await partial.page.evaluate((title) => (
      [...document.querySelectorAll(".day-review-task strong")].some((node) => node.textContent === title)
    ), resolvedTitle), false, "A recorded partial-success Daily must leave the retry list.");
    assert.equal(await partial.page.$eval('.day-review-task input[type="checkbox"]', (input) => input.checked), true, "The unresolved selection must stay checked.");
    await screenshot(partial.page, "state-day-partial-dark-390.png", manifest, {
      state: "record-yesterday-partial-failure",
      width: 390,
      theme: "dark",
    });
    await partial.page.click("#day-review-submit");
    await waitForStartupReady(partial.page);
    assert.equal(sessions.get(partial.sid)?.dayRefreshRequests, 2, "A deliberate safe retry must submit only unresolved work.");
    assertClean(partial.errors, "day partial failure", { allowHttp502: true });
  } finally {
    await partial.page.close();
  }

  const unknown = await openFixture(browser, origin, {
    width: 390,
    theme: "light",
    view: "habits",
    scenario: "day-unknown",
    sid: "day-unknown-outcome",
  });
  try {
    await waitForDayReview(unknown.page);
    await unknown.page.click('.day-review-task input[type="checkbox"]');
    await unknown.page.click("#day-review-submit");
    await unknown.page.waitForFunction(() => document.querySelector("#day-review-submit")?.textContent === "Check Day Status");
    const session = sessions.get(unknown.sid);
    assert(session, "Unknown-outcome fixture session disappeared.");
    assert.equal(session.dayRefreshRequests, 1);
    await screenshot(unknown.page, "state-day-unknown-light-390.png", manifest, {
      state: "record-yesterday-unknown-outcome",
      width: 390,
      theme: "light",
    });
    await unknown.page.click("#day-review-submit");
    await unknown.page.waitForFunction(() => document.querySelector("#day-review-submit")?.textContent === "Check Day Status");
    assert.equal(session.dayRefreshRequests, 1, "Unknown cron outcomes must never cause a second POST.");
    session.dayRefreshRequired = false;
    await unknown.page.click("#day-review-submit");
    await waitForStartupReady(unknown.page);
    assert.equal(session.dayRefreshRequests, 1, "A safe status resolution must use GET only.");
    assertClean(unknown.errors, "day unknown outcome", { allowHttp504: true });
  } finally {
    await unknown.page.close();
  }
}

async function main() {
  await fs.promises.mkdir(OUTPUT_ROOT, { recursive: true, mode: 0o700 });
  const executablePath = findExecutable();
  if (!executablePath) {
    throw new Error("No Chromium executable was found. Set PUPPETEER_EXECUTABLE_PATH to a compatible local Chromium/Chrome binary.");
  }

  const { server, origin } = await startServer();
  let browser;
  const manifest = [];
  const metrics = {};
  try {
    browser = await puppeteer.launch({
      executablePath,
      headless: "shell",
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--hide-scrollbars"],
    });
    await captureHomeViews(browser, origin, manifest, metrics);
    await captureResponsiveViews(browser, origin, manifest, metrics);
    await captureDisplaySettings(browser, origin, manifest);
    await captureTaskVariantsAndEditors(browser, origin, manifest);
    await captureQuickAdd(browser, origin, manifest);
    await captureEditorAndDelete(browser, origin, manifest);
    await captureLoadingAndError(browser, origin, manifest);
    await captureProfileBeforeAfter(browser, origin, manifest);
    await capturePotionAndCounters(browser, origin, manifest);
    await captureDayGateFlows(browser, origin, manifest);
    await fs.promises.writeFile(
      path.join(OUTPUT_ROOT, "manifest.json"),
      `${JSON.stringify({ screenshots: manifest, metrics }, null, 2)}\n`,
      { mode: 0o600 },
    );
    process.stdout.write(`Visual redesign checks passed: ${manifest.length} screenshots in ${path.relative(REPOSITORY_ROOT, OUTPUT_ROOT)}\n`);
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message || error}\n`);
  process.exitCode = 1;
});
