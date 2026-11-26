
(() => {
    const API_URL = "https://wy4fap3sc9.execute-api.eu-north-1.amazonaws.com/api/people-counts";
    const PAGE_SIZE = 4;

    const dateInput = document.getElementById("people-count-date");
    const summary = document.getElementById("people-count-summary");
    const results = document.getElementById("people-count-results");
    const errorBox = document.getElementById("people-count-error");
    const moreButton = document.getElementById("people-count-more");

    const modal = document.getElementById("people-count-image-modal");
    const enlargedImage = document.getElementById("people-count-enlarged-image");
    const closeButton = document.getElementById("people-count-image-close");

    let currentOffset = 0;
    let currentDate = "";

    function localDateString() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, "0");
        const day = String(now.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, char => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        })[char]);
    }

    function formatTimestamp(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "Unknown time";

        return date.toLocaleString("en-GB", {
            weekday: "short",
            day: "numeric",
            month: "short",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        });
    }

    function formatSeconds(value) {
        if (value === null || value === undefined) return "";
        return `${Number(value).toFixed(1)} seconds`;
    }

    function modelPanel(title, count, seconds, error, imageUrl, alt) {
        let details;

        if (error) {
            details = `<p class="red mb3">Error: ${escapeHtml(error)}</p>`;
        } else {
            details = `
                <p class="mb3">
                    <strong>People counted:</strong>
                    ${count ?? "Not available"}
                </p>
            `;
        }

        const image = imageUrl
            ? `<img src="${escapeHtml(imageUrl)}"
                    alt="${escapeHtml(alt)}"
                    class="db w-100 h-auto pointer people-count-image"
                    loading="lazy">`
            : `<p class="gray">Image unavailable</p>`;

        return `
            <div class="w-100 w-50-l pa2">
                <h3 class="mt2 mb2">${title}</h3>
                ${details}
                ${image}
            </div>
        `;
    }

    function renderCapture(record) {
        const capture = document.createElement("div");
        capture.className = "mb4 pb3 bb b--black-20";

        capture.innerHTML = `
            <h2 class="f4 mb2">
                ${escapeHtml(formatTimestamp(record.captured_at))}
            </h2>

            <div class="flex flex-wrap">
                <div class="w-100 w-50-l pa2">
                  <h3 class="mt2 mb2">Original photograph</h3>
                  <p class="mb3">&nbsp;</p>
                  <img
                      src="${escapeHtml(record.images?.original || "")}"
                      alt="Original photograph"
                      class="db w-100 h-auto pointer people-count-image"
                      loading="lazy">
              </div>

                ${modelPanel(
                    "AI annotated image",
                    record.close_pairs_count,
                    record.close_pairs_seconds,
                    record.close_pairs_error,
                    record.images?.close_pairs,
                    "Close Pairs model result"
                )}
            </div>
        `;

        results.appendChild(capture);
    }

    async function loadResults(reset = false) {
        if (reset) {
            currentOffset = 0;
            results.innerHTML = "";
            summary.textContent = "Loading results...";
            moreButton.hidden = true;
        }

        errorBox.hidden = true;
        moreButton.disabled = true;

        const requestedDate = dateInput.value;

        try {
            const params = new URLSearchParams({
                date: requestedDate,
                limit: String(PAGE_SIZE),
                offset: String(currentOffset)
            });

            const response = await fetch(`${API_URL}?${params}`);

            if (!response.ok) {
                throw new Error(`Server returned HTTP ${response.status}`);
            }

            const data = await response.json();

            if (reset) {
                summary.textContent =
                    `${data.daily_image_count} photograph(s) captured on ${requestedDate}`;
            }

            if (!data.results || data.results.length === 0) {
                if (reset) {
                    results.innerHTML =
                        '<p class="gray">No photographs found for this date.</p>';
                }
                moreButton.hidden = true;
                return;
            }

            data.results.forEach(renderCapture);

            currentOffset += data.results.length;
            currentDate = requestedDate;

            moreButton.hidden = !data.has_more;

        } catch (error) {
            console.error("Unable to load people-count results:", error);

            errorBox.textContent =
                `Unable to load results: ${error.message}`;
            errorBox.hidden = false;

            if (reset) {
                summary.textContent = "";
            }
        } finally {
            moreButton.disabled = false;
        }
    }

    function showImage(src, alt) {
        if (!src) return;

        enlargedImage.src = src;
        enlargedImage.alt = alt;
        modal.style.display = "flex";
    }

    function hideImage() {
        modal.style.display = "none";
        enlargedImage.src = "";
    }

    results.addEventListener("click", event => {
        const image = event.target.closest(".people-count-image");

        if (image) {
            showImage(image.src, image.alt);
        }
    });

    closeButton.addEventListener("click", hideImage);

    modal.addEventListener("click", event => {
        if (event.target === modal) {
            hideImage();
        }
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            hideImage();
        }
    });

    dateInput.addEventListener("change", () => {
        loadResults(true);
    });

    moreButton.addEventListener("click", () => {
        loadResults(false);
    });

    dateInput.value = localDateString();
    loadResults(true);
})();
