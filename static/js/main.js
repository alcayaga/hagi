document.getElementById("searchInput").addEventListener("keypress", function (e) {
  if (e.key === "Enter") performSearch();
});

// Responsive Placeholder Text
/**
 * Dynamically updates the search bar placeholder text based on viewport width
 * to prevent awkward text cropping on narrow mobile screens.
 */
function updateSearchPlaceholder() {
  const input = document.getElementById("searchInput");
  if (!input) return;
  if (window.innerWidth < 640) {
    input.placeholder = "Search phrases...";
  } else {
    input.placeholder = "Search for a Japanese phrase or translation...";
  }
}
window.addEventListener("resize", updateSearchPlaceholder);
document.addEventListener("DOMContentLoaded", updateSearchPlaceholder);
updateSearchPlaceholder();

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

let activeShow = null;
let activeSeason = null;
let activeEp = null;

/**
 * Handles the selection of a specific Show from the unified filters.
 * Resets the underlying season/episode states and refreshes the UI.
 */
function onShowChange() {
  activeShow = document.getElementById("filterShow").value || null;
  activeSeason = null;
  activeEp = null;
  populateDropdowns();
  renderResults();
}

/**
 * Handles the selection of a specific Season or Episode from the unified filters.
 * Parses the compound string value (e.g. "s1e2") to update internal filter state.
 */
function onEpisodeChange() {
  const val = document.getElementById("filterEpisode").value;
  if (!val) {
    activeSeason = null;
    activeEp = null;
  } else if (val.startsWith("s") && val.includes("e")) {
    const parts = val.split("e");
    activeSeason = parseInt(parts[0].replace("s", ""));
    activeEp = parseInt(parts[1]);
  } else if (val.startsWith("s")) {
    activeSeason = parseInt(val.replace("s", ""));
    activeEp = null;
  } else if (val.startsWith("e")) {
    activeSeason = null;
    activeEp = parseInt(val.replace("e", ""));
  }
  renderResults();
}

/**
 * Dynamically populates the Search Filter dropdowns based on the available data.
 * Constructs optgroups for episodes categorized by season to improve readability.
 */
function populateDropdowns() {
  const showSelect = document.getElementById("filterShow");
  const epSelect = document.getElementById("filterEpisode");
  const epWrapper = document.getElementById("episodeWrapper");

  // 1. Always populate Shows
  if (!showSelect.options.length || showSelect.options.length === 1) {
    const uniqueShows = [...new Set(allSearchResults.map((r) => r.show_title || r.path.split("/").pop()))].sort();
    showSelect.innerHTML = '<option value="">All Shows</option>';
    uniqueShows.forEach((s) => showSelect.add(new Option(`${s}`, s)));
    showSelect.value = activeShow || "";
  }

  // 2. If no show selected, hide Episode wrapper
  if (!activeShow) {
    epWrapper.classList.add("hidden");
    return;
  }

  // 3. Populate Unified Season & Episode Dropdown
  const showResults = allSearchResults.filter((r) => (r.show_title || r.path.split("/").pop()) === activeShow);
  const uniqueSeasons = [...new Set(showResults.filter((r) => r.season != null).map((r) => r.season))].sort((a, b) => a - b);
  const hasEpisodes = showResults.some((r) => r.episode != null);

  if (uniqueSeasons.length === 0 && !hasEpisodes) {
    epWrapper.classList.add("hidden");
    return;
  }

  epWrapper.classList.remove("hidden");
  epSelect.innerHTML = '<option value="">All Episodes</option>';

  if (uniqueSeasons.length > 0) {
    uniqueSeasons.forEach((s) => {
      const optGroup = document.createElement("optgroup");
      optGroup.label = `Season ${s}`;
      optGroup.appendChild(new Option(`All of Season ${s}`, `s${s}`));

      const seasonEps = [...new Set(showResults.filter((r) => r.season == s && r.episode != null).map((r) => r.episode))].sort((a, b) => a - b);
      seasonEps.forEach((e) => {
        optGroup.appendChild(new Option(`S${s} E${e}`, `s${s}e${e}`));
      });
      epSelect.appendChild(optGroup);
    });
  } else {
    const uniqueEps = [...new Set(showResults.filter((r) => r.episode != null).map((r) => r.episode))].sort((a, b) => a - b);
    uniqueEps.forEach((e) => {
      epSelect.add(new Option(`Episode ${e}`, `e${e}`));
    });
  }

  // Restore selection
  if (activeSeason !== null && activeEp !== null) epSelect.value = `s${activeSeason}e${activeEp}`;
  else if (activeSeason !== null) epSelect.value = `s${activeSeason}`;
  else if (activeEp !== null) epSelect.value = `e${activeEp}`;
}

/**
 * Fetches all search results matching the query string from the backend API.
 * Populates the local results cache and updates the UI filters.
 */
async function performSearch() {
  const query = document.getElementById("searchInput").value;
  const loading = document.getElementById("loading");
  const container = document.getElementById("resultsList");

  if (!query.trim()) return;

  loading.classList.remove("hidden");
  container.innerHTML = "";

  // Reset filters
  activeShow = null;
  activeSeason = null;
  activeEp = null;
  document.getElementById("filterShow").innerHTML = '<option value="">All Shows</option>';

  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    allSearchResults = await response.json();

    document.getElementById("filtersAndControlsWrapper").classList.remove("hidden");
    populateDropdowns();
    renderResults();
  } catch (error) {
    container.innerHTML = `<div class="px-6 py-4 text-center text-red-500">Error fetching results: ${error}</div>`;
  } finally {
    loading.classList.add("hidden");
  }
}

/**
 * Applies the currently selected Show and Episode filters to the local results cache
 * and dynamically generates the HTML for the results table.
 */
function renderResults() {
  const container = document.getElementById("resultsList");
  container.innerHTML = "";

  // Apply client-side filters
  let filtered = allSearchResults;
  if (activeShow) {
    filtered = filtered.filter((r) => (r.show_title || r.path.split("/").pop()) === activeShow);
  }
  if (activeSeason !== null) {
    filtered = filtered.filter((r) => r.season == activeSeason);
  }
  if (activeEp !== null) {
    filtered = filtered.filter((r) => r.episode == activeEp);
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div class="text-center text-gray-500 dark:text-gray-400 p-8 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">No results found matching your filters</div>`;
    return;
  }

  const rawQuery = document.getElementById("searchInput").value;
  const searchTermsTokens = rawQuery.match(/(".*?"|[^"\s]+)+(?=\s*|\s*$)/g) || [];
  const validTerms = searchTermsTokens
    .filter((t) => !t.startsWith("-"))
    .map((t) => t.replace(/(^"|"$)/g, ""))
    .filter((t) => t.trim().length > 0)
    .sort((a, b) => b.length - a.length);

  let highlightRegex = null;
  if (validTerms.length > 0) {
    highlightRegex = new RegExp(`(${validTerms.map((t) => t.replace(/[-\\/\\\\^$*+?.()|[\\]{}]/g, "\\\\$&")).join("|")})`, "gi");
  }

  function highlightText(text) {
    if (!text) return "";
    // Sanitize text first to prevent HTML injection from search results
    const div = document.createElement("div");
    div.innerText = text;
    let sanitized = div.innerHTML;

    if (highlightRegex) {
      sanitized = sanitized.replace(highlightRegex, `<mark class="bg-indigo-100 text-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-300 rounded px-1 py-0.5">$1</mark>`);
    }
    return sanitized;
  }

  filtered.forEach((r) => {
    const totalSecs = Math.floor(r.start_time);
    const h = Math.floor(totalSecs / 3600);
    const m = Math.floor((totalSecs % 3600) / 60)
      .toString()
      .padStart(h > 0 ? 2 : 1, "0");
    const s = (totalSecs % 60).toString().padStart(2, "0");
    const timeStr = h > 0 ? `${h}:${m}:${s}` : `${m}:${s}`;

    let sourceDisplay = r.path.split("/").pop();
    let subParts = [];
    if (r.show_title) {
      sourceDisplay = `${r.show_title}`;
      if (r.season !== null && r.episode !== null) {
        subParts.push(`S${r.season} E${r.episode}`);
      } else if (r.episode !== null) {
        subParts.push(`EP ${r.episode}`);
      }
    }

    const cleanText = r.text ? r.text.replace(/\n/g, " ") : "";
    const cleanSpa = r.spa_translation ? r.spa_translation.replace(/\n/g, " ") : "";
    const cleanEng = r.eng_translation ? r.eng_translation.replace(/\n/g, " ") : "";

    const card = document.createElement("div");
    card.className = "bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 hover:shadow-md transition-shadow p-4 md:p-5 flex flex-col md:flex-row gap-4 justify-between group";

    card.innerHTML = `
      <!-- Left: Content -->
      <div class="flex flex-col gap-1.5 flex-grow">
        <!-- Top Metadata -->
        <div class="flex flex-wrap items-center gap-2 text-[0.7rem] font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
          <span>${sourceDisplay}</span>
          ${subParts.length > 0 ? `<span>&bull;</span><span>${subParts.join(" ")}</span>` : ""}
          ${r.episode_title ? `<span>&bull;</span><span class="italic text-gray-400 dark:text-gray-500">"${r.episode_title}"</span>` : ""}
          <span>&bull;</span>
          <span class="font-mono bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-600 dark:text-gray-300 shadow-sm">${timeStr}</span>
        </div>
        
        <!-- Primary Text -->
        <div class="text-xl font-bold text-gray-900 dark:text-gray-100 mt-1">${highlightText(cleanText)}</div>
        
        <!-- Translations -->
        <div class="flex flex-col gap-1.5 mt-1.5">
          ${cleanSpa ? `<div class="text-sm leading-snug"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("spa").badge} mr-2 align-middle">SPA</span>&nbsp;<span class="text-gray-500 dark:text-gray-400 italic align-middle">${highlightText(cleanSpa)}</span></div>` : ""}
          ${cleanEng ? `<div class="text-sm leading-snug"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("eng").badge} mr-2 align-middle">ENG</span>&nbsp;<span class="text-gray-500 dark:text-gray-400 italic align-middle">${highlightText(cleanEng)}</span></div>` : ""}
        </div>
      </div>

      <!-- Right: Actions -->
      <div class="flex flex-row md:flex-col gap-2 justify-start md:justify-center flex-shrink-0 border-t md:border-t-0 md:border-l border-gray-100 dark:border-gray-700 pt-4 md:pt-0 md:pl-5 mt-2 md:mt-0">
         <button onclick="viewContext(${r.id})" class="flex-1 md:flex-none md:w-24 bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200 px-3 py-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 font-bold text-sm transition shadow-sm">Context</button>
         <button onclick="extractMedia(${r.id}, this)" class="flex-1 md:flex-none md:w-24 bg-indigo-600 text-white dark:bg-indigo-600 dark:text-white px-3 py-2 rounded-lg hover:bg-indigo-700 dark:hover:bg-indigo-500 font-bold text-sm transition shadow-sm">Extract</button>
      </div>
    `;
    container.appendChild(card);
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
  const sStart = parseFloat(document.getElementById("padStart").value);
  const sEnd = parseFloat(document.getElementById("padEnd").value);
  const padStart = isNaN(sStart) ? 0.5 : sStart;
  const padEnd = isNaN(sEnd) ? 0.5 : sEnd;

  currentExtraction.id = id;
  currentExtraction.padStart = padStart;
  currentExtraction.padEnd = padEnd;

  const originalText = btnElement.innerText;

  const r = allSearchResults.find((x) => x.id === id);
  if (r) {
    const mainTitle = r.show_title || r.path.split("/").pop();
    const subParts = [];

    if (r.season !== null && r.episode !== null) {
      subParts.push(`S${r.season} E${r.episode}`);
    } else if (r.episode !== null) {
      subParts.push(`EP ${r.episode}`);
    }

    if (r.episode_title) {
      subParts.push(`"${r.episode_title}"`);
    }

    const totalSecs = Math.floor(r.start_time);
    const h = Math.floor(totalSecs / 3600);
    const m = Math.floor((totalSecs % 3600) / 60)
      .toString()
      .padStart(h > 0 ? 2 : 1, "0");
    const s = (totalSecs % 60).toString().padStart(2, "0");
    const timeStr = h > 0 ? `${h}:${m}:${s}` : `${m}:${s}`;

    document.getElementById("mediaMetadata").innerHTML = `
      <div class="flex flex-col leading-tight">
        <span class="text-lg font-bold text-gray-900 dark:text-gray-100 truncate">${mainTitle}</span>
        <span class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide truncate mt-0.5">
          ${subParts.length > 0 ? subParts.join(" &bull; ") : "Unknown Episode"}
        </span>
      </div>
    `;

    document.getElementById("mediaTimestampBadge").innerText = timeStr;
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
      document.getElementById("mediaText").innerHTML = `` + (data.text || "").replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ");

      const cleanSpa = r.spa_translation ? r.spa_translation.replace(/\n/g, " ") : "";
      const cleanEng = r.eng_translation ? r.eng_translation.replace(/\n/g, " ") : "";
      let transHtml = "";
      if (cleanSpa) transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("spa").badge} mr-2 align-middle">SPA</span><span class="text-gray-500 dark:text-gray-400 italic align-middle">${cleanSpa}</span></div>`;
      if (cleanEng) transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${getLangColors("eng").badge} mr-2 align-middle">ENG</span><span class="text-gray-500 dark:text-gray-400 italic align-middle">${cleanEng}</span></div>`;
      document.getElementById("mediaTranslations").innerHTML = transHtml;

      document.getElementById("mediaImage").src = data.image_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").src = data.audio_url + "?t=" + new Date().getTime();
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
      const list = document.getElementById("contextList");
      list.innerHTML = "";

      // Match and group secondary translations to target sentences
      const groupedCards = [];
      let currentGroup = null;

      data.target_context.forEach((tgt) => {
        let bestSec = null;
        let minDiff = 5.0;

        if (data.secondary_context && data.secondary_context.length > 0) {
          data.secondary_context.forEach((sec) => {
            const diff = Math.abs(sec.start_time - tgt.start_time);
            if (diff < minDiff) {
              minDiff = diff;
              bestSec = sec;
            }
          });
        }

        if (currentGroup && bestSec && currentGroup.secId === bestSec.id) {
          // This target sentence maps to the same secondary sentence as the previous one
          currentGroup.targets.push(tgt);
        } else {
          // Start a new group
          if (currentGroup) {
            groupedCards.push(currentGroup);
          }
          currentGroup = {
            secId: bestSec ? bestSec.id : null,
            secText: bestSec ? bestSec.text : null,
            secLang: data.secondary_lang,
            targets: [tgt],
          };
        }
      });
      if (currentGroup) {
        groupedCards.push(currentGroup);
      }

      if (groupedCards.length === 0) {
        list.innerHTML = `<p class="text-gray-500 text-center py-4">No context available.</p>`;
      } else {
        groupedCards.forEach((group) => {
          const isTarget = group.targets.some((t) => t.id === id);

          const firstTarget = group.targets[0];
          const totalSecs = Math.floor(firstTarget.start_time);
          const h = Math.floor(totalSecs / 3600);
          const m = Math.floor((totalSecs % 3600) / 60)
            .toString()
            .padStart(h > 0 ? 2 : 1, "0");
          const s = (totalSecs % 60).toString().padStart(2, "0");
          const timeStr = h > 0 ? `${h}:${m}:${s}` : `${m}:${s}`;

          const cleanText = group.targets.map((t) => (t.text ? t.text.replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ") : "")).join("<br/>");
          const cleanSec = group.secText ? group.secText.replace(/<br\s*\/?>/gi, " ").replace(/\n/g, " ") : "";

          const card = document.createElement("div");

          // Style the matched card distinctly
          const baseClasses = "rounded-xl shadow-sm border p-4 md:p-5 flex flex-col gap-2 transition-all";
          if (isTarget) {
            card.className = `${baseClasses} bg-indigo-50 dark:bg-indigo-900/30 border-indigo-300 dark:border-indigo-600 ring-2 ring-indigo-500/20`;
          } else {
            card.className = `${baseClasses} bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700`;
          }

          let secondaryHtml = "";
          if (cleanSec && group.secLang) {
            const badgeColors = getLangColors(group.secLang);
            const langCode = group.secLang.substring(0, 3).toUpperCase();
            secondaryHtml = `<div class="text-sm mt-1 leading-snug"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${badgeColors.badge} mr-2 align-middle">${langCode}</span>&nbsp;<span class="text-gray-500 dark:text-gray-400 italic align-middle">${cleanSec}</span></div>`;
          }

          card.innerHTML = `
            <div class="flex items-center gap-2 mb-1">
              <span class="font-mono bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-xs text-gray-600 dark:text-gray-300 shadow-sm">${timeStr}</span>
              ${isTarget ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-100 text-indigo-800 dark:bg-indigo-800 dark:text-indigo-100 uppercase tracking-wider">Search Match</span>` : ""}
            </div>
            <div class="text-lg font-bold text-gray-900 dark:text-gray-100">${cleanText}</div>
            ${secondaryHtml}
          `;

          list.appendChild(card);

          // Auto-scroll to the matched target
          if (isTarget) {
            setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "center" }), 100);
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
  if (modalId === "mediaModal") {
    toggleModalView("mediaExtractView");
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
  timelineContainer.innerHTML = '<div class="absolute inset-0 flex justify-center items-center"><div class="animate-spin rounded-full h-5 w-5 border-b-2 border-indigo-600"></div></div>';

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
  timelineData.selectedStart = Math.max(0, targetStart - currentExtraction.padStart);
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
  grid.className = "absolute inset-0 pointer-events-none flex flex-col justify-between opacity-20";
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
      if (sEnd < timelineData.windowStart || sStart > timelineData.windowEnd) return;
      const leftPct = Math.max(0, ((sStart - timelineData.windowStart) / timelineData.duration) * 100);
      const rightPct = Math.max(0, ((timelineData.windowEnd - sEnd) / timelineData.duration) * 100);

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
      if (sEnd < timelineData.windowStart || sStart > timelineData.windowEnd) return;

      const leftPct = Math.max(0, ((sStart - timelineData.windowStart) / timelineData.duration) * 100);
      const rightPct = Math.max(0, ((timelineData.windowEnd - sEnd) / timelineData.duration) * 100);

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

  const unselectedLeft = document.createElement("div");
  unselectedLeft.id = "timelineUnselectedLeft";
  unselectedLeft.className = "absolute top-0 bottom-0 left-0 bg-black bg-opacity-40 z-10 pointer-events-none";

  const unselectedRight = document.createElement("div");
  unselectedRight.id = "timelineUnselectedRight";
  unselectedRight.className = "absolute top-0 bottom-0 right-0 bg-black bg-opacity-40 z-10 pointer-events-none";

  container.appendChild(unselectedLeft);
  container.appendChild(unselectedRight);

  const selected = document.createElement("div");
  selected.id = "timelineSelected";
  selected.className = "absolute h-full border-y-2 border-indigo-500 group pointer-events-none z-20";

  const hStart = document.createElement("div");
  hStart.id = "handleStart";
  hStart.className = "absolute top-0 bottom-0 left-0 w-6 -ml-3 cursor-ew-resize flex justify-center items-center pointer-events-auto hover:brightness-110";
  hStart.innerHTML = '<div class="w-2 h-full bg-indigo-500 rounded-full shadow-md border border-indigo-600 flex items-center justify-center"><div class="w-[2px] h-3 bg-white rounded-full opacity-70"></div></div>';

  const hEnd = document.createElement("div");
  hEnd.id = "handleEnd";
  hEnd.className = "absolute top-0 bottom-0 right-0 w-6 -mr-3 cursor-ew-resize flex justify-center items-center pointer-events-auto hover:brightness-110";
  hEnd.innerHTML = '<div class="w-2 h-full bg-indigo-500 rounded-full shadow-md border border-indigo-600 flex items-center justify-center"><div class="w-[2px] h-3 bg-white rounded-full opacity-70"></div></div>';

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

  const unleft = document.getElementById("timelineUnselectedLeft");
  const unright = document.getElementById("timelineUnselectedRight");

  const leftPct = Math.max(0, ((timelineData.selectedStart - timelineData.windowStart) / timelineData.duration) * 100);
  const rightPct = Math.max(0, ((timelineData.windowEnd - timelineData.selectedEnd) / timelineData.duration) * 100);

  selected.style.left = `${leftPct}%`;
  selected.style.right = `${rightPct}%`;

  if (unleft) unleft.style.width = `${leftPct}%`;
  if (unright) unright.style.width = `${rightPct}%`;

  document.getElementById("timelineDurationDisplay").innerText = `${(timelineData.selectedEnd - timelineData.selectedStart).toFixed(2)}s`;
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
      timelineData.selectedStart = Math.min(time, timelineData.selectedEnd - 0.5);
    } else if (timelineData.activeHandle === "end") {
      timelineData.selectedEnd = Math.max(time, timelineData.selectedStart + 0.5);
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

  if (timelineData.selectedEnd <= timelineData.selectedStart) {
    alert("Invalid timeline selection.");
    return;
  }

  currentExtraction.padStart = targetStart - timelineData.selectedStart;
  currentExtraction.padEnd = timelineData.selectedEnd - targetEnd;

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
      document.getElementById("mediaImage").src = data.image_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").src = data.audio_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").play();

      const selStart = timelineData.selectedStart;
      const selEnd = timelineData.selectedEnd;

      const cleanText = (text, lang) => {
        let cleaned = (text || "").replace(/<br\s*\/?>/gi, lang === "jpn" ? "" : " ");
        return cleaned.replace(/\n/g, lang === "jpn" ? "" : " ");
      };

      const getEncompassed = (arr, lang) => {
        if (!arr) return "";

        const filtered = arr.filter((s) => {
          const mid = ((s.start_time || 0) + (s.end_time || s.start_time + 2.0)) / 2.0;
          return mid >= selStart && mid <= selEnd;
        });

        return filtered
          .map((s) => cleanText(s.text, lang))
          .reduce((acc, text, i, array) => {
            if (i === 0) return text;

            if (lang === "jpn") {
              const prev = array[i - 1];
              const endsSentence = /[だですまるかよねわぞ。！？]$/.test(prev.trim());
              return acc + (endsSentence ? "<br/>" : "") + text;
            } else {
              let prevText = acc.trim();
              if (prevText.length > 0 && !prevText.match(/[.!?…,;:]$/)) {
                prevText += ".";
              }
              return prevText + " " + text;
            }
          }, "");
      };

      const newTargetText = getEncompassed(timelineData.contextData.target_context, timelineData.contextData.target_lang);
      const newSecondaryText = getEncompassed(timelineData.contextData.secondary_context, timelineData.contextData.secondary_lang);

      const targetTextToSet = newTargetText || cleanText(timelineData.target.text, timelineData.contextData.target_lang);
      const cTarget = getLangColors(timelineData.contextData.target_lang);
      const targetLabel = (timelineData.contextData.target_lang || "sub").toUpperCase();
      document.getElementById("mediaText").innerHTML = targetTextToSet;

      let transHtml = "";
      if (newSecondaryText) {
        const langLabel = (timelineData.contextData.secondary_lang || "sub").toUpperCase();
        const c = getLangColors(timelineData.contextData.secondary_lang);
        transHtml += `<div class="text-sm mt-2"><span class="inline-block px-1.5 py-0.5 rounded text-[0.65rem] font-bold ${c.badge} mr-2 align-middle">${langLabel}</span><span class="text-gray-500 dark:text-gray-400 italic align-middle">${newSecondaryText}</span></div>`;
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

/**
 * Replays the audio from the beginning.
 */
function replayAudio() {
  const audio = document.getElementById("mediaAudio");
  if (audio) {
    audio.currentTime = 0;
    audio.play();
  }
}

/**
 * Shows a toast notification.
 * @param {string} message - Message to show.
 * @param {string} type - 'success' or 'error'
 */
function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  const bgClass = type === "success" ? "bg-green-600" : "bg-red-600";
  toast.className = `flex items-center gap-2 text-white px-4 py-3 rounded shadow-lg transform transition-all duration-300 translate-y-10 opacity-0 ${bgClass}`;

  const icon = type === "success" ? '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>';

  toast.innerHTML = icon;
  const textSpan = document.createElement("span");
  textSpan.className = "text-sm font-semibold";
  textSpan.textContent = message;
  toast.appendChild(textSpan);
  container.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => {
    toast.classList.remove("translate-y-10", "opacity-0");
  });

  setTimeout(() => {
    toast.classList.add("opacity-0");
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

/**
 * Sends the currently extracted media to Anki via the backend API.
 * Uses the exact parameters currently stored in currentExtraction.
 * @param {HTMLElement} btn - The button element that was clicked
 * @param {string|null} targetNoteId - Optional specific NID to update
 */
async function sendToAnki(btn, targetNoteId = null) {
  if (!currentExtraction.id) return;

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<div class="animate-spin h-4 w-4 border-b-2 border-white rounded-full"></div><span>Sending...</span>`;
  btn.classList.add("opacity-70");

  try {
    const payload = {
      pad_start: currentExtraction.padStart,
      pad_end: currentExtraction.padEnd,
    };
    if (targetNoteId) {
      payload.target_note_id = Number(targetNoteId);
    }

    const response = await fetch(`/api/anki/${currentExtraction.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();

    if (data.success) {
      showToast("Successfully sent to Anki!", "success");
      if (targetNoteId) toggleModalView("mediaExtractView");
    } else {
      showToast(data.detail || "Failed to send to Anki.", "error");
    }
  } catch (err) {
    console.error("Error sending to Anki:", err);
    showToast("Error connecting to server.", "error");
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
    btn.classList.remove("opacity-70");
  }
}

/**
 * Handles sending to a specific Anki NID from the UI
 */
function sendToAnkiSpecific(btn) {
  const nidInput = document.getElementById("ankiTargetNid");
  const nid = nidInput.value.trim();
  if (!nid) {
    showToast("Please enter a Note ID.", "error");
    return;
  }
  const numericNid = Number(nid);
  if (!Number.isSafeInteger(numericNid) || numericNid <= 0) {
    showToast("Please enter a valid positive Note ID.", "error");
    return;
  }
  sendToAnki(btn, numericNid);
}

/**
 * Toggles the views inside the media extraction modal
 */
function toggleModalView(viewName) {
  const extractView = document.getElementById("mediaExtractView");
  const searchView = document.getElementById("mediaAnkiSearchView");
  const backBtn = document.getElementById("mediaModalBackButton");

  if (!extractView || !searchView || !backBtn) return;

  if (viewName === "mediaAnkiSearchView") {
    extractView.classList.add("-translate-x-full");
    searchView.classList.remove("invisible", "translate-x-full");
    backBtn.classList.remove("hidden");
  } else {
    extractView.classList.remove("-translate-x-full");
    searchView.classList.add("translate-x-full");
    backBtn.classList.add("hidden");
    // Hide completely after transition to prevent blocking clicks
    setTimeout(() => {
      if (!extractView.classList.contains("-translate-x-full")) {
        searchView.classList.add("invisible");
      }
    }, 300);
  }
}
