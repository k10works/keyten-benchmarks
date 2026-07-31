// Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
// The board: loads results/index.json plus one JSON per engine and renders a
// summary (common-subset totals) and a per-query table with relative bars.

const COLORS = { keyten: "var(--c-keyten)", duckdb: "var(--c-duckdb)", polars: "var(--c-polars)" };
const RESULTS = "../results";

const $ = (sel) => document.querySelector(sel);

async function main() {
  const index = await (await fetch(`${RESULTS}/index.json`)).json();
  const tabs = $("#tabs");
  index.suites.forEach((suite, i) => {
    const b = document.createElement("button");
    b.textContent = suite.title;
    b.setAttribute("role", "tab");
    b.onclick = () => select(index, suite, b);
    tabs.appendChild(b);
    if (i === 0) select(index, suite, b);
  });
}

async function select(index, suite, button) {
  for (const b of $("#tabs").children) b.setAttribute("aria-selected", b === button);
  const runs = await Promise.all(
    suite.engines.map((e) => fetch(`${RESULTS}/${suite.id}/${e}.json`).then((r) => r.json()))
  );
  render(suite, runs);
}

function render(suite, runs) {
  const machine = runs[0].machine;
  $("#meta").innerHTML =
    `<b>${esc(suite.dataset)}</b> — ${esc(suite.note)}<br>` +
    `${esc(machine.cpu)}, ${machine.cores} cores, ${machine.ram_gb} GB RAM, ${esc(machine.os)} · ` +
    runs.map((r) => `${esc(r.engine)} ${esc(r.version)}`).join(" · ") +
    ` · ${esc(machine.date)}`;

  // The honest comparison: totals over the queries EVERY engine completed.
  const byEngine = runs.map((r) => new Map(r.queries.map((q) => [q.idx, q])));
  const common = [...byEngine[0].keys()].filter((idx) => byEngine.every((m) => m.has(idx)));
  const totals = runs.map((r, i) => ({
    engine: r.engine,
    total: common.reduce((s, idx) => s + byEngine[i].get(idx).ms, 0),
  }));
  const best = Math.min(...totals.map((t) => t.total));

  $("#summary").innerHTML = totals
    .map(
      (t) => `
    <div class="card${t.total === best ? " best" : ""}">
      <div class="eng"><i style="background:${COLORS[t.engine]}"></i>${esc(t.engine)}</div>
      <div class="val">${(t.total / 1000).toFixed(2)} s</div>
      <div class="rel">${t.total === best ? "fastest" : "×" + (t.total / best).toFixed(2) + " vs fastest"}
        · ${common.length} common queries</div>
    </div>`
    )
    .join("");

  // Per-query table. Bars scale to the slowest engine on that query.
  const allIdx = [...new Set(runs.flatMap((r) => r.queries.map((q) => q.idx)))].sort((a, b) => a - b);
  const head =
    `<tr><th>#</th>` +
    runs.map((r) => `<th><span class="chip" style="background:${COLORS[r.engine]}"></span>${esc(r.engine)} (ms)</th>`).join("") +
    `</tr>`;
  const rows = allIdx
    .map((idx) => {
      const cells = byEngine.map((m) => m.get(idx));
      const max = Math.max(...cells.filter(Boolean).map((q) => q.ms));
      const min = Math.min(...cells.filter(Boolean).map((q) => q.ms));
      const query = (cells.find(Boolean) || {}).query || "";
      const tds = cells
        .map((q, i) => {
          if (!q) return `<td class="cell gap">—</td>`;
          const w = Math.max(2, Math.round((q.ms / max) * 120));
          const bestCls = q.ms === min ? " best" : "";
          return `<td class="cell"><div class="barrow">
            <span class="bar" style="width:${w}px;background:${COLORS[runs[i].engine]}"></span>
            <span class="ms${bestCls}">${fmt(q.ms)}</span></div></td>`;
        })
        .join("");
      return `<tr data-q="${esc(query)}"><td class="q">${idx}</td>${tds}</tr>`;
    })
    .join("");
  $("#detail").innerHTML = `<div class="tblwrap"><table>
    <thead>${head}</thead><tbody>${rows}</tbody></table></div>`;

  // Row hover: the query text as a tooltip.
  const tip = $("#tip");
  for (const tr of $("#detail").querySelectorAll("tbody tr")) {
    tr.addEventListener("mousemove", (e) => {
      const q = tr.dataset.q;
      if (!q) return;
      tip.textContent = q;
      tip.hidden = false;
      const pad = 14;
      const w = tip.offsetWidth;
      tip.style.left = Math.min(e.clientX + pad, innerWidth - w - pad) + "px";
      tip.style.top = e.clientY + pad + "px";
    });
    tr.addEventListener("mouseleave", () => (tip.hidden = true));
  }
}

const fmt = (ms) => (ms >= 100 ? Math.round(ms).toString() : ms.toFixed(1));
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

main();
