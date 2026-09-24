import assert from "node:assert/strict";
import test from "node:test";
import ankiModule from "../src/hagi/static/js/anki.js";

const { DEFAULT_ANKI_CONFIG, getActiveAnkiConfig, saveAnkiConfig, resetAnkiConfig, ankiInvoke, checkAnkiConnection, fetchBlobAsBase64, buildAnkiSearchQueries, stripHtml, buildHighlightedSentence, sendToAnki } = ankiModule;

// Mock localStorage for Node test environment
let mockStorage = {};
globalThis.localStorage = {
  getItem: (key) => mockStorage[key] || null,
  setItem: (key, val) => {
    mockStorage[key] = String(val);
  },
  removeItem: (key) => {
    delete mockStorage[key];
  },
  clear: () => {
    mockStorage = {};
  },
};

test("DEFAULT_ANKI_CONFIG provides valid standard defaults", () => {
  assert.equal(DEFAULT_ANKI_CONFIG.ankiConnectUrl, "http://127.0.0.1:8765");
  assert.equal(DEFAULT_ANKI_CONFIG.deck, "");
  assert.equal(DEFAULT_ANKI_CONFIG.noteType, "");
  assert.deepEqual(DEFAULT_ANKI_CONFIG.tags, []);
  assert.equal(DEFAULT_ANKI_CONFIG.padStart, 0.25);
  assert.equal(DEFAULT_ANKI_CONFIG.padEnd, 0.0);
});

test("getActiveAnkiConfig merges saved localStorage overrides with defaults", () => {
  globalThis.localStorage.clear();
  const initial = getActiveAnkiConfig();
  assert.equal(initial.ankiConnectUrl, "http://127.0.0.1:8765");
  assert.equal(initial.padStart, 0.25);
  assert.equal(initial.padEnd, 0.0);

  saveAnkiConfig({ deck: "CustomDeck", noteType: "CustomModel", wordField: "Front", padStart: 0.5, padEnd: 0.75 });
  const updated = getActiveAnkiConfig();
  assert.equal(updated.deck, "CustomDeck");
  assert.equal(updated.noteType, "CustomModel");
  assert.equal(updated.wordField, "Front");
  assert.equal(updated.ankiConnectUrl, "http://127.0.0.1:8765");
  assert.equal(updated.padStart, 0.5);
  assert.equal(updated.padEnd, 0.75);

  resetAnkiConfig();
  const reset = getActiveAnkiConfig();
  assert.equal(reset.deck, "");
  assert.equal(reset.padStart, 0.25);
  assert.equal(reset.padEnd, 0.0);
});

test("buildAnkiSearchQueries formats pass 1 and pass 2 correctly", () => {
  const config = {
    deck: "Mining",
    noteType: "Lapis",
    wordField: "Expression",
  };

  const queries = buildAnkiSearchQueries("雨", config, false);
  assert.equal(queries.pass1, 'deck:"Mining" note:"Lapis" Expression:"*雨*"');
  assert.equal(queries.pass2, 'deck:"Mining" note:"Lapis" "雨"');

  // Test field with space
  const configSpaced = {
    deck: "Mining",
    noteType: "Lapis",
    wordField: "Word Expression",
  };
  const spacedQueries = buildAnkiSearchQueries("雨", configSpaced, false);
  assert.equal(spacedQueries.pass1, 'deck:"Mining" note:"Lapis" "Word Expression:*雨*"');

  // Test exact search
  const exactQueries = buildAnkiSearchQueries("雨_test*word", config, true);
  assert.equal(queries.pass1, 'deck:"Mining" note:"Lapis" Expression:"*雨*"');
  assert.equal(exactQueries.pass1, 'deck:"Mining" note:"Lapis" Expression:"雨\\_test\\*word"');
});

test("buildAnkiSearchQueries handles empty or whitespace queries", () => {
  const config = { deck: "Anime", noteType: "Vocab" };
  const res = buildAnkiSearchQueries("", config);
  assert.equal(res.pass1, null);
  assert.equal(res.pass2, 'deck:"Anime" note:"Vocab"');
});

test("stripHtml correctly strips sound tags, linebreaks, and list items", () => {
  const rawHtml = "<div>[sound:hagi_123.mp3]Hello<br>World! <ul><li>One</li><li>Two</li></ul></div>";
  const cleaned = stripHtml(rawHtml);
  assert.equal(cleaned, "Hello, World! One, Two");

  assert.equal(stripHtml(""), "");
  assert.equal(stripHtml(null), "");
});

test("buildHighlightedSentence properly bolds search tokens while preserving HTML tags", () => {
  const sentence = "今日はいい天気ですね。";
  const highlighted = buildHighlightedSentence(sentence, "天気 今日");
  assert.equal(highlighted, "<b>今日</b>はいい<b>天気</b>ですね。");

  // Ignores negative search tokens like -exclude
  const highlightedWithNeg = buildHighlightedSentence(sentence, "天気 -exclude");
  assert.equal(highlightedWithNeg, "今日はいい<b>天気</b>ですね。");

  // Does not double-wrap tags if text already contains HTML
  const htmlSentence = "<span>今日は天気です</span>";
  const out = buildHighlightedSentence(htmlSentence, "天気");
  assert.equal(out, "<span>今日は<b>天気</b>です</span>");
});

test("ankiInvoke successfully returns result on version 6 response", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url, opts) => {
      assert.equal(url, "http://127.0.0.1:8765");
      const body = JSON.parse(opts.body);
      assert.equal(body.action, "version");
      assert.equal(body.version, 6);
      return {
        ok: true,
        json: async () => ({ result: 6, error: null }),
      };
    };

    const res = await ankiInvoke("version");
    assert.equal(res, 6);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ankiInvoke throws error if AnkiConnect response contains error message", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ result: null, error: "deck was not found" }),
    });

    await assert.rejects(async () => {
      await ankiInvoke("findNotes", { query: "deck:Missing" });
    }, /deck was not found/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ankiInvoke detects CORS / network failure and suggests webCorsOriginList", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };

    await assert.rejects(
      async () => {
        await ankiInvoke("version");
      },
      (err) => {
        assert.match(err.message, /webCorsOriginList/);
        assert.equal(err.isCorsOrOffline, true);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fetchBlobAsBase64 converts response blob to base64 string", async () => {
  const originalFetch = globalThis.fetch;
  try {
    const fakeData = "Hello Audio Data";
    const fakeBase64 = Buffer.from(fakeData).toString("base64");

    // Mock FileReader
    globalThis.FileReader = class {
      readAsDataURL(blob) {
        this.result = `data:audio/mp3;base64,${fakeBase64}`;
        if (typeof this.onloadend === "function") {
          this.onloadend();
        }
      }
    };

    globalThis.fetch = async () => ({
      ok: true,
      blob: async () => ({ size: fakeData.length }),
    });

    const base64 = await fetchBlobAsBase64("/media/sample.mp3");
    assert.equal(base64, fakeBase64);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ankiInvoke targets urlOverride when provided instead of default config URL", async () => {
  const originalFetch = globalThis.fetch;
  try {
    const customUrl = "http://192.168.1.100:8765";
    globalThis.fetch = async (url, opts) => {
      assert.equal(url, customUrl);
      const body = JSON.parse(opts.body);
      assert.equal(body.action, "version");
      return {
        ok: true,
        json: async () => ({ result: 6, error: null }),
      };
    };

    const res = await ankiInvoke("version", {}, 3000, customUrl);
    assert.equal(res, 6);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("sendToAnki prevents export and prompts to sync media when timeline selection is stale", async () => {
  let toastMsg = null;
  let toastType = null;
  globalThis.showToast = (msg, type) => {
    toastMsg = msg;
    toastType = type;
  };

  globalThis.window = {
    currentExtraction: { id: 42, audioFilename: "audio.mp3", imageFilename: "img.jpg" },
  };

  globalThis.timelineData = {
    selectedStart: 10.5,
    selectedEnd: 15.0,
    lastExtractedStart: 10.0,
    lastExtractedEnd: 15.0,
  };

  const btn = { innerHTML: "Quick Update", disabled: false, classList: { add() {} } };

  await sendToAnki(btn);

  assert.equal(btn.disabled, false);
  assert.equal(toastType, "error");
  assert.match(toastMsg, /Sync Media/);

  delete globalThis.showToast;
  delete globalThis.window;
  delete globalThis.timelineData;
});
