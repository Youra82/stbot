import json, os

HERE = os.path.dirname(__file__)
agg_best = json.load(open(os.path.join(HERE, "trial_convergence_agg.json")))
agg_strict = json.load(open(os.path.join(HERE, "trial_convergence_agg_strict.json")))

TEMPLATE = r"""<title>stbot — Trial-Konvergenz-Studie</title>
<style>
  .viz-root {
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --gridline: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --blue: #2a78d6;
    --s1: #2a78d6; --s1-wash: rgba(42,120,214,0.12);
    --s2: #eb6834; --s2-wash: rgba(235,104,52,0.12);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --gridline: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
      --blue: #3987e5;
      --s1: #3987e5; --s1-wash: rgba(57,135,229,0.16);
      --s2: #d95926; --s2-wash: rgba(217,89,38,0.16);
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --gridline: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --blue: #3987e5;
    --s1: #3987e5; --s1-wash: rgba(57,135,229,0.16);
    --s2: #d95926; --s2-wash: rgba(217,89,38,0.16);
  }
  * { box-sizing: border-box; }
  body { margin: 0; }
  .viz-root { background: var(--page); color: var(--text-primary); padding: 24px 20px 60px; min-height: 100vh; }
  .wrap { max-width: 980px; margin: 0 auto; }
  h1 { font-size: 1.35rem; margin: 0 0 4px; }
  .subtitle { color: var(--text-secondary); font-size: 0.9rem; margin: 0 0 4px; line-height: 1.5; }
  .caveat { color: var(--text-muted); font-size: 0.8rem; margin: 10px 0 18px; border-left: 2px solid var(--baseline); padding-left: 10px; line-height: 1.55; }
  .legend { display: flex; gap: 18px; align-items: center; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px; flex-wrap: wrap; }
  .legend .key { display: flex; align-items: center; gap: 6px; }
  .legend .swatch { width: 16px; height: 2px; border-radius: 1px; }

  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin: 6px 0 18px; }
  .seg button { font: inherit; font-size: 0.82rem; padding: 7px 14px; background: transparent; color: var(--text-secondary); border: none; cursor: pointer; border-right: 1px solid var(--border); }
  .seg button:last-child { border-right: none; }
  .seg button.active { background: var(--blue); color: #fff; }
  .seg button:hover:not(.active) { background: var(--gridline); }

  .panel { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px 14px; margin-bottom: 18px; }
  .panel h2 { font-size: 0.95rem; margin: 0 0 2px; }
  .panel .meta { font-size: 0.76rem; color: var(--text-muted); margin-bottom: 10px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .gridline { stroke: var(--gridline); stroke-width: 1; }
  .baseline { stroke: var(--baseline); stroke-width: 1; }
  .axislabel { fill: var(--text-muted); font-size: 10px; }
  .band { opacity: 1; }
  .meanline { fill: none; stroke-width: 2; }
  .hoverline { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0; }
  .hitrect { fill: transparent; cursor: crosshair; }

  .tooltip {
    position: fixed; pointer-events: none; z-index: 50;
    background: var(--text-primary); color: var(--surface-1);
    font-size: 0.74rem; line-height: 1.6; padding: 8px 10px; border-radius: 6px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.25); opacity: 0; transition: opacity 0.08s; max-width: 220px;
  }
  .tooltip.show { opacity: 1; }
  .tooltip b { font-weight: 700; }
  .stat-row { display: flex; gap: 22px; margin-top: 10px; font-size: 0.78rem; }
  .stat { color: var(--text-secondary); }
  .stat b { color: var(--text-primary); font-variant-numeric: tabular-nums; }
  footer { color: var(--text-muted); font-size: 0.74rem; margin-top: 6px; }
</style>

<div class="viz-root">
  <div class="wrap">
    <h1>Trial-Konvergenz-Studie — wie viele Optuna-Trials sind noetig?</h1>
    <p class="subtitle">
      Bester bisher gefundener PnL% (y) je Trial-Nummer (x), gemittelt ueber 3 Zufalls-Seeds (Band = Min/Max ueber die Seeds).
      Backtester mit echtem SL + echtem Trailing-Stop (Fix vom 30.07.2026).
    </p>

    <div class="seg" id="modeSeg">
      <button data-mode="best_profit" class="active">best_profit (kein Winrate-Gate)</button>
      <button data-mode="strict">strict (min_win_rate ≥ 35%)</button>
    </div>

    <p class="caveat" id="caveatText"></p>
    <div class="legend">
      <span class="key"><span class="swatch" style="background:var(--s1)"></span>mit risk_reward_ratio (11 Parameter)</span>
      <span class="key"><span class="swatch" style="background:var(--s2)"></span>ohne risk_reward_ratio (10 Parameter, seit Backtester-Fix wirkungslos)</span>
    </div>

    __PANELS__

    <footer id="footerText"></footer>
  </div>
</div>

<div class="tooltip" id="tooltip"></div>

<script>
const MODES = __MODES_DATA__;

const CAVEATS = {
  best_profit: "best_profit-Modus (nur MaxDD&lt;30%, min. 20 Trades, kein Winrate-Gate). Die absoluten PnL%-Werte hier (teils &gt;500% über 2-3 Jahre Historie) sind Ergebnis eines unbeschränkten Laufs ohne Out-of-Sample-Prüfung und Ausreisser-getrieben (grosse Streuung zwischen Seeds) — nur als relatives Konvergenz-Signal zu lesen, nicht als realistische Live-Erwartung.",
  strict: "strict-Modus (MaxDD&lt;30%, min_win_rate&ge;35%, min_pnl&ge;0%, min. 20 Trades). Robustheits-Constraint aktiv — sollte weniger Ausreisser-getrieben sein als best_profit."
};
const FOOTERS = {
  best_profit: "Quelle: daten/trial_convergence_study.py · 3 Seeds x 250 Trials je Variante · sequentiell, 1 Kern · generiert 2026-07-30",
  strict: "Quelle: daten/trial_convergence_study_strict.py · 3 Seeds x 250 Trials je Variante · Multiprocessing (12 Kerne) · generiert 2026-07-30"
};

function buildPanel(container, panel) {
  const W = 900, H = 220, padL = 46, padR = 14, padT = 10, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const seriesAll = [panel.s1.agg, panel.s2.agg].flat();
  let yMin = Infinity, yMax = -Infinity;
  seriesAll.forEach(p => { if (p.min != null) { yMin = Math.min(yMin, p.min); yMax = Math.max(yMax, p.max); } });
  if (!isFinite(yMin)) { yMin = 0; yMax = 1; }
  const pad = (yMax - yMin) * 0.08;
  yMin -= pad; yMax += pad;
  const nTrials = panel.s1.agg.length;

  const x = i => padL + (i / (nTrials - 1)) * plotW;
  const y = v => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  function pathFor(agg, key) {
    return agg.filter(p => p[key] != null).map((p, idx) => `${idx===0?'M':'L'}${x(p.trial).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  }
  function bandFor(agg) {
    const valid = agg.filter(p => p.min != null);
    if (!valid.length) return "";
    const top = valid.map(p => `${x(p.trial).toFixed(1)},${y(p.max).toFixed(1)}`).join(" L ");
    const bottom = valid.slice().reverse().map(p => `${x(p.trial).toFixed(1)},${y(p.min).toFixed(1)}`).join(" L ");
    return `M ${top} L ${bottom} Z`;
  }

  const yTicks = 4;
  let gridSvg = "";
  for (let t = 0; t <= yTicks; t++) {
    const v = yMin + (yMax - yMin) * t / yTicks;
    const yy = y(v);
    gridSvg += `<line class="gridline" x1="${padL}" x2="${W-padR}" y1="${yy}" y2="${yy}"></line>`;
    gridSvg += `<text class="axislabel" x="${padL-6}" y="${yy+3}" text-anchor="end">${v.toFixed(0)}%</text>`;
  }
  const xTicks = [0, Math.round(nTrials*0.25), Math.round(nTrials*0.5), Math.round(nTrials*0.75), nTrials-1];
  let xAxisSvg = "";
  xTicks.forEach(t => {
    xAxisSvg += `<text class="axislabel" x="${x(t)}" y="${H-8}" text-anchor="middle">${t}</text>`;
  });

  const svg = `
  <svg viewBox="0 0 ${W} ${H}">
    ${gridSvg}
    <line class="baseline" x1="${padL}" x2="${W-padR}" y1="${padT+plotH}" y2="${padT+plotH}"></line>
    ${xAxisSvg}
    <path class="band" fill="var(--s1-wash)" d="${bandFor(panel.s1.agg)}"></path>
    <path class="band" fill="var(--s2-wash)" d="${bandFor(panel.s2.agg)}"></path>
    <path class="meanline" stroke="var(--s1)" d="${pathFor(panel.s1.agg,'mean')}"></path>
    <path class="meanline" stroke="var(--s2)" d="${pathFor(panel.s2.agg,'mean')}"></path>
    <line class="hoverline" id="hl-${panel.key}" x1="0" x2="0" y1="${padT}" y2="${padT+plotH}"></line>
    <rect class="hitrect" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" id="hit-${panel.key}"></rect>
  </svg>`;

  const statHtml = `
    <div class="stat-row">
      <span class="stat">mit rr — final: <b>${panel.s1.final_mean.toFixed(0)}%</b> (${panel.s1.elapsed_mean.toFixed(0)}s/Seed)</span>
      <span class="stat">ohne rr — final: <b>${panel.s2.final_mean.toFixed(0)}%</b> (${panel.s2.elapsed_mean.toFixed(0)}s/Seed)</span>
    </div>`;

  container.innerHTML = `<h2>${panel.symbol} (${panel.timeframe})</h2>
    <div class="meta">${panel.n_candles} Kerzen Historie</div>
    ${svg}
    ${statHtml}`;

  const hitrect = container.querySelector(`#hit-${panel.key}`);
  const hoverline = container.querySelector(`#hl-${panel.key}`);
  const tooltip = document.getElementById("tooltip");

  hitrect.addEventListener("mousemove", e => {
    const svgEl = container.querySelector("svg");
    const svgRect = svgEl.getBoundingClientRect();
    const scale = W / svgRect.width;
    const localX = (e.clientX - svgRect.left) * scale;
    const trialIdx = Math.round(((localX - padL) / plotW) * (nTrials - 1));
    const clamped = Math.max(0, Math.min(nTrials - 1, trialIdx));
    hoverline.setAttribute("x1", x(clamped)); hoverline.setAttribute("x2", x(clamped));
    hoverline.style.opacity = 1;

    const p1 = panel.s1.agg[clamped], p2 = panel.s2.agg[clamped];
    tooltip.innerHTML = `<b>Trial ${clamped}</b><br>
      mit rr: ${p1.mean != null ? p1.mean.toFixed(1)+'%' : '–'}<br>
      ohne rr: ${p2.mean != null ? p2.mean.toFixed(1)+'%' : '–'}`;
    tooltip.style.left = (e.clientX + 14) + "px";
    tooltip.style.top = (e.clientY + 14) + "px";
    tooltip.classList.add("show");
  });
  hitrect.addEventListener("mouseleave", () => { hoverline.style.opacity = 0; tooltip.classList.remove("show"); });
}

function render(mode) {
  document.getElementById("caveatText").innerHTML = CAVEATS[mode];
  document.getElementById("footerText").textContent = FOOTERS[mode];
  MODES[mode].forEach(p => {
    const el = document.getElementById("panel-" + p.key);
    buildPanel(el, p);
  });
}

document.getElementById("modeSeg").addEventListener("click", e => {
  const btn = e.target.closest("button[data-mode]");
  if (!btn) return;
  document.querySelectorAll("#modeSeg button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  render(btn.dataset.mode);
});

render("best_profit");
</script>
"""

def summarize(variant):
    agg = variant["agg"]
    finals = variant["final_values"]
    elapsed = variant["elapsed_s"]
    return {
        "agg": agg,
        "final_mean": sum(finals) / len(finals),
        "elapsed_mean": sum(elapsed) / len(elapsed),
    }

def build_panels_data(agg):
    panels_data = []
    for key, entry in agg.items():
        s1 = summarize(entry["variants"]["mit_risk_reward_ratio"])
        s2 = summarize(entry["variants"]["ohne_risk_reward_ratio"])
        panels_data.append({
            "key": key, "symbol": entry["symbol"], "timeframe": entry["timeframe"],
            "n_candles": entry["n_candles"], "s1": s1, "s2": s2,
        })
    return panels_data

modes_data = {
    "best_profit": build_panels_data(agg_best),
    "strict": build_panels_data(agg_strict),
}

# Panel-Container (gleiche Keys in beiden Modi -> gleiche div-IDs wiederverwendet)
keys = [p["key"] for p in modes_data["best_profit"]]
panels_html = "\n".join(f'<div class="panel" id="panel-{k}"></div>' for k in keys)

html = TEMPLATE.replace("__PANELS__", panels_html)
html = html.replace("__MODES_DATA__", json.dumps(modes_data, separators=(",", ":")))

scratch = r"C:\Users\matol\AppData\Local\Temp\claude\c--Users-matol-Desktop-botprojekte\503ae6d3-0d1a-4674-8f23-829e1cb131db\scratchpad\stbot_trial_convergence.html"
with open(scratch, "w", encoding="utf-8") as f:
    f.write(html)
print("geschrieben:", scratch)
