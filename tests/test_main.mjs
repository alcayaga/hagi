import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const mainSource = readFileSync(new URL("../src/hagi/static/js/main.js", import.meta.url), "utf8");
const timelineStart = mainSource.indexOf("let timelineData =");
const timelineEnd = mainSource.indexOf("/**\n * Renders the custom visual timeline", timelineStart);
const timelineSource = mainSource.slice(timelineStart, timelineEnd);

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
};

const contextResponse = (id, json = Promise.resolve()) => ({
  ok: true,
  json: () => json.then(() => ({ target_context: [{ id, start_time: id, end_time: id + 1 }] })),
});

const createTimelineRuntime = (fetch) => {
  const container = { innerHTML: "" };
  const renderedIds = [];
  const context = {
    currentExtraction: { padEnd: 0, padStart: 0 },
    document: { getElementById: () => container },
    fetch,
    renderTimeline: (data) => renderedIds.push(data.target_context[0].id),
  };

  vm.runInNewContext(`${timelineSource}\n` + "globalThis.openExtractionTimeline = openExtractionTimeline;\n" + "globalThis.getTimelineTargetId = () => timelineData.target?.id;", context);

  return { container, context, renderedIds };
};

test("an older timeline fetch cannot overwrite a newer timeline", async () => {
  const olderFetch = deferred();
  const newerFetch = deferred();
  const responses = [olderFetch.promise, newerFetch.promise];
  const runtime = createTimelineRuntime(() => responses.shift());

  const olderLoad = runtime.context.openExtractionTimeline(1);
  const newerLoad = runtime.context.openExtractionTimeline(2);
  newerFetch.resolve(contextResponse(2));
  await newerLoad;

  olderFetch.resolve(contextResponse(1));
  await olderLoad;

  assert.deepEqual(runtime.renderedIds, [2]);
  assert.equal(runtime.context.getTimelineTargetId(), 2);
});

test("an older timeline JSON body cannot overwrite a newer timeline", async () => {
  const olderJson = deferred();
  const olderJsonStarted = deferred();
  const responses = [
    Promise.resolve({
      ok: true,
      json: () => {
        olderJsonStarted.resolve();
        return olderJson.promise.then(() => ({ target_context: [{ id: 1, start_time: 1, end_time: 2 }] }));
      },
    }),
    Promise.resolve(contextResponse(2)),
  ];
  const runtime = createTimelineRuntime(() => responses.shift());

  const olderLoad = runtime.context.openExtractionTimeline(1);
  await olderJsonStarted.promise;
  const newerLoad = runtime.context.openExtractionTimeline(2);
  await newerLoad;

  olderJson.resolve();
  await olderLoad;

  assert.deepEqual(runtime.renderedIds, [2]);
  assert.equal(runtime.context.getTimelineTargetId(), 2);
});

test("an older timeline error cannot replace newer timeline output", async () => {
  const olderFetch = deferred();
  const responses = [olderFetch.promise, Promise.resolve(contextResponse(2))];
  const runtime = createTimelineRuntime(() => responses.shift());

  const olderLoad = runtime.context.openExtractionTimeline(1);
  const newerLoad = runtime.context.openExtractionTimeline(2);
  await newerLoad;
  const newerOutput = runtime.container.innerHTML;

  olderFetch.reject(new Error("stale failure"));
  await olderLoad;

  assert.equal(runtime.container.innerHTML, newerOutput);
  assert.deepEqual(runtime.renderedIds, [2]);
  assert.equal(runtime.context.getTimelineTargetId(), 2);
});

test("translation badge and text formatting includes space separator in extraction and search", () => {
  assert.match(mainSource, /SPA<\/span><span[^>]*> \$\{highlightText\(cleanSpa\)\}/);
  assert.match(mainSource, /ENG<\/span><span[^>]*> \$\{highlightText\(cleanEng\)\}/);
  assert.match(mainSource, /SPA<\/span> <span[^>]*>\$\{escapeHtml\(cleanSpa\)\}/);
  assert.match(mainSource, /ENG<\/span> <span[^>]*>\$\{escapeHtml\(cleanEng\)\}/);
  assert.match(mainSource, /\$\{langLabel\}<\/span> <span[^>]*>\$\{newSecondaryText\}/);
});

test("updateEncompassedText renders secondary translation badge followed by a space", () => {
  const elements = {
    mediaText: { innerHTML: "" },
    mediaTranslations: { innerHTML: "" },
  };
  const context = {
    timelineData: {
      selectedStart: 0,
      selectedEnd: 10,
      target: { text: "テスト" },
      contextData: {
        target_lang: "jpn",
        target_context: [{ text: "テスト", start_time: 1, end_time: 2 }],
        secondary_lang: "eng",
        secondary_context: [{ text: "Test translation.", start_time: 1, end_time: 2 }],
      },
    },
    document: {
      getElementById: (id) => elements[id] || { innerHTML: "" },
    },
    escapeHtml: (str) => str,
    highlightSearchTerms: (str) => str,
    getLangColors: () => ({ badge: "bg-emerald-600 text-white shadow-sm" }),
  };

  const updateFuncStart = mainSource.indexOf("function updateEncompassedText()");
  const updateFuncEnd = mainSource.indexOf("/**\n * Binds mouse and touch events", updateFuncStart);
  const updateFuncSource = mainSource.slice(updateFuncStart, updateFuncEnd);

  vm.runInNewContext(updateFuncSource + "\nupdateEncompassedText();", context);

  assert.match(elements.mediaTranslations.innerHTML, /<span [^>]*>ENG<\/span> <span [^>]*>Test translation.<\/span>/);
});

test("search result cards include responsive thumbnail container and skeleton loader", () => {
  assert.match(mainSource, /class="thumb-container relative w-full sm:w-72 md:w-80 lg:w-96 aspect-video rounded-2xl overflow-hidden flex-shrink-0 self-center/);
  assert.match(mainSource, /class="thumb-skeleton absolute inset-0/);
  assert.match(mainSource, /class="shimmer-wave"/);
  assert.doesNotMatch(mainSource, /class="thumb-status-badge/);
  assert.match(mainSource, /class="thumb-wait-overlay hidden/);
  assert.match(mainSource, /class="thumb-img absolute inset-0/);
  assert.match(mainSource, /class="thumb-error hidden absolute inset-0/);
  assert.match(mainSource, /class="btn-extract/);
  assert.match(mainSource, /thumbnailManager\.observe\(thumbContainer\)/);
  assert.match(mainSource, /sm:group-hover:opacity-100 transition-opacity/);
  assert.match(mainSource, /text-\[0\.65rem\] md:text-\[0\.7rem\] font-medium text-gray-500/);
});

test("ThumbnailManager limits concurrent thumbnail extraction requests to maxConcurrent", () => {
  const thumbManagerStart = mainSource.indexOf("class ThumbnailManager {");
  const thumbManagerEnd = mainSource.indexOf("const thumbnailManager =", thumbManagerStart);
  const thumbManagerSource = mainSource.slice(thumbManagerStart, thumbManagerEnd);

  const context = {
    document: {
      /**
       * Mock document.getElementById returning default padding values.
       * @param {string} id - The element ID.
       */
      getElementById: (id) => {
        if (id === "padStart") return { value: "0.1" };
        if (id === "padEnd") return { value: "0.0" };
        return null;
      },
    },
  };

  vm.runInNewContext(thumbManagerSource + "\nglobalThis.ThumbnailManager = ThumbnailManager;", context);
  const manager = new context.ThumbnailManager(2, "100px 0px");

  /**
   * Helper creating a fake thumbnail container for concurrency testing.
   * @param {number} id - Target sentence ID.
   */
  const createFakeContainer = (id) => {
    const img = {
      classList: { add() {}, remove() {} },
      onload: null,
      onerror: null,
      src: "",
    };
    const skeleton = { classList: { add() {} } };
    const errorFallback = { classList: { remove() {} } };
    return {
      dataset: { sentenceId: String(id) },
      isConnected: true,
      querySelector: (sel) => {
        if (sel === ".thumb-img") return img;
        if (sel === ".thumb-skeleton") return skeleton;
        if (sel === ".thumb-error") return errorFallback;
        return null;
      },
      img,
    };
  };

  const c1 = createFakeContainer(1);
  const c2 = createFakeContainer(2);
  const c3 = createFakeContainer(3);

  manager.enqueue(c1);
  manager.enqueue(c2);
  manager.enqueue(c3);

  // Active count should be capped at maxConcurrent (2)
  assert.equal(manager.activeCount, 2);
  assert.equal(manager.queue.length, 1);
  assert.equal(c1.img.src, "/api/thumbnail/1");
  assert.equal(c2.img.src, "/api/thumbnail/2");
  assert.equal(c3.img.src, "");

  // When c1 finishes loading, c3 should be dequeued and start loading
  c1.img.onload();
  assert.equal(manager.activeCount, 2);
  assert.equal(manager.queue.length, 0);
  assert.equal(c3.img.src, "/api/thumbnail/3");

  // When c2 finishes loading, activeCount drops to 1
  c2.img.onload();
  assert.equal(manager.activeCount, 1);

  // When c3 finishes loading, activeCount drops to 0
  c3.img.onload();
  assert.equal(manager.activeCount, 0);
});

test("setCardExtractionState synchronizes thumbnail wait overlay and extract button spinner", () => {
  const extractStateStart = mainSource.indexOf("function setCardExtractionState(");
  const extractStateEnd = mainSource.indexOf("/**\n * Calls the backend API to extract audio", extractStateStart);
  const extractStateSource = mainSource.slice(extractStateStart, extractStateEnd);

  /**
   * Mock classList implementation for tracking DOM token changes in unit tests.
   */
  const createMockClassList = () => {
    const classes = new Set();
    return {
      /**
       * Adds CSS classes to mock token list.
       * @param {...string} cls - Classes to add.
       */
      add: (...cls) => cls.forEach((c) => classes.add(c)),
      /**
       * Removes CSS classes from mock token list.
       * @param {...string} cls - Classes to remove.
       */
      remove: (...cls) => cls.forEach((c) => classes.delete(c)),
      /**
       * Checks if class exists in mock token list.
       * @param {string} c - Class name.
       */
      contains: (c) => classes.has(c),
    };
  };

  const waitOverlay = { classList: createMockClassList() };
  waitOverlay.classList.add("hidden");

  const thumb = {
    classList: createMockClassList(),
    /**
     * Mock querySelector for wait overlay inside thumbnail.
     * @param {string} sel - CSS selector.
     */
    querySelector: (sel) => (sel === ".thumb-wait-overlay" ? waitOverlay : null),
  };

  const btn = {
    dataset: {},
    disabled: false,
    innerHTML: "Extract",
    classList: createMockClassList(),
  };

  const context = {
    document: {
      /**
       * Mock document.querySelector for sentence card components.
       * @param {string} sel - CSS selector.
       */
      querySelector: (sel) => {
        if (sel === '.thumb-container[data-sentence-id="42"]') return thumb;
        if (sel === '.btn-extract[data-sentence-id="42"]') return btn;
        return null;
      },
    },
  };

  vm.runInNewContext(extractStateSource + "\nglobalThis.setCardExtractionState = setCardExtractionState;", context);

  // Trigger loading state
  context.setCardExtractionState(42, true);
  assert.equal(waitOverlay.classList.contains("hidden"), false);
  assert.equal(thumb.classList.contains("pointer-events-none"), true);
  assert.equal(btn.disabled, true);
  assert.equal(btn.classList.contains("cursor-wait"), true);
  assert.match(btn.innerHTML, /Extracting/);
  assert.doesNotMatch(btn.innerHTML, /Extracting\.\.\./);
  assert.match(btn.innerHTML, /whitespace-nowrap/);
  assert.equal(btn.dataset.originalHtml, "Extract");

  // Revert loading state
  context.setCardExtractionState(42, false);
  assert.equal(waitOverlay.classList.contains("hidden"), true);
  assert.equal(thumb.classList.contains("pointer-events-none"), false);
  assert.equal(btn.disabled, false);
  assert.equal(btn.classList.contains("cursor-wait"), false);
  assert.equal(btn.innerHTML, "Extract");
  assert.equal(btn.dataset.originalHtml, undefined);
});

test("ThumbnailManager clear resets queue, activeCount, advances generation, and ignores stale callbacks", () => {
  const thumbManagerStart = mainSource.indexOf("class ThumbnailManager {");
  const thumbManagerEnd = mainSource.indexOf("const thumbnailManager =", thumbManagerStart);
  const thumbManagerSource = mainSource.slice(thumbManagerStart, thumbManagerEnd);

  const context = {
    document: {
      /**
       * Mock getElementById for manager initialization.
       */
      getElementById: () => null,
    },
  };

  vm.runInNewContext(thumbManagerSource + "\nglobalThis.ThumbnailManager = ThumbnailManager;", context);
  const manager = new context.ThumbnailManager(2, "100px 0px");
  assert.equal(manager.generation, 0);

  /**
   * Helper creating a fake thumbnail container.
   * @param {number} id - Target sentence ID.
   */
  const createFakeContainer = (id) => {
    const img = { classList: { add() {}, remove() {} }, onload: null, onerror: null, src: "" };
    return {
      dataset: { sentenceId: String(id) },
      isConnected: true,
      querySelector: (sel) => (sel === ".thumb-img" ? img : null),
      img,
    };
  };

  const c1 = createFakeContainer(10);
  manager.enqueue(c1);
  assert.equal(manager.activeCount, 1);
  assert.equal(manager.generation, 0);

  // Clear resets queue and activeCount, advances generation, and aborts in-flight images
  manager.queue.push({}, {});
  manager.clear();
  assert.equal(manager.queue.length, 0);
  assert.equal(manager.activeCount, 0);
  assert.equal(manager.generation, 1);
  assert.equal(c1.img.src, "");
  assert.equal(c1.img.onload, null);
  assert.equal(c1.img.onerror, null);
});

test("extractMedia returns early without starting duplicate extraction when card is already extracting", async () => {
  const extractMediaStart = mainSource.indexOf("async function extractMedia(");
  const extractMediaEnd = mainSource.indexOf("/**\n * Fetches the surrounding subtitle context", extractMediaStart);
  const extractMediaSource = mainSource.slice(extractMediaStart, extractMediaEnd);

  const thumb = {
    classList: {
      /**
       * Mock classList check for disabled state.
       * @param {string} cls - CSS class name.
       */
      contains: (cls) => cls === "pointer-events-none",
    },
  };
  const btn = {
    disabled: true,
  };

  let historyPushed = false;
  const context = {
    document: {
      /**
       * Mock querySelector for extraction trigger guard.
       * @param {string} sel - Selector string.
       */
      querySelector: (sel) => {
        if (sel.startsWith(".thumb-container")) return thumb;
        if (sel.startsWith(".btn-extract")) return btn;
        return null;
      },
      /**
       * Mock getElementById returning padding.
       */
      getElementById: () => ({ value: "0.1" }),
    },
    history: {
      /**
       * Mock pushState tracking whether navigation occurred.
       */
      pushState: () => {
        historyPushed = true;
      },
    },
    currentExtraction: {},
    /**
     * Mock setCardExtractionState.
     */
    setCardExtractionState: () => {},
    allSearchResults: [],
  };

  vm.runInNewContext(extractMediaSource + "\nglobalThis.extractMedia = extractMedia;", context);

  await context.extractMedia(42);
  assert.equal(historyPushed, false);
});

test("ThumbnailManager requests canonical thumbnail URL without padding query params", () => {
  const thumbManagerStart = mainSource.indexOf("class ThumbnailManager {");
  const thumbManagerEnd = mainSource.indexOf("const thumbnailManager =", thumbManagerStart);
  const thumbManagerSource = mainSource.slice(thumbManagerStart, thumbManagerEnd);

  const context = {
    window: {},
    document: {
      getElementById: () => null,
    },
  };

  vm.runInNewContext(thumbManagerSource + "\nglobalThis.ThumbnailManager = ThumbnailManager;", context);
  const manager = new context.ThumbnailManager(2, "100px 0px");
  const fakeImg = {};
  const fakeContainer = {
    dataset: { sentenceId: "123" },
    querySelector: (selector) => (selector === ".thumb-img" ? fakeImg : null),
  };

  manager.loadThumbnail(fakeContainer);
  assert.equal(fakeImg.src, "/api/thumbnail/123");
});
