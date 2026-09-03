"use strict";

// Состояние загрузок: slot (1|2) -> upload_id
const uploads = { 1: null, 2: null };
const POLL_INTERVAL_MS = 3000;

const els = {
    zones: { 1: document.getElementById("zone1"), 2: document.getElementById("zone2") },
    inputs: { 1: document.getElementById("file1"), 2: document.getElementById("file2") },
    names: { 1: document.getElementById("name1"), 2: document.getElementById("name2") },
    openButtons: { 1: document.getElementById("open1"), 2: document.getElementById("open2") },
    compare: document.getElementById("compare"),
    status: document.getElementById("status"),
    spinner: document.getElementById("spinner"),
    statusMessage: document.getElementById("status-message"),
    error: document.getElementById("error"),
    degraded: document.getElementById("degraded"),
    diff: document.getElementById("diff"),
    contentLeft: document.getElementById("content-left"),
    contentRight: document.getElementById("content-right"),
    panelLeft: document.getElementById("panel-left"),
    panelRight: document.getElementById("panel-right"),
};

function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
}

function clearMessages() {
    els.error.hidden = true;
    els.error.textContent = "";
    els.degraded.hidden = true;
}

function updateCompareButton() {
    els.compare.disabled = !(uploads[1] && uploads[2]);
}

async function uploadFile(slot, file) {
    clearMessages();
    const form = new FormData();
    form.append("file", file);
    let response;
    try {
        response = await fetch("/api/upload", { method: "POST", body: form });
    } catch {
        showError("Не удалось загрузить файл: ошибка сети");
        return;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        showError(body.error || `Ошибка загрузки файла (код ${response.status})`);
        return;
    }
    uploads[slot] = body.upload_id;
    els.names[slot].textContent = body.filename;
    els.zones[slot].classList.add("loaded");
    updateCompareButton();
}

function setupUploadZone(slot) {
    els.openButtons[slot].addEventListener("click", () => els.inputs[slot].click());
    els.inputs[slot].addEventListener("change", () => {
        const file = els.inputs[slot].files[0];
        if (file) uploadFile(slot, file);
    });

    const zone = els.zones[slot];
    zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("dragover");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("dragover");
        const file = event.dataTransfer.files[0];
        if (file) uploadFile(slot, file);
    });
}

function setStatus(message, withSpinner) {
    els.status.hidden = false;
    els.spinner.hidden = !withSpinner;
    els.statusMessage.textContent = message;
}

function hideStatus() {
    els.status.hidden = true;
}

async function startCompare() {
    clearMessages();
    els.diff.hidden = true;
    els.compare.disabled = true;

    let response;
    try {
        response = await fetch("/api/compare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                upload_id_1: uploads[1],
                upload_id_2: uploads[2],
            }),
        });
    } catch {
        showError("Не удалось запустить сравнение: ошибка сети");
        updateCompareButton();
        return;
    }
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        showError(body.error || `Ошибка запуска сравнения (код ${response.status})`);
        updateCompareButton();
        return;
    }
    const { job_id } = await response.json();
    pollJob(job_id);
}

function pollJob(jobId) {
    const timer = setInterval(async () => {
        let body;
        try {
            const response = await fetch(`/api/jobs/${jobId}`);
            body = await response.json();
            if (!response.ok) throw new Error(body.error || `код ${response.status}`);
        } catch (err) {
            clearInterval(timer);
            hideStatus();
            showError(`Ошибка опроса статуса: ${err.message}`);
            updateCompareButton();
            return;
        }

        if (body.status === "processing") {
            setStatus(body.stage_message || "Обработка...", true);
        } else if (body.status === "done") {
            clearInterval(timer);
            hideStatus();
            renderResult(body.result);
            updateCompareButton();
        } else if (body.status === "failed") {
            clearInterval(timer);
            hideStatus();
            showError(body.error || "Сравнение завершилось ошибкой");
            updateCompareButton();
        }
    }, POLL_INTERVAL_MS);
}

function renderBlock(block) {
    const div = document.createElement("div");
    div.className = "diff-block";
    if (block === null) {
        div.classList.add("placeholder");
        div.innerHTML = "&nbsp;";
        return div;
    }
    // Изменённый блок с пословным diff: подсвечиваем только различающиеся
    // слова (добавлено — зелёный, удалено — красный, изменено — жёлтый),
    // фон всего блока не заливаем
    if (block.segments) {
        for (const seg of block.segments) {
            if (seg.type === "same") {
                div.appendChild(document.createTextNode(seg.text));
            } else {
                const span = document.createElement("span");
                span.className = `seg-${seg.type}`;
                span.textContent = seg.text;
                div.appendChild(span);
            }
        }
        return div;
    }
    div.textContent = block.text;
    if (block.change) div.classList.add(`change-${block.change}`);
    return div;
}

function renderResult(result) {
    els.contentLeft.innerHTML = "";
    els.contentRight.innerHTML = "";
    for (const row of result.rows) {
        els.contentLeft.appendChild(renderBlock(row.left));
        els.contentRight.appendChild(renderBlock(row.right));
    }
    if (!result.semantic) els.degraded.hidden = false;
    els.diff.hidden = false;
}

// Синхронный скролл панелей (без зацикливания через флаг)
let syncing = false;
function syncScroll(source, target) {
    source.addEventListener("scroll", () => {
        if (syncing) return;
        syncing = true;
        target.scrollTop = source.scrollTop;
        target.scrollLeft = source.scrollLeft;
        syncing = false;
    });
}

setupUploadZone(1);
setupUploadZone(2);
els.compare.addEventListener("click", startCompare);
syncScroll(els.panelLeft, els.panelRight);
syncScroll(els.panelRight, els.panelLeft);
