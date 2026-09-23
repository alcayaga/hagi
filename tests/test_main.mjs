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
  assert.ok(mainSource.includes('${cleanSpa ? `<div class="text-sm flex items-center gap-2"><span class="flex-shrink-0 px-1.5 py-0.5 rounded text-[0.65rem] font-bold bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/20 shadow-sm">SPA</span><span class="text-gray-600 dark:text-gray-300 font-normal leading-relaxed"> ${highlightText(cleanSpa)}</span></div>` : ""}'));
  assert.ok(mainSource.includes('${cleanEng ? `<div class="text-sm flex items-center gap-2"><span class="flex-shrink-0 px-1.5 py-0.5 rounded text-[0.65rem] font-bold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 shadow-sm">ENG</span><span class="text-gray-600 dark:text-gray-300 font-normal leading-relaxed"> ${highlightText(cleanEng)}</span></div>` : ""}'));
  assert.ok(mainSource.includes('if (cleanSpa) transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("spa").badge} mr-1 align-middle">SPA</span> <span class="text-gray-500 dark:text-gray-400 italic align-middle">${escapeHtml(cleanSpa)}</span></div>`;'));
  assert.ok(mainSource.includes('if (cleanEng) transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("eng").badge} mr-1 align-middle">ENG</span> <span class="text-gray-500 dark:text-gray-400 italic align-middle">${escapeHtml(cleanEng)}</span></div>`;'));
  assert.ok(mainSource.includes('transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${c.badge} mr-1 align-middle">${langLabel}</span> <span class="text-gray-500 dark:text-gray-400 italic align-middle">${newSecondaryText}</span></div>`;'));
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
