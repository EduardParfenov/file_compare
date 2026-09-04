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
    steps: Array.from(document.querySelectorAll("#stepper .step")),
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

// Степпер этапов: converting → diffing → llm
const STAGES = ["converting", "diffing", "llm"];

function resetStepper() {
    els.steps.forEach((el) => el.classList.remove("active", "done", "error"));
}

// jobStatus: "processing" | "done" | "failed"; stageKey — текущий этап или null
function setStepper(stageKey, jobStatus) {
    const current = STAGES.indexOf(stageKey);
    els.steps.forEach((el, i) => {
        el.classList.remove("active", "done", "error");
        if (jobStatus === "done" || i < current) {
            el.classList.add("done");
        } else if (i === current && jobStatus === "failed") {
            el.classList.add("error");
        } else if (i === current && jobStatus === "processing") {
            el.classList.add("active");
        }
    });
}

async function startCompare() {
    clearMessages();
    els.diff.hidden = true;
    els.compare.disabled = true;
    resetStepper();

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
    let timer = null;
    const tick = async () => {
        let body;
        try {
            const response = await fetch(`/api/jobs/${jobId}`);
            body = await response.json();
            if (!response.ok) throw new Error(body.error || `код ${response.status}`);
        } catch (err) {
            clearInterval(timer);
            showError(`Ошибка опроса статуса: ${err.message}`);
            updateCompareButton();
            return;
        }

        if (body.status === "processing") {
            setStepper(body.stage, "processing");
        } else if (body.status === "done") {
            clearInterval(timer);
            setStepper(body.stage, "done");
            renderResult(body.result);
            updateCompareButton();
        } else if (body.status === "failed") {
            clearInterval(timer);
            setStepper(body.stage, "failed");
            showError(body.error || "Сравнение завершилось ошибкой");
            updateCompareButton();
        }
    };
    // Первый опрос — сразу, чтобы этапы были видны даже на быстрых задачах
    tick();
    timer = setInterval(tick, POLL_INTERVAL_MS);
}

function appendSegments(el, segments) {
    for (const seg of segments) {
        if (seg.type === "same") {
            el.appendChild(document.createTextNode(seg.text));
        } else {
            const span = document.createElement("span");
            span.className = `seg-${seg.type}`;
            span.textContent = seg.text;
            el.appendChild(span);
        }
    }
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
        appendSegments(div, block.segments);
        return div;
    }
    div.textContent = block.text;
    if (block.change) div.classList.add(`change-${block.change}`);
    return div;
}

// Строка результата относится к таблице: хотя бы одна сторона — строка
// таблицы (cells или sep), другая — тоже строка таблицы или пустое место
function isTableGroupRow(row) {
    const ok = (b) => b === null || "cells" in b || b.sep === true;
    const isTable = (b) => b !== null && ("cells" in b || b.sep === true);
    return ok(row.left) && ok(row.right) && (isTable(row.left) || isTable(row.right));
}

// Строки таблицы приходят отдельными блоками — собираем в одну
// HTML-таблицу. Служебная строка-разделитель (sep) пропускается.
// Разное число колонок дополняется пустыми ячейками
function renderTableSide(blocks) {
    const div = document.createElement("div");
    div.className = "diff-block diff-table";
    const dataBlocks = blocks.filter((b) => b !== null && !b.sep);
    if (dataBlocks.length === 0) {
        div.classList.add("placeholder");
        div.innerHTML = "&nbsp;";
        return div;
    }
    const cols = Math.max(...dataBlocks.map((b) => b.cells.length));
    const table = document.createElement("table");
    dataBlocks.forEach((block) => {
        const tr = document.createElement("tr");
        // added/removed — подсветка всей строки; changed — пословно в ячейках
        if (block.change && !block.cell_segments) {
            tr.classList.add(`change-${block.change}`);
        }
        for (let c = 0; c < cols; c++) {
            const cellEl = document.createElement("td");
            if (block.cell_segments && block.cell_segments[c]) {
                appendSegments(cellEl, block.cell_segments[c]);
            } else if (c < block.cells.length) {
                cellEl.textContent = block.cells[c];
            }
            tr.appendChild(cellEl);
        }
        table.appendChild(tr);
    });
    div.appendChild(table);
    return div;
}

function renderResult(result) {
    els.contentLeft.innerHTML = "";
    els.contentRight.innerHTML = "";
    const rows = result.rows;
    let i = 0;
    while (i < rows.length) {
        if (isTableGroupRow(rows[i])) {
            let j = i;
            while (j < rows.length && isTableGroupRow(rows[j])) j++;
            const group = rows.slice(i, j);
            els.contentLeft.appendChild(renderTableSide(group.map((row) => row.left)));
            els.contentRight.appendChild(renderTableSide(group.map((row) => row.right)));
            i = j;
        } else {
            els.contentLeft.appendChild(renderBlock(rows[i].left));
            els.contentRight.appendChild(renderBlock(rows[i].right));
            i++;
        }
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
