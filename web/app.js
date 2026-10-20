// Plain JS front-end. Model output is untrusted, so everything is rendered with
// textContent / form values — never innerHTML.
const $ = (id) => document.getElementById(id);
const CATEGORIES = ["food", "transport", "shopping", "bills", "health", "education",
  "entertainment", "transfer", "salary", "other"];
const form = $("draft");

form.category.append(...CATEGORIES.map((c) => new Option(c, c)));

async function api(path, options = {}) {
  const r = await fetch(path, options);
  const body = r.status === 204 ? null : await r.json();
  if (!r.ok) throw new Error(body?.detail ? JSON.stringify(body.detail) : r.statusText);
  return body;
}

// 1. upload -> draft
$("extract").onclick = async () => {
  const file = $("file").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("image", file);
  $("extract").disabled = true;
  $("extract-status").textContent = "กำลังอ่านสลิป…";
  try {
    const res = await api("/api/extract", { method: "POST", body: fd });
    const d = res.draft || {};
    for (const k of ["date", "time", "amount", "fee", "direction", "category", "counterparty", "bank", "ref_no", "memo"]) {
      form[k].value = d[k] ?? (k === "fee" ? "0" : "");
    }
    $("raw").textContent = JSON.stringify(res.raw, null, 2);
    $("draft-errors").textContent = res.errors.length ? "ตรวจสอบ: " + res.errors.join("; ") : "";
    $("extract-status").textContent = res.draft ? "ตรวจข้อมูลแล้วกดบันทึก" : "อ่านได้ไม่ครบ กรอกช่องที่ขาดเองได้";
    form.hidden = false;
  } catch (e) {
    $("extract-status").textContent = "ผิดพลาด: " + e.message;
  } finally {
    $("extract").disabled = false;
  }
};

// save the (possibly edited) draft
form.onsubmit = async (ev) => {
  ev.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  for (const k of ["time", "bank", "ref_no", "memo"]) if (!data[k]) data[k] = null;
  try {
    await api("/api/transactions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    form.hidden = true;
    $("extract-status").textContent = "บันทึกแล้ว ✓";
    loadRows();
  } catch (e) {
    $("draft-errors").textContent = "บันทึกไม่ได้: " + e.message;
  }
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
