import assert from "node:assert/strict";
import test from "node:test";
import ankiModule from "../src/hagi/static/js/anki.js";

const { DEFAULT_ANKI_CONFIG, getActiveAnkiConfig, saveAnkiConfig, resetAnkiConfig, ankiInvoke, checkAnkiConnection, fetchBlobAsBase64, buildAnkiSearchQueries, stripHtml, buildHighlightedSentence, sendToAnki, openAnkiSettingsModal, closeAnkiSettingsModal } = ankiModule;

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
    target: { id: 42 },
    selectedStart: 10.5,
    selectedEnd: 15.0,
    lastExtractedStart: 10.0,
    lastExtractedEnd: 15.0,
  };

  const btn = { innerHTML: "Quick Update", disabled: false, classList: { add() {} } };

  try {
    await sendToAnki(btn);

    assert.equal(btn.disabled, false);
    assert.equal(toastType, "error");
    assert.match(toastMsg, /Sync Media/);
  } finally {
    delete globalThis.showToast;
    delete globalThis.window;
    delete globalThis.timelineData;
  }
});

test("sendToAnki ignores stale timelineData if target id does not match current extraction id", async () => {
  let toastMsg = null;
  let toastType = null;
  globalThis.showToast = (msg, type) => {
    toastMsg = msg;
    toastType = type;
  };

  globalThis.window = {
    currentExtraction: { id: 42, audioFilename: "audio.mp3", imageFilename: "img.jpg" },
  };

  // timeline belongs to sentence 99, not 42
  globalThis.timelineData = {
    target: { id: 99 },
    selectedStart: 10.5,
    selectedEnd: 15.0,
    lastExtractedStart: 10.0,
    lastExtractedEnd: 15.0,
  };

  const btn = { innerHTML: "Quick Update", disabled: false, classList: { add() {} } };

  try {
    await sendToAnki(btn);
    // Should proceed past stale check (and fail on missing config or note, not on "Sync Media")
    assert.notEqual(toastMsg, "Please click 'Sync Media' before sending to Anki.");
  } finally {
    delete globalThis.showToast;
    delete globalThis.window;
    delete globalThis.timelineData;
  }
});

test("sendToAnki aborts export and alerts user when media storage fails", async () => {
  let toastMsg = null;
  let toastType = null;
  globalThis.showToast = (msg, type) => {
    toastMsg = msg;
    toastType = type;
  };

  globalThis.window = {
    currentExtraction: { id: 42, audioFilename: "audio.mp3", imageFilename: "img.jpg", text: "テスト" },
  };

  saveAnkiConfig({ deck: "Deck", noteType: "Model", audioField: "Audio" });

  const originalFetch = globalThis.fetch;
  let updateCalled = false;
  try {
    globalThis.fetch = async (url, opts) => {
      if (url.includes("/media/")) {
        throw new Error("Disk read error");
      }
      const body = JSON.parse(opts.body);
      if (body.action === "findNotes") {
        return { ok: true, json: async () => ({ result: [123], error: null }) };
      }
      if (body.action === "updateNoteFields") {
        updateCalled = true;
        return { ok: true, json: async () => ({ result: null, error: null }) };
      }
      return { ok: true, json: async () => ({ result: null, error: null }) };
    };

    const btn = { innerHTML: "Quick Update", disabled: false, classList: { add() {}, remove() {} } };
    await sendToAnki(btn);

    assert.equal(updateCalled, false);
    assert.equal(toastType, "error");
    assert.match(toastMsg, /Disk read error/);
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.showToast;
    delete globalThis.window;
    resetAnkiConfig();
  }
});

test("sendToAnki combines sound and image tags when audioField and imageField share the same name", async () => {
  let toastType = null;
  globalThis.showToast = (msg, type) => {
    toastType = type;
  };

  globalThis.window = {
    currentExtraction: { id: 42, audioFilename: "audio.mp3", imageFilename: "img.jpg", text: "テスト" },
  };

  saveAnkiConfig({ deck: "Deck", noteType: "Model", audioField: "Media", imageField: "Media" });

  const originalFetch = globalThis.fetch;
  let updatedFields = null;
  try {
    globalThis.FileReader = class {
      readAsDataURL() {
        this.result = "data:text/plain;base64,ZmFrZQ==";
        this.onloadend();
      }
    };

    globalThis.fetch = async (url, opts) => {
      if (url.includes("/media/")) {
        return { ok: true, blob: async () => ({ size: 10 }) };
      }
      const body = JSON.parse(opts.body);
      if (body.action === "findNotes") {
        return { ok: true, json: async () => ({ result: [123], error: null }) };
      }
      if (body.action === "storeMediaFile") {
        return { ok: true, json: async () => ({ result: body.params.filename, error: null }) };
      }
      if (body.action === "updateNoteFields") {
        updatedFields = body.params.note.fields;
        return { ok: true, json: async () => ({ result: null, error: null }) };
      }
      return { ok: true, json: async () => ({ result: null, error: null }) };
    };

    const btn = { innerHTML: "Quick Update", disabled: false, classList: { add() {}, remove() {} } };
    await sendToAnki(btn);

    assert.equal(toastType, "success");
    assert.ok(updatedFields);
    assert.equal(updatedFields["Media"], '[sound:audio.mp3] <img src="img.jpg">');
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.showToast;
    delete globalThis.window;
    delete globalThis.FileReader;
    resetAnkiConfig();
  }
});

test("openAnkiSettingsModal populates corsConfigSnippet without wildcard and sets origin label", async () => {
  const elements = {
    ankiSettingsModal: { classList: { remove() {} } },
    currentOriginLabel: { textContent: "" },
    corsConfigSnippet: { textContent: "" },
  };

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ result: 6, error: null }),
  });

  globalThis.document = {
    getElementById: (id) => elements[id] || { value: "", setAttribute() {}, classList: { add() {}, remove() {}, contains: () => false } },
    body: { classList: { add() {} } },
  };
  globalThis.window = {
    location: { origin: "http://127.0.0.1:8000" },
  };

  try {
    openAnkiSettingsModal();
    await new Promise((resolve) => setTimeout(resolve, 50));

    assert.equal(elements.currentOriginLabel.textContent, "http://127.0.0.1:8000");
    assert.match(elements.corsConfigSnippet.textContent, /"webCorsOriginList":/);
    assert.match(elements.corsConfigSnippet.textContent, /http:\/\/127\.0\.0\.1:8000/);
    assert.equal(elements.corsConfigSnippet.textContent.includes("*"), false);
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("closeAnkiSettingsModal preserves body overflow-hidden if mediaModal is still open", () => {
  let overflowRemoved = false;
  const elements = {
    ankiSettingsModal: { classList: { add() {} } },
    mediaModal: { classList: { contains: (cls) => cls !== "hidden" } },
    contextModal: { classList: { contains: (cls) => cls === "hidden" } },
  };

  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    body: {
      classList: {
        remove: (cls) => {
          if (cls === "overflow-hidden") overflowRemoved = true;
        },
      },
    },
  };

  closeAnkiSettingsModal();

  assert.equal(overflowRemoved, false);

  // When neither mediaModal nor contextModal is open, overflow-hidden should be removed
  elements.mediaModal.classList.contains = (cls) => cls === "hidden";
  closeAnkiSettingsModal();

  assert.equal(overflowRemoved, true);

  delete globalThis.document;
});

test("sendToAnki restores button state on early exit when no extraction exists", async () => {
  const btn = {
    innerHTML: "Original Text",
    disabled: false,
    className: "btn-confirm",
    classList: {
      add() {},
      remove() {},
    },
    dataset: {
      confirming: "true",
      origText: "Original Text",
      origClass: "btn-normal",
    },
  };

  globalThis.window = { currentExtraction: null };
  let toastMsg = null;
  globalThis.showToast = (msg, type) => {
    toastMsg = msg;
  };

  try {
    await sendToAnki(btn);
    assert.equal(toastMsg, "No sentence currently extracted.");
    assert.equal(btn.innerHTML, "Original Text");
    assert.equal(btn.className, "btn-normal");
    assert.equal(btn.disabled, false);
    assert.equal(btn.dataset.confirming, undefined);
  } finally {
    delete globalThis.window;
    delete globalThis.showToast;
  }
});

test("sendToAnki detects field collisions and notifies user", async () => {
  saveAnkiConfig({
    sentenceField: "TargetField",
    sentenceHighlightedField: "TargetField",
  });

  const btn = {
    innerHTML: "Send",
    disabled: false,
    classList: { add() {}, remove() {} },
  };

  globalThis.window = { currentExtraction: { id: 123 } };
  let errorMsg = null;
  globalThis.showToast = (msg, type) => {
    if (type === "error") errorMsg = msg;
  };

  try {
    await sendToAnki(btn);
    assert.match(errorMsg, /Field collision/);
  } finally {
    resetAnkiConfig();
    delete globalThis.window;
    delete globalThis.showToast;
  }
});
