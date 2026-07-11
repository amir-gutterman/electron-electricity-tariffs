"""
Electron web UI — run tariff checks from the browser and edit your current tariff.
Start with: python app.py
Then open http://localhost:5000
"""
import json
import os
import sys
import threading
from flask import Flask, Response, jsonify, render_template_string, request

sys.path.insert(0, os.path.dirname(__file__))
import electron

app = Flask(__name__)

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚡ Electron — Tariff Checker</title>
<style>
  :root {
    --bg: #f4f6fb; --surface: #ffffff; --border: #e2e8f0;
    --text: #1a202c; --muted: #64748b; --accent: #6366f1;
    --green: #16a34a; --red: #dc2626; --yellow: #b45309;
    --green-bg: rgba(22,163,74,.1); --red-bg: rgba(220,38,38,.1);
    --yellow-bg: rgba(180,83,9,.1);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         font-size: 14px; line-height: 1.6; padding: 24px; }
  h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: var(--muted); margin-bottom: 24px; font-size: 13px; }
  .grid { display: grid; grid-template-columns: 340px 1fr; gap: 20px; align-items: start; }
  @media(max-width:760px){ .grid { grid-template-columns: 1fr; } }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
              color: var(--muted); margin-bottom: 16px; }
  label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; margin-top: 12px; }
  label:first-of-type { margin-top: 0; }
  input[type=number] {
    width: 100%; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); padding: 8px 10px; font-size: 14px;
  }
  input[type=number]:focus { outline: 2px solid var(--accent); border-color: transparent; }
  .baseline-row { background: var(--bg); border-radius: 8px; padding: 12px; margin-top: 16px;
                  display: flex; justify-content: space-between; align-items: center; }
  .baseline-label { color: var(--muted); font-size: 12px; }
  .baseline-val { font-size: 20px; font-weight: 700; color: var(--yellow); }
  .run-btn {
    margin-top: 16px; width: 100%; padding: 12px; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 15px; font-weight: 600;
    border: none; cursor: pointer; transition: opacity .15s;
  }
  .run-btn:hover { opacity: .85; }
  .run-btn:disabled { opacity: .5; cursor: not-allowed; }
  .status { margin-top: 10px; font-size: 12px; color: var(--muted); min-height: 18px; text-align: center; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); padding: 6px 10px; border-bottom: 1px solid var(--border); }
  td { padding: 10px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .pill { display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 7px;
          border-radius: 99px; letter-spacing: .04em; }
  .pill-green { background: var(--green-bg); color: var(--green); }
  .pill-red   { background: var(--red-bg);   color: var(--red); }
  .pill-yellow{ background: var(--yellow-bg);color: var(--yellow); }
  .company-name { font-weight: 600; }
  .company-note { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .savings-pos { color: var(--green); font-weight: 700; }
  .savings-neg { color: var(--red); font-weight: 700; }
  .empty { color: var(--muted); text-align: center; padding: 40px 0; font-size: 13px; }
  .alert-banner { margin-bottom: 16px; padding: 12px 16px; border-radius: 8px;
                  background: var(--green-bg); border: 1px solid var(--green);
                  color: var(--green); font-weight: 600; font-size: 13px; }
  .error-list { margin-top: 12px; }
  .error-item { font-size: 11px; color: var(--red); padding: 4px 0; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.3);
             border-top-color: #fff; border-radius: 50%; animation: spin .6s linear infinite;
             vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .tag-unverified { font-size: 10px; color: var(--yellow); margin-left: 6px; }
</style>
</head>
<body>
<h1>⚡ Electron</h1>
<p class="subtitle">Spanish electricity tariff checker — scrapes official provider pages and compares against your contract</p>

<div class="grid">
  <!-- Left: tariff editor -->
  <div class="card">
    <h2>Your current tariff (before IVA)</h2>

    <label>Potencia rate (€/kW/month)</label>
    <input type="number" id="potencia_rate" value="3.62" step="0.001" min="0">

    <label>Contracted power (kW)</label>
    <input type="number" id="contracted_power" value="4.5" step="0.1" min="0">

    <label>Energy rate (€/kWh)</label>
    <input type="number" id="consumption_rate" value="0.098" step="0.001" min="0">

    <label>Assumed monthly consumption (kWh)</label>
    <input type="number" id="assumed_monthly_kwh" value="500" step="10" min="0">

    <label>Alert threshold (€/month savings)</label>
    <input type="number" id="min_savings_threshold" value="2" step="0.5" min="0">

    <div class="baseline-row">
      <span class="baseline-label">Your monthly baseline</span>
      <span class="baseline-val" id="baseline-display">€65.29</span>
    </div>

    <button class="run-btn" id="run-btn" onclick="runCheck()">Run now</button>
    <div class="status" id="status"></div>
  </div>

  <!-- Right: results -->
  <div class="card">
    <h2>Offers</h2>
    <div id="alert-area"></div>
    <div id="results">
      <div class="empty">Hit "Run now" to fetch live tariff data from provider websites.</div>
    </div>
    <div class="error-list" id="errors"></div>
  </div>
</div>

<script>
const fields = ['potencia_rate','contracted_power','consumption_rate','assumed_monthly_kwh','min_savings_threshold'];

function calcBaseline() {
  const p = parseFloat(document.getElementById('potencia_rate').value) || 0;
  const c = parseFloat(document.getElementById('contracted_power').value) || 0;
  const e = parseFloat(document.getElementById('consumption_rate').value) || 0;
  const k = parseFloat(document.getElementById('assumed_monthly_kwh').value) || 0;
  document.getElementById('baseline-display').textContent = '€' + ((p*c)+(e*k)).toFixed(2);
}
fields.forEach(f => document.getElementById(f).addEventListener('input', calcBaseline));

async function runCheck() {
  const btn = document.getElementById('run-btn');
  const status = document.getElementById('status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Checking providers…';
  status.textContent = 'Fetching live data from provider websites (this takes ~30 s)…';
  document.getElementById('alert-area').innerHTML = '';
  document.getElementById('results').innerHTML = '<div class="empty">Loading…</div>';
  document.getElementById('errors').innerHTML = '';

  const config = {};
  fields.forEach(f => config[f] = parseFloat(document.getElementById(f).value));

  try {
    const resp = await fetch('/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config),
    });
    const data = await resp.json();
    renderResults(data);
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Run now';
  }
}

function renderResults(data) {
  const status = document.getElementById('status');
  status.textContent = 'Done — ' + data.offers.length + ' offer(s) checked.';

  if (data.alert_sent) {
    document.getElementById('alert-area').innerHTML =
      '<div class="alert-banner">✅ Cheaper offer found! Alert email sent.</div>';
  }

  if (!data.offers.length) {
    document.getElementById('results').innerHTML = '<div class="empty">No offers retrieved.</div>';
    return;
  }

  const baseline = data.baseline;
  // Sort: trusted first, then by savings desc
  const sorted = [...data.offers].sort((a,b) => {
    if (a.trusted !== b.trusted) return b.trusted - a.trusted;
    return b.savings - a.savings;
  });

  let html = `<table>
    <thead><tr>
      <th>Provider</th>
      <th>Potencia (€/kW/mo)</th>
      <th>Energy (€/kWh)</th>
      <th>Est. monthly</th>
      <th>vs yours</th>
    </tr></thead><tbody>`;

  for (const o of sorted) {
    const savClass = o.savings > 0 ? 'savings-pos' : 'savings-neg';
    const savSign  = o.savings > 0 ? '−' : '+';
    const savAbs   = Math.abs(o.savings).toFixed(2);
    const pill = o.trusted
      ? (o.savings > data.threshold ? '<span class="pill pill-green">CHEAPER</span>'
         : o.savings > 0 ? '<span class="pill pill-yellow">MARGINAL</span>'
         : '<span class="pill pill-red">PRICIER</span>')
      : '<span class="pill pill-yellow">UNVERIFIED</span>';
    const unverTag = o.trusted ? '' : '<span class="tag-unverified">⚠ aggregator</span>';

    html += `<tr>
      <td>
        <div class="company-name"><a href="${o.url}" target="_blank">${o.company}</a>${unverTag}</div>
        ${o.note ? `<div class="company-note">${o.note}</div>` : ''}
      </td>
      <td>${o.potencia.toFixed(4)}</td>
      <td>${o.kwh_rate.toFixed(4)}</td>
      <td>€${o.cost.toFixed(2)}</td>
      <td><span class="${savClass}">${savSign}€${savAbs}/mo</span><br>${pill}</td>
    </tr>`;
  }

  html += `</tbody></table>`;
  document.getElementById('results').innerHTML = html;

  if (data.errors && data.errors.length) {
    document.getElementById('errors').innerHTML =
      data.errors.map(e => `<div class="error-item">⚠ ${e.parser}: ${e.error}</div>`).join('');
  }
}
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/run", methods=["POST"])
def run():
    config = request.get_json(force=True) or {}
    result = electron.run_check(config)
    return jsonify(result)


if __name__ == "__main__":
    print("Electron web UI starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
