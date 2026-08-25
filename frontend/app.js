/* TabForge frontend: загрузка -> опрос задачи -> вывод табов. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const fileInput = $("#file");
const drop = $("#drop");
const goBtn = $("#go");
const neck = $("#neck");
const emptyBox = $("#empty");
const logEl = $("#log");
const resultsEl = $("#results");

let pickedFile = null;

/* ---------- выбор файла ---------- */

fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

["dragover", "dragenter"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

function setFile(f) {
  if (!f) return;
  pickedFile = f;
  $("#fileName").textContent = f.name;
  goBtn.disabled = false;
}

/* ---------- запуск задачи ---------- */

goBtn.addEventListener("click", async () => {
  if (!pickedFile) return;
  goBtn.disabled = true;
  resultsEl.innerHTML = "";
  emptyBox.hidden = true;
  neck.hidden = false;
  neck.classList.add("playing");
  setLog("Загрузка файла на обработку…");
  markStage(null);

  const stems = [...document.querySelectorAll('input[name="stem"]:checked')]
    .map((c) => c.value).join(",");

  const form = new FormData();
  form.append("file", pickedFile);

  const params = new URLSearchParams({
    stems,
    tuning: $("#tuning").value,
    separate: $("#separate").checked,
  });

  try {
    const res = await fetch(`/api/jobs?${params}`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const { id } = await res.json();
    poll(id);
  } catch (err) {
    fail(`Не удалось запустить: ${err.message}`);
  }
});

/* ---------- опрос ---------- */

async function poll(id) {
  try {
    const res = await fetch(`/api/jobs/${id}`);
    if (!res.ok) throw new Error("задача потерялась");
    const job = await res.json();

    markStage(job.stage, job.stages);
    if (job.log.length) setLog(job.log[job.log.length - 1]);

    if (job.status === "done") return finish(job);
    if (job.status === "error") return fail(job.error);
    setTimeout(() => poll(id), 1500);
  } catch (err) {
    fail(err.message);
  }
}

function markStage(current, stages = []) {
  const items = document.querySelectorAll("#frets li");
  let seen = false;
  items.forEach((li) => {
    const s = li.dataset.stage;
    li.classList.remove("active", "done");
    if (s === current) { li.classList.add("active"); seen = true; }
    else if (!seen && current) { li.classList.add("done"); }
  });
  if (current === "done") items.forEach((li) => li.classList.add("done"));
}

function setLog(msg, isError = false) {
  logEl.textContent = msg;
  logEl.classList.toggle("error", isError);
}

function fail(msg) {
  neck.classList.remove("playing");
  setLog(msg, true);
  goBtn.disabled = false;
}

/* ---------- результат ---------- */

const STEM_RU = { guitar: "Гитара", bass: "Бас", vocals: "Вокал",
                  piano: "Клавиши", other: "Прочее", mix: "Весь микс" };

function finish(job) {
  neck.classList.remove("playing");
  markStage("done");
  setLog("Готово. Файлы можно скачать ниже.");
  goBtn.disabled = false;

  if (!job.results.length) {
    setLog("Обработка прошла, но нот не нашлось. Попробуйте другие партии.", true);
    return;
  }

  const tpl = $("#stemCard");
  for (const r of job.results) {
    const card = tpl.content.cloneNode(true);
    card.querySelector(".stem-name").textContent = STEM_RU[r.stem] || r.stem;
    card.querySelector(".stem-meta").textContent =
      `${r.notes} нот · ${r.bpm} BPM`;

    const nav = card.querySelector(".stem-downloads");
    for (const [ext, url] of Object.entries(r.files)) {
      const a = document.createElement("a");
      a.href = url;
      a.textContent = `.${ext}`;
      a.setAttribute("download", "");
      nav.appendChild(a);
    }

    card.querySelector(".asciitab").textContent = r.ascii;

    const atEl = card.querySelector(".alphatab");
    resultsEl.appendChild(card);

    // Нотный стан + таб рендерит alphaTab из .gp5, если он собрался
    if (r.files.gp5 && window.alphaTab) {
      atEl.hidden = false;
      try {
        new alphaTab.AlphaTabApi(atEl, {
          file: r.files.gp5,
          display: { staveProfile: "ScoreTab" },
          player: { enablePlayer: false },
        });
      } catch (e) {
        atEl.hidden = true;   // остаётся ASCII-таб
      }
    }
  }
}
