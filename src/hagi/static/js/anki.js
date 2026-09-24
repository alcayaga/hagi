/**
 * @fileoverview Client-side AnkiConnect module for Hagi.
 * Connects directly from the browser to desktop Anki via AnkiConnect (127.0.0.1:8765),
 * eliminating macOS local network privacy and backend daemon connection issues.
 */

// Default configuration fallback
const DEFAULT_ANKI_CONFIG = {
  ankiConnectUrl: "http://127.0.0.1:8765",
  deck: "",
  noteType: "",
  wordField: "",
  definitionField: "",
  sentenceField: "",
  sentenceHighlightedField: "",
  audioField: "",
  imageField: "",
  sourceField: "",
  tags: [],
  padStart: 0.25,
  padEnd: 0.0,
};

let serverAnkiConfig = { ...DEFAULT_ANKI_CONFIG };
let activeAnkiConfig = { ...DEFAULT_ANKI_CONFIG };
let ankiConnectionStatus = "checking"; // "connected" | "disconnected" | "checking"
let searchAnkiCardsAbortController = null;
let searchAnkiCardsTimeout = null;

/**
 * Retrieves the currently active Anki configuration.
 * Preference order: localStorage overrides -> server config.json -> default fallback.
 * @returns {object} The active Anki configuration.
 */
function getActiveAnkiConfig() {
  try {
    const saved = localStorage.getItem("hagi_anki_config");
    if (saved) {
      const parsed = JSON.parse(saved);
      return { ...DEFAULT_ANKI_CONFIG, ...serverAnkiConfig, ...parsed };
    }
  } catch (e) {
    console.error("Failed to read hagi_anki_config from localStorage", e);
  }
  return { ...DEFAULT_ANKI_CONFIG, ...serverAnkiConfig };
}

/**
 * Loads configuration from the backend endpoint /api/anki/config.
 * Merges with any local user preferences saved in localStorage.
 * @returns {Promise<object>} The loaded configuration.
 */
async function loadAnkiConfig() {
  try {
    const res = await fetch("/api/anki/config");
    if (res.ok) {
      const data = await res.json();
      serverAnkiConfig = { ...DEFAULT_ANKI_CONFIG, ...data };
    }
  } catch (err) {
    console.warn("Could not load /api/anki/config from server; using defaults", err);
  }
  activeAnkiConfig = getActiveAnkiConfig();
  return activeAnkiConfig;
}

/**
 * Saves user configuration overrides to localStorage.
 * @param {object} config - Configuration object to save.
 */
function saveAnkiConfig(config) {
  try {
    activeAnkiConfig = { ...activeAnkiConfig, ...config };
    localStorage.setItem("hagi_anki_config", JSON.stringify(config));
  } catch (e) {
    console.error("Failed to write hagi_anki_config to localStorage", e);
  }
}

/**
 * Resets user configuration to the server defaults from config.json.
 */
function resetAnkiConfig() {
  try {
    localStorage.removeItem("hagi_anki_config");
  } catch (e) {
    console.error("Failed to clear hagi_anki_config from localStorage", e);
  }
  activeAnkiConfig = { ...DEFAULT_ANKI_CONFIG, ...serverAnkiConfig };
}

/**
 * Invokes an action on AnkiConnect directly via client-side fetch.
 * @param {string} action - AnkiConnect action name.
 * @param {object} params - Action parameters dictionary.
 * @param {number} timeout - Request timeout in milliseconds (default: 8000ms).
 * @returns {Promise<any>} The result field from AnkiConnect response.
 */
async function ankiInvoke(action, params = {}, timeout = 8000) {
  const config = getActiveAnkiConfig();
  const url = config.ankiConnectUrl || "http://127.0.0.1:8765";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, version: 6, params }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (!response.ok) {
      throw new Error(`AnkiConnect HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    if (data.error) {
      throw new Error(data.error);
    }
    return data.result;
  } catch (err) {
    clearTimeout(timer);
    if (err.name === "AbortError") {
      throw new Error(`Connection to AnkiConnect timed out after ${timeout}ms.`);
    }

    // Identify common browser CORS / network restrictions
    const isNetworkError = err instanceof TypeError && (err.message.includes("Failed to fetch") || err.message.includes("NetworkError") || err.message.includes("Load failed"));

    if (isNetworkError) {
      const errObj = new Error(`Unable to reach AnkiConnect at ${url}. Please ensure Anki is running with AnkiConnect installed. ` + `If running on macOS or over a local network, ensure your origin is listed in AnkiConnect's webCorsOriginList.`);
      errObj.isCorsOrOffline = true;
      throw errObj;
    }
    throw err;
  }
}

/**
 * Checks connection health with AnkiConnect and updates the UI status pill.
 * @returns {Promise<boolean>} True if connection succeeded.
 */
async function checkAnkiConnection() {
  updateAnkiStatusPill("checking");
  try {
    const version = await ankiInvoke("version", {}, 2500);
    if (version) {
      ankiConnectionStatus = "connected";
      updateAnkiStatusPill("connected", `v${version}`);
      return true;
    }
  } catch (err) {
    console.debug("Anki connection check failed:", err.message);
  }
  ankiConnectionStatus = "disconnected";
  updateAnkiStatusPill("disconnected");
  return false;
}

/**
 * Updates the header status pill with current connection status and styling.
 * @param {"connected"|"disconnected"|"checking"} status - The connection status.
 * @param {string} [extraInfo] - Optional version or latency string.
 */
function updateAnkiStatusPill(status, extraInfo = "") {
  const pill = document.getElementById("ankiStatusPill");
  const dot = document.getElementById("ankiStatusDot");
  const text = document.getElementById("ankiStatusText");
  if (!pill || !dot || !text) return;

  dot.className = "w-2.5 h-2.5 sm:w-2 sm:h-2 rounded-full flex-shrink-0";
  pill.classList.remove("border-amber-300", "dark:border-amber-700", "border-emerald-200", "dark:border-emerald-800/60", "border-rose-200", "dark:border-rose-900/60");

  if (status === "connected") {
    dot.classList.add("bg-emerald-500", "animate-pulse");
    const statusLabel = extraInfo ? `Anki Connected (${extraInfo})` : "Anki Connected";
    text.textContent = statusLabel;
    pill.title = `${statusLabel} — Click to configure settings`;
    pill.classList.add("border-emerald-200", "dark:border-emerald-800/60");
  } else if (status === "checking") {
    dot.classList.add("bg-amber-400", "animate-ping");
    text.textContent = "Checking Anki...";
    pill.title = "Checking AnkiConnect... — Click to configure settings";
    pill.classList.add("border-amber-300", "dark:border-amber-700");
  } else {
    dot.classList.add("bg-rose-500");
    text.textContent = "Anki Offline";
    pill.title = "Anki Offline / Unreachable — Click to configure settings";
    pill.classList.add("border-rose-200", "dark:border-rose-900/60");
  }
}

/**
 * Converts a media URL from the local server to a base64 encoded data string.
 * @param {string} url - The media URL to fetch.
 * @returns {Promise<string>} Base64 encoded file data.
 */
async function fetchBlobAsBase64(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch media file for Anki export (${url}): ${response.statusText}`);
  }
  const blob = await response.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      if (typeof reader.result === "string") {
        const parts = reader.result.split(",");
        resolve(parts.length > 1 ? parts[1] : parts[0]);
      } else {
        reject(new Error("FileReader result is not a string"));
      }
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read blob as data URL"));
    reader.readAsDataURL(blob);
  });
}

/**
 * Builds the search query expression for Anki note lookup.
 * @param {string} query - Raw search query string.
 * @param {object} config - Configuration object with deck, noteType, wordField.
 * @param {boolean} exact - Whether to search for exact expression.
 * @returns {{pass1: string|null, pass2: string|null}}
 */
function buildAnkiSearchQueries(query, config, exact = false) {
  const deck = config.deck || "";
  const noteType = config.noteType || "";
  const wordField = config.wordField || "";

  const baseFilters = [];
  if (deck) baseFilters.push(`deck:"${deck}"`);
  if (noteType) baseFilters.push(`note:"${noteType}"`);
  const baseQueryStr = baseFilters.join(" ");

  const safeQuery = query ? query.replace(/\\/g, "\\\\").replace(/"/g, '\\"') : "";
  if (!safeQuery) {
    return { pass1: null, pass2: baseQueryStr || null };
  }

  let pass1 = null;
  if (wordField) {
    const safeExact = safeQuery.replace(/\*/g, "\\*").replace(/_/g, "\\_");
    const fieldExpr = exact ? `"${safeExact}"` : `"*${safeQuery}*"`;
    if (wordField.includes(" ")) {
      pass1 = `${baseQueryStr} "${wordField}:${exact ? safeExact : `*${safeQuery}*`}"`.trim();
    } else {
      pass1 = `${baseQueryStr} ${wordField}:${fieldExpr}`.trim();
    }
  }

  const pass2 = baseQueryStr ? `${baseQueryStr} "${safeQuery}"`.trim() : `"${safeQuery}"`;
  return { pass1, pass2 };
}

/**
 * Debounced wrapper for Anki search to prevent rapid API calls.
 */
function debounceSearchAnkiCards() {
  if (searchAnkiCardsTimeout) clearTimeout(searchAnkiCardsTimeout);
  searchAnkiCardsTimeout = setTimeout(() => {
    searchAnkiCards();
  }, 250);
}

/**
 * Safely strips HTML from Anki fields and formats lists with commas.
 * @param {string} html - The raw HTML string.
 * @returns {string} The cleaned text.
 */
function stripHtml(html) {
  if (!html) return "";
  let clean = html.replace(/\[sound:[^\]]+\]/g, "");
  clean = clean.replace(/<br\s*\/?>/gi, ", ");
  clean = clean.replace(/<\/li>/gi, ", </li>");
  clean = clean.replace(/<\/(div|p|h[1-6])>/gi, " </$1>");

  let text = "";
  if (typeof DOMParser !== "undefined") {
    const parser = new DOMParser();
    const doc = parser.parseFromString(clean, "text/html");
    text = doc.body.textContent || "";
  } else {
    text = clean.replace(/<[^>]*>/g, "");
  }

  text = text.replace(/\s+/g, " ").trim();
  text = text.replace(/,\s*(?=[,])/g, "");
  text = text.replace(/,\s*$/, "");
  return text;
}

/**
 * Searches the user's Anki collection dynamically directly from the browser.
 */
async function searchAnkiCards() {
  const input = document.getElementById("ankiCardSearchInput");
  const resultsContainer = document.getElementById("ankiSearchResults");
  if (!input || !resultsContainer) return;

  const query = input.value.trim();

  if (!query) {
    if (searchAnkiCardsAbortController) {
      searchAnkiCardsAbortController.abort();
      searchAnkiCardsAbortController = null;
    }
    resultsContainer.innerHTML = '<div class="flex items-center justify-center h-full text-gray-400 text-sm">Please enter a search query.</div>';
    return;
  }

  if (searchAnkiCardsAbortController) {
    searchAnkiCardsAbortController.abort();
  }
  const currentController = new AbortController();
  searchAnkiCardsAbortController = currentController;

  resultsContainer.innerHTML = '<div class="flex justify-center mt-8"><div class="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div></div>';

  try {
    const config = getActiveAnkiConfig();
    const { pass1, pass2 } = buildAnkiSearchQueries(query, config, false);

    const limit = 20;
    const uniqueIds = [];
    const seen = new Set();

    // Pass 1: query targeting wordField
    if (pass1) {
      try {
        const ids1 = await ankiInvoke("findNotes", { query: pass1 }, 4000);
        if (Array.isArray(ids1)) {
          for (const nid of ids1) {
            if (!seen.has(nid)) {
              seen.add(nid);
              uniqueIds.push(nid);
              if (uniqueIds.length >= limit) break;
            }
          }
        }
      } catch (err) {
        console.debug("Pass 1 search failed, continuing to broad search", err);
      }
    }

    // Pass 2: broad query across note
    if (uniqueIds.length < limit && pass2) {
      try {
        const ids2 = await ankiInvoke("findNotes", { query: pass2 }, 4000);
        if (Array.isArray(ids2)) {
          for (const nid of ids2) {
            if (!seen.has(nid)) {
              seen.add(nid);
              uniqueIds.push(nid);
              if (uniqueIds.length >= limit) break;
            }
          }
        }
      } catch (err) {
        console.debug("Pass 2 search failed", err);
      }
    }

    if (currentController !== searchAnkiCardsAbortController) return;

    if (uniqueIds.length === 0) {
      resultsContainer.innerHTML = '<div class="flex items-center justify-center h-full text-gray-500 text-sm">No cards found matching your query in Anki.</div>';
      return;
    }

    const notesInfo = await ankiInvoke("notesInfo", { notes: uniqueIds }, 5000);
    if (currentController !== searchAnkiCardsAbortController) return;

    if (!Array.isArray(notesInfo) || notesInfo.length === 0) {
      resultsContainer.innerHTML = '<div class="flex items-center justify-center h-full text-gray-500 text-sm">No note details found.</div>';
      return;
    }

    resultsContainer.innerHTML = "";

    const wordField = config.wordField || "";
    const definitionField = config.definitionField || "";
    const sentenceField = config.sentenceHighlightedField || config.sentenceField || "";

    notesInfo.forEach((note) => {
      if (!note || !note.fields) return;
      const fields = Object.keys(note.fields);
      if (fields.length === 0) return;

      let tier1 = "";
      let tier2 = "";
      let tier3 = "";

      // Tier 1 (Word)
      if (wordField && note.fields[wordField]) {
        tier1 = note.fields[wordField].value || "";
      } else if (note.fields[fields[0]]) {
        tier1 = note.fields[fields[0]].value || "";
      }

      // Tier 2 (Definition)
      if (definitionField && note.fields[definitionField]) {
        tier2 = note.fields[definitionField].value || "";
      } else if (fields.length > 1 && note.fields[fields[1]]) {
        tier2 = note.fields[fields[1]].value || "";
      }

      // Tier 3 (Sentence)
      if (sentenceField && note.fields[sentenceField]) {
        tier3 = note.fields[sentenceField].value || "";
      }

      const currentQuery = document.getElementById("ankiCardSearchInput")?.value.trim() || "";
      const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => s;
      const highlightFn = typeof highlightSearchTerms === "function" ? highlightSearchTerms : (s) => s;

      tier1 = highlightFn(stripHtml(tier1), currentQuery, esc);
      tier2 = highlightFn(stripHtml(tier2), currentQuery, esc);
      tier3 = highlightFn(stripHtml(tier3), currentQuery, esc);

      const el = document.createElement("div");
      el.className = "shrink-0 w-full text-left p-4 rounded-xl dark:bg-gray-800 bg-white border border-gray-100 dark:border-gray-700 shadow-sm flex justify-between items-center group relative overflow-hidden";

      const contentDiv = document.createElement("div");
      contentDiv.className = "flex-1 overflow-hidden pr-2 z-10 pl-1";

      if (tier1) {
        const t1 = document.createElement("div");
        t1.className = "text-lg font-bold text-gray-900 dark:text-gray-100 truncate";
        t1.innerHTML = tier1;
        contentDiv.appendChild(t1);
      }

      if (tier2) {
        const t2 = document.createElement("div");
        t2.className = "text-sm text-gray-500 dark:text-gray-400 truncate mt-1";
        t2.innerHTML = tier2;
        contentDiv.appendChild(t2);
      }

      if (tier3) {
        const t3 = document.createElement("div");
        t3.className = "text-xs text-gray-400 dark:text-gray-500 mt-2 italic truncate border-l-2 border-indigo-200 dark:border-indigo-900/50 pl-2 py-0.5";
        t3.innerHTML = tier3;
        contentDiv.appendChild(t3);
      }

      const selectBadge = document.createElement("button");
      selectBadge.type = "button";
      selectBadge.className = "update-badge ml-2 px-4 py-2 bg-indigo-100 text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-900/50 dark:text-indigo-300 dark:hover:bg-indigo-900/80 text-xs font-bold rounded-lg shadow-sm flex items-center gap-1 z-10 relative cursor-pointer opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 md:focus:opacity-100 transition-opacity duration-300";
      selectBadge.textContent = "Update";

      selectBadge.onclick = (e) => {
        e.stopPropagation();

        if (selectBadge.dataset.confirmTimer) {
          clearTimeout(Number(selectBadge.dataset.confirmTimer));
          delete selectBadge.dataset.confirmTimer;
        }

        if (selectBadge.dataset.confirming === "true") {
          selectBadge.dataset.confirming = "sending";
          sendToAnki(selectBadge, note.noteId);
        } else {
          selectBadge.dataset.confirming = "true";
          selectBadge.dataset.origText = selectBadge.textContent;
          selectBadge.dataset.origClass = selectBadge.className;

          selectBadge.className = "update-badge ml-2 px-4 py-2 bg-red-500 hover:bg-red-600 text-white text-xs font-bold rounded-lg transition shadow-sm flex items-center gap-1 z-10 relative cursor-pointer animate-pulse";
          selectBadge.textContent = "Confirm?";

          selectBadge.dataset.confirmTimer = setTimeout(() => {
            if (selectBadge.dataset.confirming === "true") {
              selectBadge.className = selectBadge.dataset.origClass;
              selectBadge.textContent = selectBadge.dataset.origText;
              delete selectBadge.dataset.confirming;
              delete selectBadge.dataset.origText;
              delete selectBadge.dataset.origClass;
              delete selectBadge.dataset.confirmTimer;
            }
          }, 3000);
        }
      };

      el.appendChild(contentDiv);
      el.appendChild(selectBadge);
      resultsContainer.appendChild(el);
    });
  } catch (err) {
    if (currentController !== searchAnkiCardsAbortController) return;
    const errorElement = document.createElement("div");
    errorElement.className = "flex flex-col items-center justify-center h-full text-center p-4 gap-2";
    errorElement.innerHTML = `
      <span class="text-rose-500 text-sm font-semibold">${escapeHtml(err.message)}</span>
      <button onclick="openAnkiSettingsModal()" class="mt-2 text-xs font-semibold px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition">
        Open Anki Settings &amp; Diagnostics
      </button>
    `;
    resultsContainer.innerHTML = "";
    resultsContainer.appendChild(errorElement);
  }
}

/**
 * Prepares highlighted sentence text by wrapping matching search query tokens in <b> tags.
 * @param {string} text - Sentence text.
 * @param {string} searchQuery - Search query string.
 * @returns {string} Text with highlights.
 */
function buildHighlightedSentence(text, searchQuery) {
  if (!text || !searchQuery) return text || "";
  const tokens = searchQuery.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  const cleanTokens = [];

  for (let token of tokens) {
    token = token.replace(/^["']+|["']+$/g, "");
    if (token.startsWith("-")) continue;
    if (token) {
      cleanTokens.push(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    }
  }

  if (cleanTokens.length === 0) return text;
  const pattern = new RegExp(`(${cleanTokens.join("|")})`, "gi");
  const parts = text.split(/(<[a-zA-Z/](?:[^>"']|"[^"]*"|'[^']*')*>)/g);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      parts[i] = parts[i].replace(pattern, "<b>$1</b>");
    }
  }
  return parts.join("");
}

/**
 * Exports currently extracted sentence and media directly to Anki via AnkiConnect.
 * @param {HTMLElement} btn - Clicked trigger button.
 * @param {number|string|null} targetNoteId - Specific note ID to update.
 */
async function sendToAnki(btn, targetNoteId = null) {
  const ext = window.currentExtraction || {};
  if (!ext.id) {
    if (typeof showToast === "function") showToast("No sentence currently extracted.", "error");
    return;
  }

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<div class="animate-spin h-4 w-4 border-b-2 border-current rounded-full"></div><span>Sending...</span>`;
  btn.classList.add("opacity-70");

  try {
    const config = getActiveAnkiConfig();
    let resolvedNoteId = targetNoteId ? Number(targetNoteId) : null;

    // Resolve note ID if unspecified: get the newest note matching deck/noteType
    if (!resolvedNoteId) {
      const deck = config.deck || "";
      const noteType = config.noteType || "";

      if (!deck && !noteType) {
        throw new Error("Please specify 'Target Deck' or 'Target Note Type' in Anki Settings.");
      }

      const queryParts = [];
      if (deck) queryParts.push(`deck:"${deck}"`);
      if (noteType) queryParts.push(`note:"${noteType}"`);

      const notes = await ankiInvoke("findNotes", { query: queryParts.join(" ") });
      if (!Array.isArray(notes) || notes.length === 0) {
        throw new Error(`No notes found in deck "${deck}" with note type "${noteType}".`);
      }
      resolvedNoteId = Math.max(...notes);
    }

    // Determine current audio and image filenames/urls
    const audioUrl = ext.audioUrl || `/media/${ext.audioFilename}`;
    const imageUrl = ext.imageUrl || `/media/${ext.imageFilename}`;
    const audioFilename = ext.audioFilename || (audioUrl ? audioUrl.split("/").pop().split("?")[0] : "");
    const imageFilename = ext.imageFilename || (imageUrl ? imageUrl.split("/").pop().split("?")[0] : "");

    // Transfer media to AnkiConnect via base64 encoded data
    let storedAudioName = null;
    let storedImageName = null;

    if (config.audioField && audioUrl) {
      try {
        const audioBase64 = await fetchBlobAsBase64(audioUrl);
        storedAudioName = await ankiInvoke("storeMediaFile", {
          filename: audioFilename,
          data: audioBase64,
          deleteExisting: false,
        });
      } catch (mediaErr) {
        console.warn("Failed to store audio in AnkiConnect:", mediaErr);
      }
    }

    if (config.imageField && imageUrl) {
      try {
        const imageBase64 = await fetchBlobAsBase64(imageUrl);
        storedImageName = await ankiInvoke("storeMediaFile", {
          filename: imageFilename,
          data: imageBase64,
          deleteExisting: false,
        });
      } catch (mediaErr) {
        console.warn("Failed to store image in AnkiConnect:", mediaErr);
      }
    }

    // Prepare fields to update
    const fieldsToUpdate = {};
    const searchQuery = document.getElementById("searchInput")?.value.trim() || "";
    const rawText = ext.text || "";
    const highlightedText = buildHighlightedSentence(rawText, searchQuery);

    if (config.sentenceField) {
      fieldsToUpdate[config.sentenceField] = rawText;
    }
    if (config.sentenceHighlightedField) {
      fieldsToUpdate[config.sentenceHighlightedField] = highlightedText;
    }
    if (config.sourceField && ext.sourceInfo) {
      fieldsToUpdate[config.sourceField] = ext.sourceInfo;
    }
    if (config.audioField && (storedAudioName || audioFilename)) {
      fieldsToUpdate[config.audioField] = `[sound:${storedAudioName || audioFilename}]`;
    }
    if (config.imageField && (storedImageName || imageFilename)) {
      fieldsToUpdate[config.imageField] = `<img src="${storedImageName || imageFilename}">`;
    }

    // Update note fields
    await ankiInvoke("updateNoteFields", {
      note: {
        id: resolvedNoteId,
        fields: fieldsToUpdate,
      },
    });

    // Add tags if configured
    if (Array.isArray(config.tags) && config.tags.length > 0) {
      try {
        await ankiInvoke("addTags", {
          notes: [resolvedNoteId],
          tags: config.tags.join(" "),
        });
      } catch (tagErr) {
        console.warn("Failed to add tags to note:", tagErr);
      }
    }

    if (typeof showToast === "function") {
      showToast(`Successfully updated note ${resolvedNoteId} in Anki!`, "success");
    }
    if (targetNoteId && typeof toggleModalView === "function") {
      toggleModalView("mediaExtractView");
    }
  } catch (err) {
    console.error("Anki export error:", err);
    if (typeof showToast === "function") {
      showToast(err.message || "Failed to export to Anki.", "error");
    }
  } finally {
    btn.innerHTML = btn.dataset.origText || originalHtml;
    if (btn.dataset.origClass) {
      btn.className = btn.dataset.origClass;
    }
    delete btn.dataset.confirming;
    delete btn.dataset.origText;
    delete btn.dataset.origClass;
    if (btn.dataset.confirmTimer) {
      clearTimeout(Number(btn.dataset.confirmTimer));
      delete btn.dataset.confirmTimer;
    }
    btn.disabled = false;
    btn.classList.remove("opacity-70");
  }
}

/**
 * Toggles the views inside the media extraction modal.
 * @param {"mediaExtractView"|"mediaAnkiSearchView"} viewName
 */
function toggleModalView(viewName) {
  const extractView = document.getElementById("mediaExtractView");
  const searchView = document.getElementById("mediaAnkiSearchView");
  const backBtn = document.getElementById("mediaModalBackButton");
  const container = document.getElementById("mediaModalContentContainer");

  if (!extractView || !searchView || !backBtn) return;

  if (viewName === "mediaAnkiSearchView") {
    if (container) {
      container.style.minHeight = "min(60vh, 600px)";
    }
    extractView.classList.add("-translate-x-full");
    extractView.setAttribute("inert", "");
    searchView.classList.remove("invisible", "translate-x-full");
    searchView.removeAttribute("inert");
    backBtn.classList.remove("opacity-0", "pointer-events-none");
    backBtn.removeAttribute("tabindex");

    const mainQuery = document.getElementById("searchInput")?.value.trim() || "";
    const ankiSearchInput = document.getElementById("ankiCardSearchInput");
    if (ankiSearchInput) {
      ankiSearchInput.value = mainQuery;
      searchAnkiCards();
    }

    setTimeout(() => {
      if (!document.getElementById("mediaModal")?.classList.contains("hidden") && extractView.classList.contains("-translate-x-full")) {
        ankiSearchInput?.focus();
      }
    }, 300);
  } else {
    if (container) {
      container.style.minHeight = "";
    }
    extractView.classList.remove("-translate-x-full");
    extractView.removeAttribute("inert");
    searchView.classList.add("translate-x-full");
    searchView.setAttribute("inert", "");
    backBtn.classList.add("opacity-0", "pointer-events-none");
    backBtn.setAttribute("tabindex", "-1");
    setTimeout(() => {
      if (!extractView.classList.contains("-translate-x-full")) {
        searchView.classList.add("invisible");
        if (!document.getElementById("mediaModal")?.classList.contains("hidden")) {
          document.getElementById("btnReextract")?.focus();
        }
      }
    }, 300);
  }
}

/**
 * Opens the Anki Settings and Diagnostics modal.
 */
function openAnkiSettingsModal() {
  const modal = document.getElementById("ankiSettingsModal");
  if (!modal) return;

  const originLabel = document.getElementById("currentOriginLabel");
  if (originLabel && typeof window !== "undefined") {
    originLabel.textContent = window.location.origin;
  }

  // Populate input fields with active configuration
  const config = getActiveAnkiConfig();
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? "";
  };

  setVal("cfgAnkiUrl", config.ankiConnectUrl);
  setVal("cfgDeck", config.deck);
  setVal("cfgNoteType", config.noteType);
  setVal("cfgWordField", config.wordField);
  setVal("cfgDefField", config.definitionField);
  setVal("cfgSentenceField", config.sentenceField);
  setVal("cfgSentenceHighField", config.sentenceHighlightedField);
  setVal("cfgAudioField", config.audioField);
  setVal("cfgImageField", config.imageField);
  setVal("cfgSourceField", config.sourceField);
  setVal("cfgTags", Array.isArray(config.tags) ? config.tags.join(", ") : "");
  setVal("cfgPadStart", config.padStart ?? 0.25);
  setVal("cfgPadEnd", config.padEnd ?? 0.0);

  modal.classList.remove("hidden");
  if (typeof document !== "undefined" && document.body) {
    document.body.classList.add("overflow-hidden");
  }
  testAnkiConnectionUI();
}

/**
 * Closes the Anki Settings and Diagnostics modal.
 */
function closeAnkiSettingsModal() {
  const modal = document.getElementById("ankiSettingsModal");
  if (modal) modal.classList.add("hidden");
  if (typeof document !== "undefined" && document.body) {
    document.body.classList.remove("overflow-hidden");
  }
}

/**
 * Tests connection to AnkiConnect and renders diagnostics in the modal card.
 */
async function testAnkiConnectionUI() {
  const dot = document.getElementById("ankiDiagDot");
  const statusText = document.getElementById("ankiDiagStatus");
  const details = document.getElementById("ankiDiagDetails");
  const urlDisplay = document.getElementById("ankiDiagUrl");
  const testBtn = document.getElementById("btnTestAnki");
  const corsBox = document.getElementById("ankiCorsHelpBox");

  const urlInput = document.getElementById("cfgAnkiUrl");
  const testUrl = urlInput?.value.trim() || activeAnkiConfig.ankiConnectUrl || "http://127.0.0.1:8765";

  if (urlDisplay) urlDisplay.textContent = testUrl;
  if (statusText) statusText.textContent = "Pinging AnkiConnect...";
  if (dot) dot.className = "w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping";
  if (testBtn) testBtn.disabled = true;

  const startTime = performance.now();
  try {
    const version = await ankiInvoke("version", {}, 3000);
    const latency = Math.round(performance.now() - startTime);

    if (dot) dot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse";
    if (statusText) statusText.textContent = `Connected! AnkiConnect v${version} (${latency}ms)`;

    // Attempt to fetch deck and model counts
    let deckCount = 0;
    let modelCount = 0;
    try {
      const decks = await ankiInvoke("deckNames", {}, 2000);
      if (Array.isArray(decks)) deckCount = decks.length;
      const models = await ankiInvoke("modelNames", {}, 2000);
      if (Array.isArray(models)) modelCount = models.length;
    } catch (e) {
      // Introspection optional
    }

    if (details) {
      details.innerHTML = `Connected to <b>${escapeHtml(testUrl)}</b> &bull; Found ${deckCount} decks, ${modelCount} note types.`;
    }
    if (corsBox) corsBox.classList.add("hidden");
    updateAnkiStatusPill("connected", `v${version}`);
  } catch (err) {
    if (dot) dot.className = "w-2.5 h-2.5 rounded-full bg-rose-500";
    if (statusText) statusText.textContent = "Connection Failed";
    if (details) {
      details.innerHTML = `<span class="text-rose-500">${escapeHtml(err.message)}</span>`;
    }
    if (corsBox) corsBox.classList.remove("hidden");
    updateAnkiStatusPill("disconnected");
  } finally {
    if (testBtn) testBtn.disabled = false;
  }
}

/**
 * Saves configuration values entered in the settings modal.
 */
function saveAnkiSettingsFromModal() {
  const getVal = (id) => document.getElementById(id)?.value.trim() || "";

  const tagsRaw = getVal("cfgTags");
  const tags = tagsRaw
    ? tagsRaw
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
    : [];

  const rawPadStart = parseFloat(getVal("cfgPadStart"));
  const rawPadEnd = parseFloat(getVal("cfgPadEnd"));

  if (!Number.isFinite(rawPadStart) || rawPadStart < 0 || !Number.isFinite(rawPadEnd) || rawPadEnd < 0) {
    if (typeof showToast === "function") {
      showToast("Padding values must be valid non-negative numbers.", "error");
    }
    return;
  }

  const padStart = rawPadStart;
  const padEnd = rawPadEnd;

  const newConfig = {
    ankiConnectUrl: getVal("cfgAnkiUrl") || "http://127.0.0.1:8765",
    deck: getVal("cfgDeck"),
    noteType: getVal("cfgNoteType"),
    wordField: getVal("cfgWordField"),
    definitionField: getVal("cfgDefField"),
    sentenceField: getVal("cfgSentenceField"),
    sentenceHighlightedField: getVal("cfgSentenceHighField"),
    audioField: getVal("cfgAudioField"),
    imageField: getVal("cfgImageField"),
    sourceField: getVal("cfgSourceField"),
    tags: tags,
    padStart: padStart,
    padEnd: padEnd,
  };

  const padStartChanged = padStart !== activeAnkiConfig.padStart;
  const padEndChanged = padEnd !== activeAnkiConfig.padEnd;

  saveAnkiConfig(newConfig);

  // Sync with inline extraction padding controls on search screen only if changed in modal and not overridden by URL query
  const urlParams = new URLSearchParams(window.location.search);
  const padStartEl = document.getElementById("padStart");
  const padEndEl = document.getElementById("padEnd");
  if (padStartEl && !urlParams.has("padStart") && padStartChanged) {
    padStartEl.value = padStart;
  }
  if (padEndEl && !urlParams.has("padEnd") && padEndChanged) {
    padEndEl.value = padEnd;
  }

  closeAnkiSettingsModal();
  if (typeof showToast === "function") {
    showToast("Settings saved successfully!", "success");
  }
  checkAnkiConnection();
}

/**
 * Resets modal fields to config.json defaults and clears localStorage.
 */
function resetAnkiSettingsInModal() {
  resetAnkiConfig();
  const urlParams = new URLSearchParams(window.location.search);
  const padStartEl = document.getElementById("padStart");
  const padEndEl = document.getElementById("padEnd");
  if (padStartEl && !urlParams.has("padStart")) {
    padStartEl.value = activeAnkiConfig.padStart ?? 0.25;
  }
  if (padEndEl && !urlParams.has("padEnd")) {
    padEndEl.value = activeAnkiConfig.padEnd ?? 0.0;
  }

  openAnkiSettingsModal();
  if (typeof showToast === "function") {
    showToast("Reset settings to default values.", "info");
  }
}

/**
 * Copies the AnkiConnect CORS snippet to the user's clipboard.
 * @param {HTMLElement} btn - Clicked copy button.
 */
async function copyCorsSnippet(btn) {
  const snippet = document.getElementById("corsConfigSnippet")?.textContent || "";
  try {
    await navigator.clipboard.writeText(snippet);
    const orig = btn.textContent;
    btn.textContent = "Copied! ✓";
    btn.classList.add("bg-emerald-600");
    setTimeout(() => {
      btn.textContent = orig;
      btn.classList.remove("bg-emerald-600");
    }, 2000);
  } catch (err) {
    if (typeof showToast === "function") showToast("Failed to copy to clipboard", "error");
  }
}

// Initial boot
if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", async () => {
    await loadAnkiConfig();
    await checkAnkiConnection();

    // Initialize inline padding inputs from config if they haven't been customized via URL query params
    const urlParams = new URLSearchParams(window.location.search);
    const padStartEl = document.getElementById("padStart");
    const padEndEl = document.getElementById("padEnd");
    if (padStartEl && !urlParams.has("padStart") && activeAnkiConfig.padStart !== undefined) {
      padStartEl.value = activeAnkiConfig.padStart;
    }
    if (padEndEl && !urlParams.has("padEnd") && activeAnkiConfig.padEnd !== undefined) {
      padEndEl.value = activeAnkiConfig.padEnd;
    }

    document.getElementById("ankiCardSearchInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        toggleModalView("mediaExtractView");
        e.stopPropagation();
      }
    });
  });

  // Global exports for inline HTML event handlers
  window.ankiInvoke = ankiInvoke;
  window.checkAnkiConnection = checkAnkiConnection;
  window.sendToAnki = sendToAnki;
  window.searchAnkiCards = searchAnkiCards;
  window.debounceSearchAnkiCards = debounceSearchAnkiCards;
  window.toggleModalView = toggleModalView;
  window.openAnkiSettingsModal = openAnkiSettingsModal;
  window.closeAnkiSettingsModal = closeAnkiSettingsModal;
  window.testAnkiConnectionUI = testAnkiConnectionUI;
  window.saveAnkiSettingsFromModal = saveAnkiSettingsFromModal;
  window.resetAnkiSettingsInModal = resetAnkiSettingsInModal;
  window.copyCorsSnippet = copyCorsSnippet;
}

// Node.js module exports for unit testing
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DEFAULT_ANKI_CONFIG,
    getActiveAnkiConfig,
    loadAnkiConfig,
    saveAnkiConfig,
    resetAnkiConfig,
    ankiInvoke,
    checkAnkiConnection,
    fetchBlobAsBase64,
    buildAnkiSearchQueries,
    stripHtml,
    buildHighlightedSentence,
  };
}
