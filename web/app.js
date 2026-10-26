// Plain JS front-end. Model output is untrusted, so everything is rendered with
// textContent / form values — never innerHTML.
const $ = (id) => document.getElementById(id);
const CATEGORIES = ["food", "transport", "shopping", "bills", "health", "education",
  "entertainment", "transfer", "salary", "other"];
const FIELDS = ["date", "time", "amount", "fee", "direction", "category", "counterparty", "bank", "ref_no", "memo", "source"];
const MAX_FILES = 20;
const PARALLEL = 2; // stay well under the proxy's rate limit

async function api(path, options = {}) {
  const r = await fetch(path, options);
  const body = r.status === 204 ? null : await r.json();
  if (!r.ok) throw new Error(body?.detail ? JSON.stringify(body.detail) : r.statusText);
  return body;
}

// 1. upload several images -> one editable draft card per image
function newDraftCard(title, file) {
  const form = $("draft-template").content.firstElementChild.cloneNode(true);
  form.category.append(...CATEGORIES.map((c) => new Option(c, c)));
  form.querySelector(".fname").textContent = title;
  const img = form.querySelector(".thumb");
  if (file) {
    img.src = URL.createObjectURL(file);
    img.onload = () => URL.revokeObjectURL(img.src);
  } else {
    img.hidden = true;
  }
  form.querySelector(".status").textContent = "รอคิว…";
  setDisabled(form, true);
  form.onsubmit = (ev) => { ev.preventDefault(); saveDraft(form); };
  form.querySelector(".skip").onclick = () => { form.remove(); updateBatchActions(); };
  $("drafts").append(form);
  updateBatchActions();
  return form;
}

const setDisabled = (form, on) => form.querySelectorAll("input, select, button").forEach((el) => (el.disabled = on));

// Put a /api/extract or /api/extract-text result into its card.
function fillCard(form, res) {
  const d = res.draft || {};
  for (const k of FIELDS) form[k].value = d[k] ?? (k === "fee" ? "0" : k === "source" ? "slip" : "");
  form.querySelector(".raw").textContent = JSON.stringify(res.raw, null, 2);
  form.querySelector(".errors").textContent = res.errors.length ? "ตรวจสอบ: " + res.errors.join("; ") : "";
  setStatus(form, res.draft ? "พร้อมบันทึก — ตรวจข้อมูลก่อน" : "อ่านได้ไม่ครบ กรอกช่องที่ขาดเอง", res.draft ? null : "failed");
  form.dataset.ready = res.draft ? "1" : "";
}

function setStatus(form, text, cls) {
  form.querySelector(".status").textContent = text;
  form.classList.remove("saved", "failed");
  if (cls) form.classList.add(cls);
}

async function extractInto(form, file) {
  setStatus(form, "กำลังอ่าน…");
  const fd = new FormData();
  fd.append("image", file);
  try {
    fillCard(form, await api("/api/extract", { method: "POST", body: fd }));
  } catch (e) {
    setStatus(form, "อ่านไม่ได้: " + e.message, "failed");
  }
  setDisabled(form, false);
}

async function saveDraft(form) {
  if (form.classList.contains("saved")) return true;
  if (!form.reportValidity()) return false;
  const data = Object.fromEntries(new FormData(form));
  for (const k of ["time", "bank", "ref_no", "memo"]) if (!data[k]) data[k] = null;
  try {
    await api("/api/transactions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    setStatus(form, "บันทึกแล้ว ✓", "saved");
    setDisabled(form, true);
    form.querySelector(".errors").textContent = "";
    loadRows();
    return true;
  } catch (e) {
    form.querySelector(".errors").textContent = "บันทึกไม่ได้: " + e.message;
    return false;
  }
}

function updateBatchActions() {
  $("batch-actions").hidden = $("drafts").children.length === 0;
}

$("extract").onclick = async () => {
  let files = [...$("files").files];
  if (!files.length) return;
  if (files.length > MAX_FILES) files = files.slice(0, MAX_FILES);
  const jobs = files.map((f) => [newDraftCard(f.name, f), f]);
  $("extract").disabled = true;
  $("files").value = "";
  let done = 0;
  const status = () => ($("extract-status").textContent =
    `อ่านแล้ว ${done} / ${jobs.length} รูป` + (files.length === MAX_FILES ? ` (จำกัด ${MAX_FILES} รูปต่อครั้ง)` : ""));
  status();
  const queue = [...jobs];
  const worker = async () => {
    while (queue.length) {
      const [form, file] = queue.shift();
      await extractInto(form, file);
      done++;
      status();
    }
  };
  await Promise.all(Array.from({ length: PARALLEL }, worker));
  $("extract").disabled = false;
};

// Typed entry: the model turns one line into a draft card, same review flow as a slip.
$("parse-text").onclick = async () => {
  const text = $("typed").value.trim();
  if (!text) return;
  const form = newDraftCard(`พิมพ์: ${text}`, null);
  setStatus(form, "กำลังแปลง…");
  $("parse-text").disabled = true;
  try {
    fillCard(form, await api("/api/extract-text", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    }));
    $("typed").value = "";
  } catch (e) {
    setStatus(form, "แปลงไม่ได้: " + e.message, "failed");
  } finally {
    setDisabled(form, false);
    $("parse-text").disabled = false;
  }
};
$("typed").onkeydown = (e) => { if (e.key === "Enter") $("parse-text").click(); };

// A blank card for entering everything by hand — no model call at all.
$("blank-form").onclick = () => {
  const form = newDraftCard("กรอกเอง", null);
  form.date.value = new Date().toLocaleDateString("sv-SE"); // YYYY-MM-DD, local time
  form.direction.value = "expense";
  form.category.value = "other";
  form.fee.value = "0";
  form.source.value = "manual";
  setStatus(form, "กรอกข้อมูลแล้วกดบันทึก");
  setDisabled(form, false);
  form.dataset.ready = "1";
  form.counterparty.focus();
};

// Save every draft that read cleanly; leave the ones needing attention.
$("save-all").onclick = async () => {
  const forms = [...$("drafts").querySelectorAll("form.draft:not(.saved)")].filter((f) => f.dataset.ready);
  let ok = 0;
  for (const f of forms) ok += (await saveDraft(f)) ? 1 : 0;
  $("extract-status").textContent = `บันทึก ${ok} / ${forms.length} รายการ` +
    (ok < forms.length ? " — ที่เหลือดูข้อความในการ์ด" : "");
};

$("clear-drafts").onclick = () => {
  $("drafts").replaceChildren();
  $("extract-status").textContent = "";
  updateBatchActions();
};

// 2. ask the agent
$("ask").onclick = async () => {
  const question = $("question").value.trim();
  if (!question) return;
  $("ask").disabled = true;
  $("answer").textContent = "กำลังคิด…";
  try {
    const res = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
    });
    $("answer").textContent = res.answer;
    const ol = $("steps");
    ol.replaceChildren(...res.steps.map((s) => {
      const li = document.createElement("li");
      const q = document.createElement("code");
      q.textContent = s.action;
      const o = document.createElement("pre");
      o.textContent = s.observation;
      li.append(q, o);
      return li;
    }));
    $("steps-box").hidden = res.steps.length === 0;
  } catch (e) {
    $("answer").textContent = "ผิดพลาด: " + e.message;
  } finally {
    $("ask").disabled = false;
  }
};
$("question").onkeydown = (e) => { if (e.key === "Enter") $("ask").click(); };

// 3. recent transactions
const baht = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2 });
async function loadRows() {
  const rows = await api("/api/transactions?limit=50");
  $("rows").replaceChildren(...rows.map((t) => {
    const tr = document.createElement("tr");
    const sign = t.direction === "income" ? "+" : t.direction === "expense" ? "−" : "";
    const cells = [t.date, t.counterparty, t.category, sign + baht.format(t.amount)];
    cells.forEach((v, i) => {
      const td = document.createElement("td");
      td.textContent = v;
      if (i === 3) td.className = "num " + t.direction;
      tr.append(td);
    });
    const del = document.createElement("button");
    del.textContent = "ลบ";
    del.title = "ลบรายการนี้";
    del.onclick = async () => { await api(`/api/transactions/${t.id}`, { method: "DELETE" }); loadRows(); };
    const td = document.createElement("td");
    td.append(del);
    tr.append(td);
    return tr;
  }));
}
loadRows();
