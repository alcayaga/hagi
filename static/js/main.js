document
  .getElementById("searchInput")
  .addEventListener("keypress", function (e) {
    if (e.key === "Enter") performSearch();
  });

// Store all results locally so we can filter them on the client side
let allSearchResults = [];

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

  filtered.forEach((r) => {
    const m = Math.floor(r.start_time / 60)
      .toString()
      .padStart(2, "0");
    const s = Math.floor(r.start_time % 60)
      .toString()
      .padStart(2, "0");
    const timeStr = `${m}:${s}`;

    let sourceDisplay = r.path.split("/").pop();
    if (r.show_title) {
      sourceDisplay = `${r.show_title}`;
      if (r.season !== null && r.episode !== null) {
        sourceDisplay += ` - S${r.season.toString().padStart(2, "0")}E${r.episode.toString().padStart(2, "0")}`;
      } else if (r.episode !== null) {
        sourceDisplay += ` - Ep ${r.episode}`;
      }
    }

    let langClass =
      "bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-200";
    if (r.language === "jpn")
      langClass =
        "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200";
    if (r.language === "eng")
      langClass =
        "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200";
    if (r.language === "spa")
      langClass = "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200";
    if (r.language === "por")
      langClass =
        "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200";

    const row = document.createElement("tr");
    row.className = "hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors";
    row.innerHTML = `
                    <td class="px-6 py-4"><span class="px-2 py-1 rounded text-sm font-bold ${langClass}">${r.language.toUpperCase()}</span></td>
                    <td class="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">${timeStr}</td>
                    <td class="px-6 py-4 text-lg font-medium">${r.text}</td>
                    <td class="px-6 py-4 text-xs text-gray-400 max-w-xs truncate" title="${sourceDisplay}">${sourceDisplay}</td>
                    <td class="px-6 py-4">
                        <div class="flex flex-col items-center space-y-2">
                            <button onclick="viewContext(${r.id})" class="w-20 bg-gray-100 text-gray-700 dark:bg-gray-600 dark:text-gray-200 px-3 py-1 rounded hover:bg-gray-200 dark:hover:bg-gray-500 font-semibold text-sm transition shadow-sm">Context</button>
                            <button onclick="extractMedia(${r.id}, this)" class="w-20 bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200 px-3 py-1 rounded hover:bg-indigo-200 dark:hover:bg-indigo-800 font-semibold text-sm transition shadow-sm">Extract</button>
                        </div>
                    </td>
                `;
    tbody.appendChild(row);
  });
}

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

  const originalText = btnElement.innerText;

  const r = allSearchResults.find((x) => x.id === id);
  if (r) {
    let metaStr = r.show_title || r.path.split("/").pop();
    if (r.season !== null && r.episode !== null) {
      metaStr += ` - S${r.season.toString().padStart(2, "0")}E${r.episode.toString().padStart(2, "0")}`;
    } else if (r.episode !== null) {
      metaStr += ` - Ep ${r.episode}`;
    }
    const m = Math.floor(r.start_time / 60)
      .toString()
      .padStart(2, "0");
    const s = Math.floor(r.start_time % 60)
      .toString()
      .padStart(2, "0");
    metaStr += ` [${m}:${s}]`;
    document.getElementById("mediaMetadata").innerText = metaStr;
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
      document.getElementById("mediaText").innerText = data.text;
      document.getElementById("mediaImage").src =
        data.image_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").src =
        data.audio_url + "?t=" + new Date().getTime();
      document.getElementById("mediaAudio").play();
      document.getElementById("mediaModal").classList.remove("hidden");
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
        const grid = document.createElement("div");
        grid.className = "grid grid-cols-2 gap-4";

        const colTarget = document.createElement("div");
        colTarget.className =
          "flex flex-col gap-2 border-r dark:border-gray-700 pr-4";
        colTarget.innerHTML = `<h4 class="font-bold text-gray-400 mb-2 uppercase text-xs">${data.target_lang} Track</h4>`;

        const colJpn = document.createElement("div");
        colJpn.className = "flex flex-col gap-2 pl-2";
        colJpn.innerHTML = `<h4 class="font-bold text-gray-400 mb-2 uppercase text-xs">${data.secondary_lang.toUpperCase()} Track</h4>`;

        const renderSentences = (sentences, container) => {
          sentences.forEach((r) => {
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
            div.className = `p-2 rounded border ${bgClass}`;
            div.innerHTML = `
                                    <span class="text-xs text-gray-500 dark:text-gray-400 font-mono block mb-1">[${timeStr}]</span>
                                    <span class="text-sm ${isTarget ? "font-bold text-indigo-700 dark:text-indigo-300" : ""}">${r.text}</span>
                                `;
            container.appendChild(div);

            if (isTarget) {
              setTimeout(
                () =>
                  div.scrollIntoView({ behavior: "smooth", block: "center" }),
                100,
              );
            }
          });
        };

        renderSentences(data.target_context, colTarget);
        renderSentences(data.secondary_context, colJpn);

        grid.appendChild(colTarget);
        grid.appendChild(colJpn);
        list.appendChild(grid);
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
                                <span class="${isTarget ? "font-bold text-indigo-700 dark:text-indigo-300" : ""}">${r.text}</span>
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
