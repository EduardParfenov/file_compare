"use strict";

// Состояние загрузок: slot (1|2) -> upload_id
const uploads = { 1: null, 2: null };
const POLL_INTERVAL_MS = 1000;

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

// Режем текстовые узлы root по глобальным смещениям points
// (отсортированы). Возвращает куски {node, start, end} в порядке текста.
function splitTextNodesAt(root, points) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let n;
    while ((n = walker.nextNode())) textNodes.push(n);
    const pieces = [];
    let offset = 0;
    for (const textNode of textNodes) {
        if (!textNode.data.length) continue;
        let node = textNode;
        let nodeStart = offset;
        const nodeEnd = offset + textNode.data.length;
        for (const cut of points) {
            if (cut <= nodeStart || cut >= nodeEnd) continue;
            const rest = node.splitText(cut - nodeStart);
            pieces.push({ node, start: nodeStart, end: cut });
            node = rest;
            nodeStart = cut;
        }
        pieces.push({ node, start: nodeStart, end: nodeEnd });
        offset = nodeEnd;
    }
    return pieces;
}

// Вплетает пословный diff в текстовые узлы HTML: сегменты del/add
// оборачиваются в span.seg-*, пустые метки del-mark/add-mark вставляются
// в свою позицию. Разметка блока не разрывается и не дублируется.
// Предусловие: root.textContent === конкатенация текстов сегментов.
function weaveSegments(root, segments) {
    const intervals = []; // {start, end, type} — подсвечиваемые диапазоны
    const marks = []; // {pos, type} — пустые метки мест удалений/добавлений
    let pos = 0;
    for (const seg of segments) {
        if (seg.type === "del" || seg.type === "add") {
            intervals.push({ start: pos, end: pos + seg.text.length, type: seg.type });
            pos += seg.text.length;
        } else if (seg.type === "same") {
            pos += seg.text.length;
        } else {
            marks.push({ pos, type: seg.type });
        }
    }
    const points = new Set();
    intervals.forEach((i) => {
        points.add(i.start);
        points.add(i.end);
    });
    marks.forEach((m) => points.add(m.pos));
    const pieces = splitTextNodesAt(root, [...points].sort((a, b) => a - b));

    for (const piece of pieces) {
        const iv = intervals.find((i) => i.start <= piece.start && piece.end <= i.end);
        if (!iv) continue;
        const span = document.createElement("span");
        span.className = `seg-${iv.type}`;
        piece.node.parentNode.replaceChild(span, piece.node);
        span.appendChild(piece.node);
    }
    for (const m of marks) {
        const span = document.createElement("span");
        span.className = `seg-${m.type}`;
        const piece = pieces.find((p) => p.start === m.pos);
        if (piece) {
            piece.node.parentNode.insertBefore(span, piece.node);
        } else {
            root.appendChild(span); // метка в самом конце блока
        }
    }
}

// Изображения блока: data-URI из конвертации; images_changed — рамка
// по стороне (красная слева/файл 1, зелёная справа/файл 2)
function appendImages(container, block, side) {
    for (const uri of block.images || []) {
        const img = document.createElement("img");
        img.src = uri;
        img.className = "block-image";
        if (block.images_changed) img.classList.add(`image-changed-${side}`);
        container.appendChild(img);
    }
}

// Содержимое блока: HTML из конвертации с вплетённым пословным diff
// либо plain text (блок без html или расхождение html и текста)
function renderBlockContent(div, block) {
    if (block.html) {
        // HTML генерируется только нашим конвертером: текст документа
        // экранирован на этапе конвертации, набор тегов фиксирован,
        // поэтому innerHTML здесь безопасен
        div.innerHTML = block.html;
        if (block.segments) {
            if (div.textContent === block.text) {
                weaveSegments(div, block.segments);
            } else {
                // HTML не соответствует тексту блока: fallback на plain text
                div.textContent = "";
                appendSegments(div, block.segments);
            }
        } else if (block.change) {
            div.classList.add(`change-${block.change}`);
        }
        return;
    }
    // Изменённый блок с пословным diff: подсвечиваем только различающиеся
    // слова по двухцветной модели (удалено/старая версия — красный,
    // добавлено/новая версия — зелёный), фон всего блока не заливаем
    if (block.segments) {
        appendSegments(div, block.segments);
        return;
    }
    div.textContent = block.text;
    if (block.change) div.classList.add(`change-${block.change}`);
}

function renderBlock(block, placeholderKind, side) {
    const div = document.createElement("div");
    div.className = "diff-block";
    if (block === null) {
        div.classList.add("placeholder");
        if (placeholderKind) div.classList.add(`placeholder-${placeholderKind}`);
        div.innerHTML = "&nbsp;";
        return div;
    }
    renderBlockContent(div, block);
    appendImages(div, block, side);
    return div;
}

// Строка результата относится к таблице: хотя бы одна сторона — строка
// таблицы (cells или sep), другая — тоже строка таблицы или пустое место
function isTableGroupRow(row) {
    const ok = (b) => b === null || "cells" in b || b.sep === true;
    const isTable = (b) => b !== null && ("cells" in b || b.sep === true);
    return ok(row.left) && ok(row.right) && (isTable(row.left) || isTable(row.right));
}

// Пословный diff ячеек вплетается в <td> по индексу; при расхождении
// содержимого ячейки с текстом — fallback на plain text сегменты
function weaveTableSegments(tr, block) {
    if (!block.cell_segments) return;
    const tds = tr.children;
    for (let c = 0; c < block.cell_segments.length && c < tds.length; c++) {
        const segs = block.cell_segments[c];
        if (!segs || segs.length === 0) continue;
        const td = tds[c];
        if (td.textContent === (block.cells[c] || "")) {
            weaveSegments(td, segs);
        } else {
            td.textContent = "";
            appendSegments(td, segs);
        }
    }
}

// Строки таблицы приходят отдельными блоками — собираем в одну
// HTML-таблицу. Служебная строка-разделитель (sep) пропускается.
// Разное число колонок дополняется пустыми ячейками
function renderTableSide(blocks, placeholderKind, side) {
    const div = document.createElement("div");
    div.className = "diff-block diff-table";
    const dataBlocks = blocks.filter((b) => b !== null && !b.sep);
    if (dataBlocks.length === 0) {
        div.classList.add("placeholder");
        if (placeholderKind) div.classList.add(`placeholder-${placeholderKind}`);
        div.innerHTML = "&nbsp;";
        return div;
    }
    // Число колонок: по ячейкам и по сегментам (сегменты включают ячейки
    // другой стороны — например метку добавленной колонки)
    const cols = Math.max(
        ...dataBlocks.map((b) => Math.max(b.cells.length, (b.cell_segments || []).length))
    );
    const table = document.createElement("table");
    dataBlocks.forEach((block) => {
        const tr = document.createElement("tr");
        // added/removed — подсветка всей строки; changed — пословно в ячейках
        if (block.change && !block.cell_segments) {
            tr.classList.add(`change-${block.change}`);
        }
        if (block.html) {
            // Converter-generated фрагмент <td>…</td>… (текст экранирован)
            tr.innerHTML = block.html;
            while (tr.children.length < cols) {
                tr.appendChild(document.createElement("td"));
            }
            weaveTableSegments(tr, block);
        } else {
            for (let c = 0; c < cols; c++) {
                const cellEl = document.createElement("td");
                if (block.cell_segments && block.cell_segments[c]) {
                    appendSegments(cellEl, block.cell_segments[c]);
                } else if (c < block.cells.length) {
                    cellEl.textContent = block.cells[c];
                }
                tr.appendChild(cellEl);
            }
        }
        table.appendChild(tr);
    });
    div.appendChild(table);
    for (const block of dataBlocks) appendImages(div, block, side);
    return div;
}

// Тип заглушки (null-сторона) по классу изменения противоположной стороны
function placeholderKind(other) {
    if (other && (other.change === "removed" || other.change === "added")) {
        return other.change;
    }
    return null;
}

// Тип заглушки табличной группы: класс изменения первого непустого блока
// противоположной стороны
function tablePlaceholderKind(otherBlocks) {
    const block = otherBlocks.find((b) => b !== null && b.change);
    return block ? block.change : null;
}

// Выравнивание высот парных блоков: обеим сторонам пары задаётся высота
// большей, иначе содержимое панелей разъезжается при синхронной прокрутке
function equalizeHeights() {
    const leftChildren = els.contentLeft.children;
    const rightChildren = els.contentRight.children;
    const count = Math.min(leftChildren.length, rightChildren.length);
    const pairs = [];
    for (let i = 0; i < count; i++) {
        const left = leftChildren[i];
        const right = rightChildren[i];
        left.style.minHeight = "";
        right.style.minHeight = "";
        pairs.push([left, right]);
    }
    // Сначала все измерения, потом все записи — без layout thrashing
    for (const [left, right] of pairs) {
        const height = Math.max(left.offsetHeight, right.offsetHeight);
        left.style.minHeight = `${height}px`;
        right.style.minHeight = `${height}px`;
    }
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
            const leftBlocks = group.map((row) => row.left);
            const rightBlocks = group.map((row) => row.right);
            els.contentLeft.appendChild(
                renderTableSide(leftBlocks, tablePlaceholderKind(rightBlocks), "left")
            );
            els.contentRight.appendChild(
                renderTableSide(rightBlocks, tablePlaceholderKind(leftBlocks), "right")
            );
            i = j;
        } else {
            els.contentLeft.appendChild(
                renderBlock(rows[i].left, placeholderKind(rows[i].right), "left")
            );
            els.contentRight.appendChild(
                renderBlock(rows[i].right, placeholderKind(rows[i].left), "right")
            );
            i++;
        }
    }
    if (!result.semantic) els.degraded.hidden = false;
    els.diff.hidden = false;
    // Измеряем только после того, как панели стали видимыми
    equalizeHeights();
}

// Синхронный скролл панелей по обеим осям.
//
// Запись scrollTop/scrollLeft в другую панель вызывает событие прокрутки
// в ней. У широких таблиц scrollWidth панелей различается, поэтому целевая
// панель обрезает присланное смещение, а её эхо-событие возвращает
// источнику уже обрезанное значение — панель отскакивает назад у края.
// Поэтому последнее записанное в каждую панель смещение запоминается:
// событие, совпадающее с собственной записью, игнорируется.
const lastWritten = new WeakMap();
function syncScroll(source, target) {
    source.addEventListener("scroll", () => {
        if (lastWritten.get(source) === source.scrollLeft) {
            lastWritten.delete(source);
            return;
        }
        target.scrollTop = source.scrollTop;
        target.scrollLeft = source.scrollLeft;
        lastWritten.set(target, target.scrollLeft);
    });
}

setupUploadZone(1);
setupUploadZone(2);
els.compare.addEventListener("click", startCompare);
syncScroll(els.panelLeft, els.panelRight);
syncScroll(els.panelRight, els.panelLeft);

// При изменении ширины окна переносы строк меняются — выравниваем заново
window.addEventListener("resize", () => {
    if (!els.diff.hidden) equalizeHeights();
});

// После догрузки изображений высоты блоков меняются — выравниваем заново
// (событие load не всплывает, поэтому слушатель в фазе capture)
els.diff.addEventListener(
    "load",
    (event) => {
        if (event.target.tagName === "IMG" && !els.diff.hidden) equalizeHeights();
    },
    true
);
