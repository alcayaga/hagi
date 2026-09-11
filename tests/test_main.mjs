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
