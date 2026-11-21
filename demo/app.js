// Farm-to-Shelf Beer Game demo.
// Loads the project's Python model with Pyodide and draws the results with Chart.js.

const REPO_URL = "https://github.com/careneA/farm-beer-game";   // set at publish time
const MODEL_FILES = ["__init__.py", "sim.py", "agents.py", "metrics.py", "scenarios.py", "negotiation.py"];

const PRESETS = [
  { id: "bullwhip", label: "1. The bullwhip", a: "baseline", b: "info_sharing", demand: "noisy", log: true,
    text: "<strong>Each player sees only its neighbor's orders (A), so small swings in customer demand grow at every step toward the farm.</strong> When everyone sees real customer demand (B), the factory's orders track demand closely. The first chart uses a log scale so both lines fit." },
  { id: "hog", label: "2. The hog cycle", a: "info_sharing", b: "contract_floor_share", demand: "noisy",
    text: "<strong>Sharing data calms orders, but not the farmer's income.</strong> Farmers still plant for today's price, so prices boom and crash (A). A contract with a committed quantity (B) keeps every season profitable. Move \"Farmers plant for today's price\" to see the cycle grow." },
  { id: "power", label: "3. Bargaining power", a: "neg_smallholder", b: "neg_cooperative", demand: "noisy",
    text: "<strong>Same chain, same contract type, very different split.</strong> A smallholder who needs a deal fast concedes early (A). A cooperative can afford to hold out (B) and gets a better price. Click \"Average 30 runs\" to compare fairly." },
  { id: "farmgate", label: "4. Fixed farmgate price", a: "government_farmgate", b: "contract_floor_share", demand: "noisy",
    text: "<strong>Fixing the price without committing to a quantity doesn't protect farmers.</strong> They still plant for a price set from last season (A), and a middleman keeps the gap to the market price. Lower the farmgate percentage and watch shortages appear." },
];

const KPIS = [
  { key: "bullwhip_factory", label: "Bullwhip at factory (order swing ÷ demand swing)", fmt: v => v.toLocaleString(undefined, { maximumFractionDigits: 1 }) + "×", better: "low" },
  { key: "empty_shelf_weeks_pct", label: "Weeks with empty shelves", fmt: v => v.toFixed(1) + "%", better: "low" },
  { key: "farm_income", label: "Farmer's income, 4 years", fmt: v => Math.round(v).toLocaleString() },
  { key: "worst_season_income_x", label: "Farmer's worst season (× normal profit)", fmt: v => v.toFixed(2), better: "high" },
  { key: "farmer_avg_price", label: "Farmer's average price", fmt: v => v.toFixed(2) },
  { key: "factory_crop_spend", label: "What the buyer pays for crop", fmt: v => Math.round(v).toLocaleString() },
];

const $ = id => document.getElementById(id);
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
let py, runJson, charts = {}, demandKind = "noisy", busy = false;

async function boot() {
  try {
    $("status-detail").textContent = "Starting Python…";
    py = await loadPyodide();
    $("status-detail").textContent = "Loading numpy…";
    await py.loadPackage("numpy");
    $("status-detail").textContent = "Loading the model…";
    py.FS.mkdirTree("/home/pyodide/beergame");
    for (const f of MODEL_FILES) {
      const res = await fetch(`../beergame/${f}`);
      if (!res.ok) throw new Error(`could not load beergame/${f} (${res.status})`);
      py.FS.writeFile(`/home/pyodide/beergame/${f}`, await res.text());
    }
    const bridge = await (await fetch("bridge.py")).text();
    py.runPython(bridge);
    runJson = py.globals.get("run_json");
    const setups = JSON.parse(py.globals.get("setups_json")());
    for (const sel of [$("setupA"), $("setupB")]) {
      sel.innerHTML = setups.map(s => `<option value="${s.id}">${s.label}</option>`).join("");
    }
    buildPresets();
    wire();
    applyPreset(PRESETS[0]);
    $("status").classList.add("hidden");
  } catch (err) {
    $("status").innerHTML = `<div><p><strong>The model couldn't load.</strong></p><p class="hint">${err.message}</p></div>`;
    console.error(err);
  }
}

function buildPresets() {
  $("presets").innerHTML = PRESETS.map(p =>
    `<button data-id="${p.id}" aria-pressed="false">${p.label}</button>`).join("");
  $("presets").querySelectorAll("button").forEach(btn =>
    btn.addEventListener("click", () => applyPreset(PRESETS.find(p => p.id === btn.dataset.id))));
}

function applyPreset(p) {
  $("setupA").value = p.a;
  $("setupB").value = p.b;
  setDemand(p.demand);
  $("logScale").checked = !!p.log;
  $("finding").innerHTML = p.text;
  $("presets").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b.dataset.id === p.id));
  update();
}

function setDemand(v) {
  demandKind = v;
  $("demand").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === v));
}

function wire() {
  const sliders = { priceResponse: v => (+v).toFixed(2), yieldSd: v => "±" + Math.round(v * 100) + "%",
                    floor: v => (+v).toFixed(2), upside: v => Math.round(v * 100) + "%",
                    farmgate: v => Math.round(v * 100) + "%" };
  for (const [id, fmt] of Object.entries(sliders)) {
    const el = $(id), out = $(id + "Out");
    const show = () => { out.textContent = fmt(el.value); };
    show();
    el.addEventListener("input", show);
    el.addEventListener("change", update);
  }
  $("setupA").addEventListener("change", manual);
  $("setupB").addEventListener("change", manual);
  $("seed").addEventListener("change", update);
  $("demand").querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => { setDemand(b.dataset.v); update(); }));
  $("reroll").addEventListener("click", () => { $("seed").value = Math.floor(Math.random() * 10000); update(); });
  $("many").addEventListener("click", () => update(30));
  $("logScale").addEventListener("change", () => { charts.orders.options.scales.y.type =
    $("logScale").checked ? "logarithmic" : "linear"; charts.orders.update(); });
  document.querySelectorAll("a[href='../']").forEach(a => a.href = REPO_URL);
  document.querySelectorAll("a[href='../REPORT.md']").forEach(a => a.href = REPO_URL + "/blob/main/REPORT.md");
}

function manual() {
  $("presets").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", "false"));
  $("finding").innerHTML = "Your own comparison. Pick a numbered finding above to go back to a guided view.";
  update();
}

function params() {
  return [demandKind, +$("seed").value || 0, +$("priceResponse").value, +$("yieldSd").value,
          +$("floor").value, +$("upside").value, +$("farmgate").value];
}

function update(manySeeds = 0) {
  if (busy) return;
  busy = true;
  $("busy").textContent = manySeeds ? "Running 30 random runs for each setup (about 15 seconds)…" : "Running…";
  document.querySelectorAll(".actions button").forEach(b => b.disabled = true);
  // Let the browser paint the "Running…" message before Python blocks the page.
  setTimeout(() => {
    try {
      const a = JSON.parse(runJson($("setupA").value, ...params(), manySeeds));
      const b = JSON.parse(runJson($("setupB").value, ...params(), manySeeds));
      render(a, b, manySeeds);
    } catch (err) {
      $("busy").textContent = "Something went wrong: " + err.message;
      console.error(err);
    } finally {
      busy = false;
      document.querySelectorAll(".actions button").forEach(b => b.disabled = false);
      if (!$("busy").textContent.startsWith("Something")) $("busy").textContent = "";
    }
  }, 30);
}

function render(a, b, manySeeds) {
  const src = manySeeds ? "median" : "metrics";
  $("kpis").querySelector("tbody").innerHTML = KPIS.map(k => {
    const va = a[src][k.key], vb = b[src][k.key];
    const best = k.better && va !== vb ? ((k.better === "low") === (va < vb) ? "a" : "b") : null;
    return `<tr><td>${k.label}</td><td class="${best === "a" ? "better" : ""}">${k.fmt(va)}</td>` +
           `<td class="${best === "b" ? "better" : ""}">${k.fmt(vb)}</td></tr>`;
  }).join("");
  $("kpiNote").textContent = manySeeds
    ? "Median of 30 random runs. Bold marks the better value where better is clear-cut."
    : "One random run. Use \"Average 30 runs\" for steadier numbers. Bold marks the better value where better is clear-cut.";

  const dealText = (r, name) => r.deals.length
    ? `${name}: ` + r.deals.map(d => d.floor === null ? "no deal"
        : d.share === 0 ? `fixed price ${Math.max(d.floor, 1).toFixed(2)}`
        : `${Math.round(d.share * 100)}% of market moves, never below ${d.floor.toFixed(2)}`).join(" · ")
    : "";
  $("deals").innerHTML = [dealText(a, "A's negotiated deals by season"), dealText(b, "B's negotiated deals by season")]
    .filter(Boolean).join("<br>");

  const weeks = a.demand.map((_, i) => i);
  drawLine("orders", "ordersChart", weeks, [
    { label: "Customer demand", data: a.demand, color: css("--demand"), width: 1.2 },
    { label: "A: factory orders", data: a.factory_orders, color: css("--a"), width: 2 },
    { label: "B: factory orders", data: b.factory_orders, color: css("--b"), width: 2 },
  ], "Units per week");
  drawLine("price", "priceChart", weeks, [
    { label: "A: farmgate price", data: a.price, color: css("--a"), width: 2 },
    { label: "B: farmgate price", data: b.price, color: css("--b"), width: 2 },
  ], "Price (1.0 = balanced)");
  drawBars(a.season_income, b.season_income);
}

function baseOptions(yTitle) {
  const ink2 = css("--ink2"), grid = css("--grid"), muted = css("--muted");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: ink2, boxWidth: 12, boxHeight: 12 } },
               tooltip: { callbacks: { label: c => `${c.dataset.label}: ${(+c.parsed.y).toLocaleString(undefined, { maximumFractionDigits: 2 })}` } } },
    scales: { x: { ticks: { color: muted, maxTicksLimit: 9 }, grid: { display: false },
                   title: { display: true, text: "Week", color: ink2 } },
              y: { ticks: { color: muted }, grid: { color: grid },
                   title: { display: true, text: yTitle, color: ink2 } } },
  };
}

function drawLine(key, canvasId, labels, series, yTitle) {
  const datasets = series.map(s => ({ label: s.label, data: s.data, borderColor: s.color,
    backgroundColor: s.color, borderWidth: s.width, pointRadius: 0, pointHoverRadius: 4, tension: 0.15 }));
  if (!charts[key]) {
    charts[key] = new Chart($(canvasId), { type: "line", data: { labels, datasets }, options: baseOptions(yTitle) });
  } else {
    charts[key].data.labels = labels;
    charts[key].data.datasets = datasets;
  }
  if (key === "orders") charts[key].options.scales.y.type = $("logScale").checked ? "logarithmic" : "linear";
  charts[key].update();
}

function drawBars(incA, incB) {
  const labels = incA.map((_, i) => `Season ${i + 2}`);
  const datasets = [
    { label: "A", data: incA, backgroundColor: css("--a"), borderRadius: 4, borderColor: css("--surface"), borderWidth: 2 },
    { label: "B", data: incB, backgroundColor: css("--b"), borderRadius: 4, borderColor: css("--surface"), borderWidth: 2 },
  ];
  if (charts.income) {
    charts.income.data.labels = labels;
    charts.income.data.datasets = datasets;
    charts.income.update();
    return;
  }
  const opts = baseOptions("Profit");
  opts.scales.x.title.text = "Season (the first is skipped: its crop was planted before the game)";
  charts.income = new Chart($("incomeChart"), { type: "bar", data: { labels, datasets }, options: opts });
}

boot();
