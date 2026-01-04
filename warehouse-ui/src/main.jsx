import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

/* ---------------- Env & API base ---------------- */
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

const DEFAULT_MAP = import.meta.env.VITE_MAP_PATH_DEFAULT || "warehouse_map.json";
const DEFAULT_INV = import.meta.env.VITE_INVENTORY_CSV_DEFAULT || "inventory.csv";
const DEFAULT_DATA = import.meta.env.VITE_DATA_CSV_DEFAULT || "data.csv";

/* ---------------- Fetch helpers ---------------- */
async function fetchJson(path, opt = {}) {
  const url = `${API_BASE}${path}`;
  const init = { method: "GET", ...opt };
  const headers = new Headers(init.headers || {});
  
  const hasBody = init.body != null;
  if (hasBody && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!hasBody && String(init.method || "GET").toUpperCase() === "GET") headers.delete("Content-Type");
  
  init.headers = headers;
  
  try {
    const res = await fetch(url, init);
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try {
        const d = await res.json();
        msg = d?.detail || (typeof d === 'string' ? d : JSON.stringify(d));
      } catch (e) {}
      throw new Error(msg);
    }
    return await res.json();
  } catch (err) {
    console.error(`API Error [${url}]:`, err);
    throw err;
  }
}

async function fetchBlob(path, opt = {}) {
  const url = `${API_BASE}${path}`;
  const init = { method: "POST", ...opt };
  
  try {
    const res = await fetch(url, init);
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try {
        const d = await res.json();
        msg = d?.detail || JSON.stringify(d);
      } catch {}
      throw new Error(msg);
    }
    return await res.blob();
  } catch (err) {
    console.error(`Blob API Error [${url}]:`, err);
    throw err;
  }
}

/* ---------------- API ---------------- */
const api = {
  base: API_BASE,
  health: () => fetchJson("/health"),
  getPaths: () => fetchJson("/config/paths"),
  // รวม Set Paths + Train ไว้ใน endpoint เดียวตาม Logic เดิมของ Backend
  setPaths: (map_path, inventory_csv, data_csv, retrain = true) =>
    fetchJson("/config/paths", {
      method: "POST",
      body: JSON.stringify({ map_path, inventory_csv, data_csv, retrain }),
    }),
  recommend: (top_k) => fetchJson(`/recommend${top_k ? `?top_k=${top_k}` : ""}`),
  allocate: (top_k) => fetchJson(`/allocate${top_k ? `?top_k=${top_k}` : ""}`, { method: "POST" }),
  summary: ({ weight = "total", topKSkus } = {}) => {
    const qs = new URLSearchParams();
    qs.set("weight", weight);
    if (topKSkus != null) qs.set("top_k_skus", String(topKSkus));
    return fetchJson(`/summary?${qs.toString()}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  exportCSV: () =>
    fetchBlob("/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "alloc", fmt: "csv" }),
    }),
};

/* ---------------- UI Components ---------------- */
function Pill({ ok }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ${
        ok ? "bg-emerald-700/30 text-emerald-300" : "bg-rose-700/30 text-rose-300"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-400"}`} />
      {ok ? "System Online" : "System Offline"}
    </span>
  );
}

function Button({ children, onClick, variant = "primary", disabled, icon }) {
  const base =
    "inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed";
  const styles =
    variant === "primary"
      ? "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20"
      : variant === "secondary"
      ? "bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700"
      : "bg-transparent text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/50";
  return (
    <button className={`${base} ${styles}`} onClick={onClick} disabled={disabled}>
      {icon && <span className="text-lg">{icon}</span>}
      {children}
    </button>
  );
}

function TextInput({ value, onChange, placeholder, type = "text" }) {
  return (
    <input
      className="w-full rounded-xl bg-zinc-900/60 border border-zinc-700 px-4 py-3 text-zinc-100 placeholder-zinc-500 outline-none focus:ring-2 focus:ring-indigo-600/50 transition-all"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      type={type}
    />
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4 hover:border-zinc-700 transition-colors">
      <div className="text-xs uppercase tracking-wide text-zinc-500 font-medium">{label}</div>
      <div className="mt-1 text-2xl font-bold text-zinc-100">{value}</div>
      {sub ? <div className="mt-1 text-xs text-zinc-500">{sub}</div> : null}
    </div>
  );
}

function SlotChips({ str }) {
  if (!str) return <span className="text-zinc-600">-</span>;
  const parts = String(str).split("•").map((s) => s.trim()).filter(Boolean);
  if (parts.length === 0) return <span className="text-zinc-600">-</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {parts.map((p, i) => (
        <span key={i} className="inline-block rounded-md bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 text-xs font-medium text-indigo-300">
          {p}
        </span>
      ))}
    </div>
  );
}

/* ---------------- Main App ---------------- */
function App() {
  const [health, setHealth] = useState(null);

  // Config State
  const [mapPath, setMapPath] = useState(DEFAULT_MAP);
  const [invPath, setInvPath] = useState(DEFAULT_INV);
  const [dataPath, setDataPath] = useState(DEFAULT_DATA);

  // Data State
  const [maxItems, setMaxItems] = useState("5000");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 12;

  // KPI State
  const [kpi, setKpi] = useState(null);
  const [weightEff, setWeightEff] = useState("total");
  const [kpiTopK, setKpiTopK] = useState("50");

  useEffect(() => {
    (async () => {
      try {
        const h = await api.health();
        setHealth(h);
        const p = await api.getPaths();
        if (p?.map_path) setMapPath(p.map_path);
        if (p?.inventory_csv) setInvPath(p.inventory_csv);
        if (p?.data_csv) setDataPath(p.data_csv);
      } catch (e) {
        setHealth({ status: "down", error: String(e) });
      }
    })();
  }, []);

  // Helpers
  function toTopK(v) {
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n > 0 ? n : undefined;
  }
  
  function pickRows(payload) {
    if (!payload) return [];
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload.rows)) return payload.rows;
    return [];
  }

  // --- Unified Actions ---

  // 1. Run Optimization: Set Config -> Train -> Fetch Results -> Compute KPI
  async function handleRunOptimization() {
    setLoading(true);
    try {
      // Step A: Send Config & Trigger Retrain
      await api.setPaths(mapPath, invPath, dataPath, true);
      
      // Step B: Refresh Health Status
      const h = await api.health();
      setHealth(h);

      // Step C: Fetch Optimized Results
      const r = await api.recommend(toTopK(maxItems));
      setRows(pickRows(r));
      setPage(1);
      
      // Step D: Compute KPI Automatically
      const s = await api.summary({ weight: weightEff, topKSkus: toTopK(kpiTopK) });
      setKpi(s);
      
    } catch (e) {
      alert(`Optimization failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  // 2. Generate Plan: Run Allocator -> Refresh Rows
  async function handleGeneratePlan() {
    setLoading(true);
    try {
      const res = await api.allocate(toTopK(maxItems));
      const nextRows = pickRows(res);
      setRows(nextRows);
      
      // Refresh KPI just in case
      const s = await api.summary({ weight: weightEff, topKSkus: toTopK(kpiTopK) });
      setKpi(s);
    } catch (e) {
      alert(`Allocation failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  // 3. Export CSV
  async function handleExport() {
    try {
      const blob = await api.exportCSV();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `warehouse_optimization_${new Date().toISOString().slice(0,10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      alert(`Export failed: ${e.message}`);
    }
  }

  // Pagination
  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return (rows || []).slice(start, start + pageSize);
  }, [rows, page]);

  function fmtEff(x) {
    return typeof x === "number" && Number.isFinite(x) ? `${x.toLocaleString(undefined, { maximumFractionDigits: 1 })} m` : "-";
  }

  return (
    <div className="min-h-screen bg-[#0b0b0f] text-zinc-100 font-sans selection:bg-indigo-500/30">
      <div className="mx-auto max-w-7xl px-6 py-10">
        
        {/* Header */}
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-white mb-2">
              Warehouse <span className="text-indigo-500">ML</span> Optimizer
            </h1>
            <div className="flex items-center gap-3 text-sm text-zinc-400">
              <Pill ok={health?.status === "ok" || health?.status === "OK"} />
              <span>Version 1.8 (K-Mean clustering & Market Basket Analysis)</span>
            </div>
          </div>
          <div className="text-right hidden sm:block">
            <div className="text-2xl font-bold text-zinc-100">{health?.n_parts?.toLocaleString() ?? "-"}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wider">Total SKUs</div>
          </div>
        </header>

        {/* Controls Section */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
          
          {/* Config Column */}
          <section className="lg:col-span-2 rounded-3xl border border-zinc-800 bg-zinc-900/40 p-6 shadow-xl backdrop-blur-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-zinc-200">System Configuration</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5">
              <TextInput value={mapPath} onChange={setMapPath} placeholder="Map JSON Path" />
              <TextInput value={invPath} onChange={setInvPath} placeholder="Inventory CSV Path" />
              <TextInput value={dataPath} onChange={setDataPath} placeholder="Orders CSV Path" />
              <TextInput value={maxItems} onChange={setMaxItems} placeholder="Limit Rows" type="number" />
            </div>
            
            {/* The Simplified Action Buttons */}
            <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-zinc-800/50">
              <Button onClick={handleRunOptimization} disabled={loading} icon="🚀">
                {loading ? "Processing..." : "Run Optimization"}
              </Button>
              <Button onClick={handleGeneratePlan} disabled={loading} variant="secondary" icon="📋">
                Generate Plan
              </Button>
              <div className="flex-grow" />
              <Button onClick={handleExport} disabled={loading || rows.length === 0} variant="ghost" icon="📥">
                Export CSV
              </Button>
            </div>
          </section>

          {/* KPI Column */}
          <section className="rounded-3xl border border-zinc-800 bg-zinc-900/40 p-6 shadow-xl backdrop-blur-sm flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-zinc-200">Performance (KPI)</h2>
                {kpi && <span className="text-xs text-emerald-400 font-medium">Updated</span>}
              </div>
              
              <div className="grid grid-cols-2 gap-3 mb-4">
                 <div className="p-3 bg-zinc-900/50 rounded-xl border border-zinc-800">
                    <div className="text-xs text-zinc-500 mb-1">Total Savings</div>
                    <div className="text-xl font-bold text-emerald-400">
                      {kpi?.reallocate_efficiency ? fmtEff(kpi.reallocate_efficiency) : "-"}
                    </div>
                 </div>
                 <div className="p-3 bg-zinc-900/50 rounded-xl border border-zinc-800">
                    <div className="text-xs text-zinc-500 mb-1">Distance Reduction</div>
                    <div className="text-xl font-bold text-indigo-400">
                       {kpi?.saved_pct ? `${kpi.saved_pct.toFixed(1)}%` : "-"}
                    </div>
                 </div>
              </div>

              {kpi && (
                <div className="space-y-2 text-sm text-zinc-400">
                  <div className="flex justify-between">
                    <span>Before:</span>
                    <span className="text-zinc-200">{fmtEff(kpi.before_efficiency)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>After:</span>
                    <span className="text-zinc-200">{fmtEff(kpi.after_efficiency)}</span>
                  </div>
                </div>
              )}
            </div>
            
            {!kpi && (
              <div className="text-center text-zinc-600 text-sm py-4">
                Run optimization to see stats
              </div>
            )}
          </section>
        </div>

        {/* Results Table */}
        <section className="rounded-3xl border border-zinc-800 bg-zinc-900/40 shadow-xl overflow-hidden">
          <div className="border-b border-zinc-800/70 p-4 flex items-center justify-between bg-zinc-900/60">
             <div className="text-sm text-zinc-400">
                Page <span className="text-zinc-200 font-medium">{page}</span> of {Math.max(1, Math.ceil(rows.length / pageSize))}
             </div>
             <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}>← Prev</Button>
                <Button variant="ghost" onClick={() => setPage(p => Math.min(Math.ceil(rows.length / pageSize), p + 1))} disabled={page * pageSize >= rows.length}>Next →</Button>
             </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="bg-zinc-900/80 text-zinc-400 uppercase text-xs tracking-wider">
                <tr>
                  <th className="py-3 px-6">#</th>
                  <th className="py-3 px-6">Part ID</th>
                  <th className="py-3 px-6">Part Name</th>
                  <th className="py-3 px-6">Category</th>
                  <th className="py-3 px-6 text-center">Score</th>
                  <th className="py-3 px-6 text-center">Current Slot</th>
                  <th className="py-3 px-6 text-center">New Slot</th>
                  <th className="py-3 px-6 text-right">Current Dist</th>
                  <th className="py-3 px-6 text-right">New Dist</th>
                  <th className="py-3 px-6 text-right">Saved</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {pageRows.length === 0 ? (
                  <tr><td colSpan={10} className="py-12 text-center text-zinc-500 italic">No data available. Please run optimization.</td></tr>
                ) : (
                  pageRows.map((r, idx) => {
                    const saved = r.Dist_Saving_m || 0;
                    return (
                      <tr key={r.Part_ID || idx} className="hover:bg-zinc-800/30 transition-colors">
                        <td className="py-3 px-6 text-zinc-500">{(page - 1) * pageSize + idx + 1}</td>
                        <td className="py-3 px-6 font-mono text-indigo-300 font-medium">{r.Part_ID}</td>
                        <td className="py-3 px-6 text-zinc-300 max-w-xs truncate" title={r.Part_Name}>{r.Part_Name || "-"}</td>
                        <td className="py-3 px-6 text-zinc-400">{r.Category || "Medium"}</td>
                        <td className="py-3 px-6 text-center font-medium text-zinc-300">{r.Rec_Score?.toLocaleString()}</td>
                        <td className="py-3 px-6 text-center"><SlotChips str={r.Before_Slot_All} /></td>
                        <td className="py-3 px-6 text-center"><SlotChips str={r.After_Slot_All} /></td>
                        <td className="py-3 px-6 text-right text-zinc-400">{r.Before_Dist_m?.toFixed(1)}</td>
                        <td className="py-3 px-6 text-right text-zinc-300 font-medium">{r.After_Dist_m?.toFixed(1)}</td>
                        <td className={`py-3 px-6 text-right font-bold ${saved > 0 ? "text-emerald-400" : "text-zinc-600"}`}>
                          {saved > 0 ? `-${saved.toFixed(1)}` : "0"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);