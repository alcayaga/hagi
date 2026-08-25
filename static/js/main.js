document
  .getElementById("searchInput")
  .addEventListener("keypress", function (e) {
    if (e.key === "Enter") performSearch();
  });

// Store all results locally so we can filter them on the client side
let allSearchResults = [];

const LANG_COLORS = {
  jpn: {
    solid: "bg-indigo-600",
    light: "bg-indigo-500",
    badge: "bg-indigo-600 text-white shadow-sm",
  },
  eng: {
    solid: "bg-emerald-600",
    light: "bg-emerald-500",
    badge: "bg-emerald-600 text-white shadow-sm",
  },
  spa: {
    solid: "bg-amber-500",
    light: "bg-amber-400",
    badge: "bg-amber-500 text-white shadow-sm",
  },
};

/**
 * Returns styling classes for language-specific badges and timeline blocks.
 * @param {string} lang - The language code (jpn, eng, spa).
 * @returns {Object} An object containing Tailwind classes for solid, light, and badge styles.
 */
function getLangColors(lang) {
  return (
    LANG_COLORS[lang?.toLowerCase()] || {
      solid: "bg-gray-600",
      light: "bg-gray-500",
      badge: "bg-gray-500 text-white shadow-sm",
    }
  );
}

/**
 * Dynamically updates the episode dropdown based on the currently selected show,
 * then triggers a re-render of the search results table.
 */
function updateEpisodesAndRender() {
  const showFilter = document.getElementById("filterShow").value;
  const epDropdown = document.getElementById("filterEpisode");
  const currentEp = epDropdown.value;

  // Rebuild episodes dropdown based on the selected show
  let availableEps = new Set();
  allSearchResults.forEach((r) => {
    const showName = r.show_title || r.path.split("/").pop();
    if ((!showFilter || showName === showFilter) && r.episode !== null) {
      availableEps.add(r.episode);
    }
  });

  const sortedEps = [...availableEps].sort((a, b) => a - b);

  epDropdown.innerHTML = '<option value="">All Eps</option>';
  sortedEps.forEach((ep) => {
    const option = document.createElement("option");
    option.value = ep;
    option.innerText = `Ep ${ep}`;
    epDropdown.appendChild(option);
  });

  // Restore previous selection if it's still valid
  if (sortedEps.includes(parseInt(currentEp))) {
    epDropdown.value = currentEp;
  }

  renderResults();
}

/**
 * Fetches all search results matching the query string from the backend API.
 * Populates the local results cache and updates the UI filters.
 */
async function performSearch() {
  const query = document.getElementById("searchInput").value;
  const loading = document.getElementById("loading");
  const tbody = document.getElementById("resultsBody");

  if (!query.trim()) return;

  loading.classList.remove("hidden");
  tbody.innerHTML = "";

  // Reset filters on a new search
  document.getElementById("filterShow").innerHTML =
    '<option value="">All Shows</option>';
  document.getElementById("filterEpisode").innerHTML =
    '<option value="">All Eps</option>';

  try {
    // Fetch ALL results matching the keyword
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    allSearchResults = await response.json();

    // Extract unique shows to populate the dropdown
    const uniqueShows = [
      ...new Set(
        allSearchResults.map((r) => r.show_title || r.path.split("/").pop()),
      ),
    ];
    uniqueShows.sort();

    const showDropdown = document.getElementById("filterShow");
    uniqueShows.forEach((show) => {
      const option = document.createElement("option");
      option.value = show;
      option.innerText = show;
      showDropdown.appendChild(option);
    });

    // Update episodes dropdown and render the table
    updateEpisodesAndRender();

    // Show filters now that we have results
    document.getElementById("filterContainer").classList.remove("hidden");
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-6 py-4 text-center text-red-500">Error fetching results: ${error}</td></tr>`;
  } finally {
    loading.classList.add("hidden");
  }
}

/**
 * Applies the currently selected Show and Episode filters to the local results cache
 * and dynamically generates the HTML for the results table.
 */
function renderResults() {
  const tbody = document.getElementById("resultsBody");
  const showFilter = document.getElementById("filterShow").value;
  const epFilter = document.getElementById("filterEpisode").value;

  tbody.innerHTML = "";

  // Apply client-side filters
  let filtered = allSearchResults;
  if (showFilter) {
    filtered = filtered.filter(
      (r) => (r.show_title || r.path.split("/").pop()) === showFilter,
    );
  }
  if (epFilter) {
    filtered = filtered.filter((r) => r.episode == epFilter);
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-6 py-4 text-center text-gray-500 dark:text-gray-400">No results found matching your filters</td></tr>`;
    return;
  }

  const rawQuery = document.getElementById("searchInput").value;
  const searchTermsTokens =
    rawQuery.match(/(".*?"|[^"\s]+)+(?=\s*|\s*$)/g) || [];
  const validTerms = searchTermsTokens
    .filter((t) => !t.startsWith("-"))
    .map((t) => t.replace(/(^"|"$)/g, ""))
    .filter((t) => t.trim().length > 0)
    .sort((a, b) => b.length - a.length);

  let highlightRegex = null;
  if (validTerms.length > 0) {
    highlightRegex = new RegExp(
      `(${validTerms.map((t) => t.replace(/[-\\/\\\\^$*+?.()|[\\]{}]/g, "\\\\$&")).join("|")})`,
      "gi",
    );
  }

  function highlightText(text) {
    if (!text) return "";
    // Sanitize text first to prevent HTML injection from search results
    const div = document.createElement("div");
    div.innerText = text;
    let sanitized = div.innerHTML;

    if (highlightRegex) {
      sanitized = sanitized.replace(
        highlightRegex,
        `<mark class="bg-yellow-200 dark:bg-yellow-900 text-inherit rounded px-0.5">$1</mark>`,
      );
    }
    return sanitized;
  }

  filtered.forEach((r) => {
    const m = Math.floor(r.start_time / 60)
      .toString()
      .padStart(2, "0");
    const s = Math.floor(r.start_time % 60)
      .toString()
      .padStart(2, "0");
    const timeStr = `${m}:${s}`;

    let sourceDisplay = r.path.split("/").pop();
    let fullTitle = sourceDisplay;
    let episodeTitleHtml = "";

    if (r.show_title) {
      sourceDisplay = `${r.show_title}`;
      if (r.season !== null && r.episode !== null) {
        sourceDisplay += ` - S${r.season.toString().padStart(2, "0")}E${r.episode.toString().padStart(2, "0")}`;
      } else if (r.episode !== null) {
        sourceDisplay += ` - Ep ${r.episode}`;
      }
      fullTitle = sourceDisplay;
      if (r.episode_title) {
        fullTitle += ` "${r.episode_title}"`;
        episodeTitleHtml = `<div class="mt-1 text-gray-500 italic">${r.episode_title}</div>`;
      }
      fullTitle = fullTitle.replace(/"/g, "&quot;");
    }

    const cleanText = r.text ? r.text.replace(/\n/g, " ") : "";
    const cleanSpa = r.spa_translation
      ? r.spa_translation.replace(/\n/g, " ")
      : "";
    const cleanEng = r.eng_translation
      ? r.eng_translation.replace(/\n/g, " ")
      : "";

    const row = document.createElement("tr");
    row.className =
      "flex flex-col md:table-row border-b dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors p-2 md:p-0";
    row.innerHTML = `
                    <td class="block md:table-cell px-2 py-2 md:px-6 md:py-4">
                        <div class="text-lg font-medium">${highlightText(cleanText)}</div>
                        ${cleanSpa ? `<div class="text-sm text-gray-500 dark:text-gray-400 mt-1 leading-snug"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("spa").badge} mr-2 align-middle">SPA</span> <span>${highlightText(cleanSpa)}</span></div>` : ""}
                        ${cleanEng ? `<div class="text-sm text-gray-500 dark:text-gray-400 mt-1 leading-snug"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("eng").badge} mr-2 align-middle">ENG</span> <span>${highlightText(cleanEng)}</span></div>` : ""}
                    </td>
                    <td class="block md:table-cell px-2 py-1 md:px-6 md:py-4 text-xs text-gray-400 md:max-w-xs whitespace-normal break-words" title="${fullTitle}">
                        <span class="inline-block md:hidden font-bold mr-1 text-gray-500">Source:</span>
                        <span class="font-medium text-gray-600 dark:text-gray-300">${sourceDisplay}</span>
                        ${episodeTitleHtml ? `<span class="block md:mt-1 text-gray-500 italic">${r.episode_title}</span>` : ""}
                    </td>
                    <td class="block md:table-cell px-2 py-1 md:px-6 md:py-4 text-sm text-gray-500 dark:text-gray-400">
                        <span class="inline-block md:hidden font-bold mr-1">Time:</span>${timeStr}
                    </td>
                    <td class="block md:table-cell px-2 py-3 md:px-6 md:py-4">
                        <div class="flex flex-row md:flex-col items-center justify-start md:justify-center space-x-2 md:space-x-0 md:space-y-2">
                            <button onclick="viewContext(${r.id})" class="flex-1 md:flex-none w-full md:w-20 bg-gray-100 text-gray-700 dark:bg-gray-600 dark:text-gray-200 px-3 py-2 md:py-1 rounded hover:bg-gray-200 dark:hover:bg-gray-500 font-semibold text-sm transition shadow-sm">Context</button>
                            <button onclick="extractMedia(${r.id}, this)" class="flex-1 md:flex-none w-full md:w-20 bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200 px-3 py-2 md:py-1 rounded hover:bg-indigo-200 dark:hover:bg-indigo-800 font-semibold text-sm transition shadow-sm">Extract</button>
                        </div>
                    </td>
                `;
    tbody.appendChild(row);
  });
}

let currentExtraction = { id: null, padStart: 0.5, padEnd: 0.5 };

/**
 * Calls the backend API to extract audio and snapshot images for a specific sentence.
 * Displays the extracted media in a modal.
 *
 * @param {number} id - The ID of the sentence to extract.
 * @param {HTMLElement} btnElement - The button element that triggered the extraction.
 */
async function extractMedia(id, btnElement) {
  const padStart = parseFloat(document.getElementById("padStart").value) || 0.5;
  const padEnd = parseFloat(document.getElementById("padEnd").value) || 0.5;

  currentExtraction.id = id;
  currentExtraction.padStart = padStart;
  currentExtraction.padEnd = padEnd;

  const originalText = btnElement.innerText;

  const r = allSearchResults.find((x) => x.id === id);
  if (r) {
    let line1 = r.show_title || r.path.split("/").pop();
    if (r.season !== null && r.episode !== null) {
      line1 += ` - S${r.season.toString().padStart(2, "0")}E${r.episode.toString().padStart(2, "0")}`;
    } else if (r.episode !== null) {
      line1 += ` - EP ${r.episode}`;
    }

    let line2 = "";
    if (r.episode_title) {
      line2 += `"${r.episode_title}" `;
    }
    const m = Math.floor(r.start_time / 60)
      .toString()
      .padStart(2, "0");
    const s = Math.floor(r.start_time % 60)
      .toString()
      .padStart(2, "0");
    line2 += `[${m}:${s}]`;

    document.getElementById("mediaMetadata").innerHTML =
      `<span>${line1}</span><span class="text-sm ml-3 text-gray-500 dark:text-gray-400 font-normal">${line2}</span>`;
  }
  btnElement.innerText = "Wait...";
  btnElement.disabled = true;
  btnElement.classList.add("opacity-50");

  try {
    const response = await fetch(`/api/extract/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pad_start: padStart, pad_end: padEnd }),
    });
    const data = await response.json();

    if (data.success) {
      document.getElementById("mediaText").innerHTML =
        `` + (data.text || "").replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ");

      const cleanSpa = r.spa_translation
        ? r.spa_translation.replace(/\n/g, " ")
        : "";
      const cleanEng = r.eng_translation
        ? r.eng_translation.replace(/\n/g, " ")
        : "";
      let transHtml = "";
      if (cleanSpa)
        transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("spa").badge} mr-2 align-middle">SPA</span><span class="text-gray-600 dark:text-gray-300 align-middle">${cleanSpa}</span></div>`;
      if (cleanEng)
        transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("eng").badge} mr-2 align-middle">ENG</span><span class="text-gray-600 dark:text-gray-300 align-middle">${cleanEng}</span></div>`;
      document.getElementById("mediaTranslations").innerHTML = transHtml;

      document.getElementById("mediaImage").src =
        data.image_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").src =
        data.audio_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").play();
      document.getElementById("mediaModal").classList.remove("hidden");

      openExtractionTimeline(id);
    } else {
      alert("Extraction failed: " + data.detail);
    }
  } catch (error) {
    alert("Error calling extraction API: " + error);
  } finally {
    btnElement.innerText = originalText;
    btnElement.disabled = false;
    btnElement.classList.remove("opacity-50");
  }
}

/**
 * Fetches the surrounding subtitle context for a specific sentence.
 * If the target sentence is not Japanese, it fetches both the target language
 * and the Japanese track, rendering them side-by-side.
 *
 * @param {number} id - The ID of the target sentence.
 */
async function viewContext(id) {
  document.getElementById("contextModal").classList.remove("hidden");
  const list = document.getElementById("contextList");
  const loading = document.getElementById("contextLoading");

  list.innerHTML = "";
  loading.classList.remove("hidden");

  try {
    const response = await fetch(`/api/context/${id}`);
    const data = await response.json();

    if (data.target_context.length === 0) {
      list.innerHTML = `<p class="text-gray-500">No context available.</p>`;
    } else {
      const hasSecondary =
        data.secondary_context && data.secondary_context.length > 0;

      if (hasSecondary) {
        // Find target match
        const targetMatchIndex = data.target_context.findIndex(
          (r) => r.id === id,
        );
        const targetMatch =
          targetMatchIndex !== -1
            ? data.target_context[targetMatchIndex]
            : data.target_context[0];

        const targetBefore =
          targetMatchIndex !== -1
            ? data.target_context.slice(0, targetMatchIndex)
            : [];
        const targetAfter =
          targetMatchIndex !== -1
            ? data.target_context.slice(targetMatchIndex + 1)
            : [];

        // Find secondary match (closest start_time within 5 seconds to match search behavior)
        let secondaryMatchIndex = -1;
        let minDiff = 5.0;
        if (targetMatch) {
          data.secondary_context.forEach((r, idx) => {
            const diff = Math.abs(r.start_time - targetMatch.start_time);
            if (diff < minDiff) {
              minDiff = diff;
              secondaryMatchIndex = idx;
            }
          });
        }

        let secondaryBefore = [];
        let secondaryMatchObj = null;
        let secondaryAfter = [];
        if (secondaryMatchIndex !== -1) {
          secondaryMatchObj = data.secondary_context[secondaryMatchIndex];
          secondaryBefore = data.secondary_context.slice(
            0,
            secondaryMatchIndex,
          );
          secondaryAfter = data.secondary_context.slice(
            secondaryMatchIndex + 1,
          );
        } else {
          secondaryBefore = data.secondary_context;
        }

        const createSentenceDiv = (r, isHighlight) => {
          if (!r) return document.createElement("div");
          const m = Math.floor(r.start_time / 60)
            .toString()
            .padStart(2, "0");
          const s = Math.floor(r.start_time % 60)
            .toString()
            .padStart(2, "0");
          const timeStr = `${m}:${s}`;

          const bgClass = isHighlight
            ? "bg-indigo-50 dark:bg-indigo-900 border-indigo-200 dark:border-indigo-700 shadow-inner"
            : "bg-gray-50 dark:bg-gray-700 border-transparent";

          const div = document.createElement("div");
          div.className = `p-2 rounded border ${bgClass}`;
          div.innerHTML = `
                <span class="text-xs text-gray-500 dark:text-gray-400 font-mono block mb-1">[${timeStr}]</span>
                <span class="text-sm ${isHighlight ? "font-bold text-indigo-700 dark:text-indigo-300" : ""}">${(r.text || "").replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ")}</span>
            `;
          return div;
        };

        const renderBlock = (targets, secondaries, justifyClass) => {
          if (targets.length === 0 && secondaries.length === 0) return;
          const row = document.createElement("div");
          row.className = "grid grid-cols-2 gap-2 md:gap-4 mb-2";

          const colTarget = document.createElement("div");
          colTarget.className = `flex flex-col gap-2 border-r dark:border-gray-700 pr-2 md:pr-4 ${justifyClass}`;
          targets.forEach((r) =>
            colTarget.appendChild(createSentenceDiv(r, false)),
          );

          const colJpn = document.createElement("div");
          colJpn.className = `flex flex-col gap-2 pl-2 ${justifyClass}`;
          secondaries.forEach((r) =>
            colJpn.appendChild(createSentenceDiv(r, false)),
          );

          row.appendChild(colTarget);
          row.appendChild(colJpn);
          list.appendChild(row);
        };

        // Header
        const headerRow = document.createElement("div");
        headerRow.className = "grid grid-cols-2 gap-2 md:gap-4 mb-2";
        headerRow.innerHTML = `
            <div class="border-r dark:border-gray-700 pr-2 md:pr-4">
                <h4 class="font-bold text-gray-400 uppercase text-xs">${data.target_lang} Track</h4>
            </div>
            <div class="pl-2">
                <h4 class="font-bold text-gray-400 uppercase text-xs">${data.secondary_lang.toUpperCase()} Track</h4>
            </div>
        `;
        list.appendChild(headerRow);

        // Before
        renderBlock(targetBefore, secondaryBefore, "justify-end");

        // Match
        const matchRow = document.createElement("div");
        matchRow.className = "grid grid-cols-2 gap-2 md:gap-4 mb-2";

        const colMatchTarget = document.createElement("div");
        colMatchTarget.className =
          "flex flex-col gap-2 border-r dark:border-gray-700 pr-2 md:pr-4";
        if (targetMatch) {
          const div = createSentenceDiv(targetMatch, true);
          colMatchTarget.appendChild(div);
          setTimeout(
            () => div.scrollIntoView({ behavior: "smooth", block: "center" }),
            100,
          );
        }

        const colMatchSecondary = document.createElement("div");
        colMatchSecondary.className = "flex flex-col gap-2 pl-2";
        if (secondaryMatchObj) {
          const div = createSentenceDiv(secondaryMatchObj, true);
          colMatchSecondary.appendChild(div);
        }

        matchRow.appendChild(colMatchTarget);
        matchRow.appendChild(colMatchSecondary);
        list.appendChild(matchRow);

        // After
        renderBlock(targetAfter, secondaryAfter, "justify-start");
      } else {
        data.target_context.forEach((r) => {
          const m = Math.floor(r.start_time / 60)
            .toString()
            .padStart(2, "0");
          const s = Math.floor(r.start_time % 60)
            .toString()
            .padStart(2, "0");
          const timeStr = `${m}:${s}`;

          const isTarget = r.id === id;
          const bgClass = isTarget
            ? "bg-indigo-50 dark:bg-indigo-900 border-indigo-200 dark:border-indigo-700 shadow-inner"
            : "bg-gray-50 dark:bg-gray-700 border-transparent";

          const div = document.createElement("div");
          div.className = `p-3 rounded border ${bgClass}`;
          div.innerHTML = `
                                <span class="text-sm text-gray-500 dark:text-gray-400 font-mono mr-2">[${timeStr}]</span>
                                <span class="${isTarget ? "font-bold text-indigo-700 dark:text-indigo-300" : ""}">${(r.text || "").replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ")}</span>
                            `;
          list.appendChild(div);

          if (isTarget) {
            setTimeout(
              () => div.scrollIntoView({ behavior: "smooth", block: "center" }),
              100,
            );
          }
        });
      }
    }
  } catch (error) {
    list.innerHTML = `<p class="text-red-500">Error fetching context: ${error}</p>`;
  } finally {
    loading.classList.add("hidden");
  }
}

/**
 * Closes a specified modal and optionally pauses its associated audio playback.
 *
 * @param {string} modalId - The HTML ID of the modal to close.
 * @param {string|null} audioId - The HTML ID of the audio element to pause (if any).
 */
function closeModal(modalId, audioId = null) {
  document.getElementById(modalId).classList.add("hidden");
  if (audioId) {
    document.getElementById(audioId).pause();
  }
}

/**
 * Copies the requested item to the clipboard.
 * Matches Nadeshiko upstream behavior: copies the absolute URL for media, and plain text for phrases.
 * @param {string} type - 'image', 'audio', or 'text'
 * @param {HTMLElement} btn - The button element that was clicked
 */
async function copyExtractItem(type, btn) {
  let content = "";
  if (type === "image") {
    const imgElement = document.getElementById("mediaImage");
    if (imgElement && imgElement.src) {
      // imgElement.src returns the absolute URL, but it has a ?t= timestamp query parameter
      // We strip the query parameter so Anki add-ons can fetch it cleanly
      const url = new URL(imgElement.src);
      content = url.origin + url.pathname;
    }
  } else if (type === "audio") {
    const audioElement = document.getElementById("mediaAudio");
    if (audioElement && audioElement.src) {
      const url = new URL(audioElement.src);
      content = url.origin + url.pathname;
    }
  } else if (type === "text") {
    const textElement = document.getElementById("mediaText");
    if (textElement) {
      content = textElement.innerText;
    }
  }

  if (!content) return;

  const span = btn.querySelector("span");
  const originalText = span.innerText;

  try {
    await navigator.clipboard.writeText(content);
    span.innerText = "Copied!";
  } catch (err) {
    console.error("Failed to copy: ", err);
    span.innerText = "Failed";
  }
  setTimeout(() => (span.innerText = originalText), 2000);
}

let timelineData = {
  target: null,
  windowStart: 0,
  windowEnd: 0,
  duration: 0,
  selectedStart: 0,
  selectedEnd: 0,
  isDragging: false,
  activeHandle: null,
  contextData: null,
};

async function openExtractionTimeline(id) {
  const timelineContainer = document.getElementById("timelineContainer");
  timelineContainer.innerHTML =
    '<div class="absolute inset-0 flex justify-center items-center"><div class="animate-spin rounded-full h-5 w-5 border-b-2 border-indigo-600"></div></div>';

  const response = await fetch(`/api/context/${id}`);
  const contextData = await response.json();

  let targetSentence = contextData.target_context.find((s) => s.id === id);
  if (!targetSentence) targetSentence = contextData.target_context[0];

  const targetStart = targetSentence.start_time || 0;
  const targetEnd = targetSentence.end_time || targetStart + 2.0;

  timelineData.windowStart = Math.max(0, targetStart - 10.0);
  timelineData.windowEnd = targetEnd + 10.0;
  timelineData.duration = timelineData.windowEnd - timelineData.windowStart;
  timelineData.contextData = contextData;

  timelineData.target = targetSentence;
  timelineData.selectedStart = Math.max(
    0,
    targetStart - currentExtraction.padStart,
  );
  timelineData.selectedEnd = targetEnd + currentExtraction.padEnd;

  renderTimeline(contextData);
}

/**
 * Renders the custom visual timeline with dual-track language blocks.
 * @param {Object} contextData - Context data containing target and secondary subtitle arrays.
 */
function renderTimeline(contextData) {
  const container = document.getElementById("timelineContainer");
  container.innerHTML = "";

  const legend = document.getElementById("timelineLegend");
  if (legend) {
    legend.innerHTML = "";
    if (contextData.target_lang) {
      const c = getLangColors(contextData.target_lang);
      legend.innerHTML += `<div class="flex items-center gap-1"><div class="w-3 h-3 ${c.solid} rounded-[2px] opacity-80"></div> ${contextData.target_lang.toUpperCase()}</div>`;
    }
    if (contextData.secondary_lang) {
      const c = getLangColors(contextData.secondary_lang);
      legend.innerHTML += `<div class="flex items-center gap-1"><div class="w-3 h-3 ${c.solid} rounded-[2px] opacity-80"></div> ${contextData.secondary_lang.toUpperCase()}</div>`;
    }
  }

  const grid = document.createElement("div");
  grid.className =
    "absolute inset-0 pointer-events-none flex flex-col justify-between opacity-20";
  for (let i = 0; i <= 10; i++) {
    const tick = document.createElement("div");
    tick.className = "absolute top-0 bottom-0 border-l border-gray-500";
    tick.style.left = `${(i / 10) * 100}%`;
    grid.appendChild(tick);
  }
  container.appendChild(grid);

  const marks = document.createElement("div");
  marks.className = "absolute inset-0";

  if (contextData.secondary_context) {
    contextData.secondary_context.forEach((s) => {
      if (!s.start_time) return;
      const sStart = s.start_time;
      const sEnd = s.end_time || sStart + 2.0;
      if (sEnd < timelineData.windowStart || sStart > timelineData.windowEnd)
        return;
      const leftPct = Math.max(
        0,
        ((sStart - timelineData.windowStart) / timelineData.duration) * 100,
      );
      const rightPct = Math.max(
        0,
        ((timelineData.windowEnd - sEnd) / timelineData.duration) * 100,
      );

      const c = getLangColors(s.language || contextData.secondary_lang);
      const block = document.createElement("div");
      block.className = `absolute bottom-0 h-1/2 ${c.solid} bg-opacity-70 border border-white border-opacity-20 rounded-sm truncate text-[9px] text-white px-1 leading-tight select-none cursor-pointer hover:bg-opacity-100 transition-opacity`;
      block.style.left = `${leftPct}%`;
      block.style.right = `${rightPct}%`;
      block.textContent = (s.text || "")
        .replace(/<br\s*\/?>/gi, " ")
        .replace(/\n/g, " ")
        .replace(/(<([^>]+)>)/gi, "");

      const tooltip = document.getElementById("timelineTooltip");
      block.addEventListener("mouseenter", () => {
        tooltip.textContent = (s.text || "")
          .replace(/<br\s*\/?>/gi, " ")
          .replace(/\n/g, " ")
          .replace(/(<([^>]+)>)/gi, "");
        tooltip.classList.remove("hidden");
      });
      block.addEventListener("mousemove", (e) => {
        tooltip.style.left = e.clientX + "px";
        tooltip.style.top = e.clientY + "px";
      });
      block.addEventListener("mouseleave", () => {
        tooltip.classList.add("hidden");
      });
      marks.appendChild(block);
    });
  }

  if (contextData.target_context) {
    contextData.target_context.forEach((s) => {
      if (!s.start_time) return;
      const sStart = s.start_time;
      const sEnd = s.end_time || sStart + 2.0;
      if (sEnd < timelineData.windowStart || sStart > timelineData.windowEnd)
        return;

      const leftPct = Math.max(
        0,
        ((sStart - timelineData.windowStart) / timelineData.duration) * 100,
      );
      const rightPct = Math.max(
        0,
        ((timelineData.windowEnd - sEnd) / timelineData.duration) * 100,
      );

      const c = getLangColors(s.language || contextData.target_lang);
      const block = document.createElement("div");
      block.className = `absolute top-0 h-1/2 ${c.solid} bg-opacity-70 border border-white border-opacity-20 rounded-sm truncate text-[9px] text-white px-1 leading-tight select-none cursor-pointer hover:bg-opacity-100 transition-opacity`;
      if (s.id === timelineData.target.id) {
        block.classList.remove("bg-opacity-70");
        block.classList.add("bg-opacity-100", "font-bold");
      }
      block.style.left = `${leftPct}%`;
      block.style.right = `${rightPct}%`;
      block.textContent = (s.text || "")
        .replace(/<br\s*\/?>/gi, " ")
        .replace(/\n/g, " ")
        .replace(/(<([^>]+)>)/gi, "");

      const tooltip = document.getElementById("timelineTooltip");
      block.addEventListener("mouseenter", () => {
        tooltip.textContent = (s.text || "")
          .replace(/<br\s*\/?>/gi, " ")
          .replace(/\n/g, " ")
          .replace(/(<([^>]+)>)/gi, "");
        tooltip.classList.remove("hidden");
      });
      block.addEventListener("mousemove", (e) => {
        tooltip.style.left = e.clientX + "px";
        tooltip.style.top = e.clientY + "px";
      });
      block.addEventListener("mouseleave", () => {
        tooltip.classList.add("hidden");
      });
      marks.appendChild(block);
    });
  }
  container.appendChild(marks);

  const selected = document.createElement("div");
  selected.id = "timelineSelected";
  selected.className =
    "absolute h-full bg-indigo-500 bg-opacity-20 border-l-2 border-r-2 border-indigo-600 group pointer-events-none";

  const hStart = document.createElement("div");
  hStart.id = "handleStart";
  hStart.className =
    "absolute top-0 bottom-0 left-0 w-6 -ml-3 cursor-ew-resize flex justify-center items-center pointer-events-auto group-hover:bg-black group-hover:bg-opacity-10";
  hStart.innerHTML = '<div class="w-1 h-6 bg-indigo-600 rounded"></div>';

  const hEnd = document.createElement("div");
  hEnd.id = "handleEnd";
  hEnd.className =
    "absolute top-0 bottom-0 right-0 w-6 -mr-3 cursor-ew-resize flex justify-center items-center pointer-events-auto group-hover:bg-black group-hover:bg-opacity-10";
  hEnd.innerHTML = '<div class="w-1 h-6 bg-indigo-600 rounded"></div>';

  selected.appendChild(hStart);
  selected.appendChild(hEnd);
  container.appendChild(selected);

  updateTimelineSelection();
  attachTimelineEvents(container, hStart, hEnd);
}

/**
 * Visually updates the position of the left and right selection handles on the timeline.
 */
function updateTimelineSelection() {
  const selected = document.getElementById("timelineSelected");
  if (!selected) return;

  const leftPct = Math.max(
    0,
    ((timelineData.selectedStart - timelineData.windowStart) /
      timelineData.duration) *
      100,
  );
  const rightPct = Math.max(
    0,
    ((timelineData.windowEnd - timelineData.selectedEnd) /
      timelineData.duration) *
      100,
  );

  selected.style.left = `${leftPct}%`;
  selected.style.right = `${rightPct}%`;

  document.getElementById("timelineDurationDisplay").innerText =
    `${(timelineData.selectedEnd - timelineData.selectedStart).toFixed(2)}s`;
}

/**
 * Binds mouse and touch events to the timeline selection handles to allow dragging.
 * @param {HTMLElement} container - The timeline container element.
 * @param {HTMLElement} hStart - The starting (left) drag handle.
 * @param {HTMLElement} hEnd - The ending (right) drag handle.
 */
function attachTimelineEvents(container, hStart, hEnd) {
  const getPos = (e) => {
    const rect = container.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    let pct = (clientX - rect.left) / rect.width;
    pct = Math.max(0, Math.min(1, pct));
    return timelineData.windowStart + pct * timelineData.duration;
  };

  const onMove = (e) => {
    if (!timelineData.isDragging) return;
    const time = getPos(e);

    if (timelineData.activeHandle === "start") {
      timelineData.selectedStart = Math.min(
        time,
        timelineData.selectedEnd - 0.5,
      );
    } else if (timelineData.activeHandle === "end") {
      timelineData.selectedEnd = Math.max(
        time,
        timelineData.selectedStart + 0.5,
      );
    }
    updateTimelineSelection();
  };

  const onEnd = () => {
    timelineData.isDragging = false;
    timelineData.activeHandle = null;
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onEnd);
    document.removeEventListener("touchmove", onMove);
    document.removeEventListener("touchend", onEnd);
  };

  const onStartDrag = (handleType) => (e) => {
    e.preventDefault();
    timelineData.isDragging = true;
    timelineData.activeHandle = handleType;

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onEnd);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd);
  };

  hStart.addEventListener("mousedown", onStartDrag("start"));
  hStart.addEventListener("touchstart", onStartDrag("start"), {
    passive: false,
  });

  hEnd.addEventListener("mousedown", onStartDrag("end"));
  hEnd.addEventListener("touchstart", onStartDrag("end"), { passive: false });
}

/**
 * Sends the newly selected timeline range to the backend API to extract
 * a perfectly trimmed audio clip and updates the UI with the enclosed subtitle text.
 */
async function applyTimelineExtraction() {
  const targetStart = timelineData.target.start_time || 0;
  const targetEnd = timelineData.target.end_time || targetStart + 2.0;

  currentExtraction.padStart = Math.max(
    0,
    targetStart - timelineData.selectedStart,
  );
  currentExtraction.padEnd = Math.max(0, timelineData.selectedEnd - targetEnd);

  document.getElementById("mediaModalLoading").classList.remove("hidden");
  const btn = document.getElementById("btnReextract");
  btn.disabled = true;
  btn.innerText = "Extracting...";

  try {
    const response = await fetch(`/api/extract/${currentExtraction.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pad_start: currentExtraction.padStart,
        pad_end: currentExtraction.padEnd,
      }),
    });
    const data = await response.json();

    if (data.success) {
      document.getElementById("mediaImage").src =
        data.image_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").src =
        data.audio_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").play();

      const selStart = timelineData.selectedStart;
      const selEnd = timelineData.selectedEnd;

      const getEncompassed = (arr) => {
        if (!arr) return "";
        return arr
          .filter((s) => {
            const mid =
              ((s.start_time || 0) + (s.end_time || s.start_time + 2.0)) / 2.0;
            return mid >= selStart && mid <= selEnd;
          })
          .map((s) => s.text)
          .join(" ");
      };

      const newTargetText = getEncompassed(
        timelineData.contextData.target_context,
      );
      const newSecondaryText = getEncompassed(
        timelineData.contextData.secondary_context,
      );

      const targetTextToSet = newTargetText || timelineData.target.text;
      const cTarget = getLangColors(timelineData.contextData.target_lang);
      const targetLabel = (
        timelineData.contextData.target_lang || "sub"
      ).toUpperCase();
      document.getElementById("mediaText").innerHTML = (targetTextToSet || "")
        .replace(/<br\s*\/?>/gi, " ")
        .replace(/\n/g, " ");

      let transHtml = "";
      if (newSecondaryText) {
        const langLabel = (
          timelineData.contextData.secondary_lang || "sub"
        ).toUpperCase();
        const c = getLangColors(timelineData.contextData.secondary_lang);
        transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${c.badge} mr-2 align-middle">${langLabel}</span><span class="text-gray-600 dark:text-gray-300 align-middle">${newSecondaryText}</span></div>`;
      }
      document.getElementById("mediaTranslations").innerHTML = transHtml;
    } else {
      alert("Extraction failed: " + data.detail);
    }
  } catch (error) {
    alert("Error calling extraction API: " + error);
  } finally {
    document.getElementById("mediaModalLoading").classList.add("hidden");
    btn.disabled = false;
    btn.innerText = "Apply & Re-extract";
  }
}
