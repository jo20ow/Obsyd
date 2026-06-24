# EU Gas Vertical UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `GAS` tab to the Obsyd dashboard surfacing the EU gas-balance vertical: a Pro-gated residual-balance hero (with a RESIDUAL ⇄ implied-vs-actual toggle) plus three free driver panels (storage, supply, demand).

**Architecture:** Four new self-contained React panel components, each fetching one (or two) `/api/gas/*` endpoint(s) via the existing `useFetchWithError` hook, rendered with the existing `Panel` wrapper and `recharts`. A new tab is wired into `App.jsx` (`TABS` array + a conditional content block). The residual hero is wrapped in the existing `ProGate`; driver panels are ungated. No backend changes.

**Tech Stack:** React 19, Vite 7, recharts ^3.7.0, Tailwind v4 (custom theme: `cyan-glow`, `green-glow`, `bg-surface`, `border-border`). No frontend test framework exists in this repo — verification per task is `npm run lint` + `npm run build`; a final manual smoke test covers behavior.

**Conventions (reference `frontend/src/components/DisruptionScorePanel.jsx`):**
- Data: `const { data, loading, error } = useFetchWithError(url)` — `data` is the parsed JSON.
- States: fetch `error` → small red box; `!data?.available && !loading` → `return null`; `loading` → pulse text; else render.
- Wrapper: `<Panel id title info collapsible headerRight>…</Panel>`.
- Charts: `recharts` (`ResponsiveContainer`, `AreaChart`/`LineChart`, etc.), height ~70px, monospace 8px ticks.
- Gating: `<ProGate feature="…">…</ProGate>` (defined in `components/ProGate.jsx`).
- All endpoints return `{ available: boolean, reason?: string, data: [...] }` (balance also returns `latest`, `active_flags`).

**Branch:** work continues on `feat/eu-gas-vertical-ui` (already checked out, holds the spec).

---

## File Structure

- Create: `frontend/src/components/GasStoragePanel.jsx` — free; AGSI fill % + sparkline.
- Create: `frontend/src/components/GasSupplyPanel.jsx` — free; ENTSOG supply + decomposition.
- Create: `frontend/src/components/GasDemandPanel.jsx` — free; total demand (power+heat+industrial).
- Create: `frontend/src/components/GasBalancePanel.jsx` — Pro; residual hero + toggle.
- Modify: `frontend/src/App.jsx` — import the 4 panels, add `GAS` tab, add tab content block.

---

## Task 1: GasStoragePanel (free)

**Files:**
- Create: `frontend/src/components/GasStoragePanel.jsx`

- [ ] **Step 1: Create the component**

```jsx
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'

const API = '/api'

function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }

export default function GasStoragePanel() {
  const { data, loading, error } = useFetchWithError(`${API}/gas/storage?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS STORAGE // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = rows[rows.length - 1]
  const fill = latest?.fill_pct
  const net = latest ? (latest.injection_gwh || 0) - (latest.withdrawal_gwh || 0) : 0

  return (
    <Panel
      id="gas-storage"
      title="EU GAS STORAGE · AGSI"
      info="EU gas in storage (AGSI/GIE). Fill % of working capacity plus daily injection/withdrawal. Free, official redistributable data."
      collapsible
      headerRight={fill != null && <span className="font-mono text-[10px] text-cyan-glow font-bold">{fill.toFixed(1)}%</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading storage…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{fill?.toFixed(1)}%</span>
              <span className="font-mono text-[10px] text-neutral-600">{latest.stock_twh?.toFixed(1)} TWh</span>
            </div>
            <div className={`font-mono text-[10px] mt-1 ${net >= 0 ? 'text-green-glow' : 'text-orange-400'}`}>
              {net >= 0 ? '▲ injecting' : '▼ withdrawing'} {Math.abs(net).toFixed(0)} GWh/d
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={24} domain={[0, 100]} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${Number(v).toFixed(1)}%`, 'fill']} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="fill_pct" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.06} strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
```

- [ ] **Step 2: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds (chunk-size warning is pre-existing and OK).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GasStoragePanel.jsx
git commit -m "feat(gas-ui): GasStoragePanel (AGSI fill % + sparkline)"
```

---

## Task 2: GasSupplyPanel (free)

**Files:**
- Create: `frontend/src/components/GasSupplyPanel.jsx`

Endpoint `/api/gas/supply?days=120` returns `{ available, from, to, data:[{date, pipeline_gwh, production_gwh, lng_gwh, uk_net_gwh, supply_gwh}] }` (`production_gwh` is `0.0` from this route).

- [ ] **Step 1: Create the component**

```jsx
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'

const API = '/api'

function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }

function Stat({ label, value }) {
  return (
    <div className="flex items-center justify-between font-mono text-[10px]">
      <span className="text-neutral-600">{label}</span>
      <span className="text-neutral-300">{value == null ? '—' : `${Math.round(value).toLocaleString()} GWh`}</span>
    </div>
  )
}

export default function GasSupplyPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/gas/supply?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS SUPPLY // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = rows[rows.length - 1]

  return (
    <Panel
      id="gas-supply"
      title="EU GAS SUPPLY · ENTSOG"
      info="Daily EU gas supply (GWh/d) from ENTSOG physical flows: pipeline imports + LNG send-out + net UK interconnector. Free, official data."
      collapsible
      headerRight={latest && <span className="font-mono text-[10px] text-cyan-glow font-bold">{Math.round(latest.supply_gwh).toLocaleString()}</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading supply…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-2 mb-2">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{Math.round(latest.supply_gwh).toLocaleString()}</span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/d total</span>
            </div>
            <div className="space-y-1">
              <Stat label="pipeline" value={latest.pipeline_gwh} />
              <Stat label="LNG send-out" value={latest.lng_gwh} />
              <Stat label="net UK" value={latest.uk_net_gwh} />
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={28} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${Math.round(v).toLocaleString()} GWh`, 'supply']} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="supply_gwh" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.06} strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
```

- [ ] **Step 2: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GasSupplyPanel.jsx
git commit -m "feat(gas-ui): GasSupplyPanel (ENTSOG supply + decomposition)"
```

---

## Task 3: GasDemandPanel (free, two fetches)

**Files:**
- Create: `frontend/src/components/GasDemandPanel.jsx`

Combines `/api/gas/demand?days=120` (`data:[{date, heat_gwh, industrial_gwh, model_version}]`) and `/api/gas/power-burn?days=120` (`data:[{date, gen_gwh_el, implied_gas_gwh, efficiency}]`). Total demand per day = `implied_gas_gwh` (power) + `heat_gwh` + `industrial_gwh`.

- [ ] **Step 1: Create the component**

```jsx
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'

const API = '/api'

function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }

function Stat({ label, value, color }) {
  return (
    <div className="flex items-center justify-between font-mono text-[10px]">
      <span className="text-neutral-600">{label}</span>
      <span className={color || 'text-neutral-300'}>{value == null ? '—' : `${Math.round(value).toLocaleString()} GWh`}</span>
    </div>
  )
}

// Merge demand + power-burn rows by date into [{date, power, heat, industrial, total}].
function mergeRows(demand, power) {
  const byDate = new Map()
  for (const r of demand?.data ?? []) {
    byDate.set(r.date, { date: r.date, heat: r.heat_gwh || 0, industrial: r.industrial_gwh || 0, power: 0 })
  }
  for (const r of power?.data ?? []) {
    const row = byDate.get(r.date) || { date: r.date, heat: 0, industrial: 0, power: 0 }
    row.power = r.implied_gas_gwh || 0
    byDate.set(r.date, row)
  }
  const rows = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
  for (const r of rows) r.total = r.power + r.heat + r.industrial
  return rows
}

export default function GasDemandPanel() {
  const demand = useFetchWithError(`${API}/gas/demand?days=120`)
  const power = useFetchWithError(`${API}/gas/power-burn?days=120`)

  const loading = demand.loading || power.loading
  const error = demand.error // demand is the required source; power may be unavailable

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS DEMAND // FETCH ERROR</div>
      </div>
    )
  if (!demand.data?.available && !loading) return null

  const rows = mergeRows(demand.data, power.data)
  const latest = rows[rows.length - 1]
  const modelVersion = demand.data?.data?.[demand.data.data.length - 1]?.model_version

  return (
    <Panel
      id="gas-demand"
      title="EU GAS DEMAND"
      info="Modeled EU gas demand = gas-fired power burn (ENTSO-E) + HDD-driven heating + flat industrial baseline. model_version flags whether power burn is separated."
      collapsible
      headerRight={latest && <span className="font-mono text-[10px] text-cyan-glow font-bold">{Math.round(latest.total).toLocaleString()}</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading demand…</div>
      )}
      {!loading && demand.data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-2 mb-2">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{Math.round(latest.total).toLocaleString()}</span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/d total</span>
            </div>
            <div className="space-y-1">
              <Stat label="power burn" value={latest.power} />
              <Stat label="heating (HDD)" value={latest.heat} />
              <Stat label="industrial" value={latest.industrial} />
            </div>
            {modelVersion && (
              <div className="font-mono text-[8px] text-neutral-700 mt-2 truncate">model: {modelVersion}</div>
            )}
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={28} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v, name) => [`${Math.round(v).toLocaleString()} GWh`, name]} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="power" stackId="1" stroke="#a78bfa" fill="#a78bfa" fillOpacity={0.15} strokeWidth={1} dot={false} />
                  <Area type="monotone" dataKey="heat" stackId="1" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.12} strokeWidth={1} dot={false} />
                  <Area type="monotone" dataKey="industrial" stackId="1" stroke="#64748b" fill="#64748b" fillOpacity={0.12} strokeWidth={1} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
```

- [ ] **Step 2: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GasDemandPanel.jsx
git commit -m "feat(gas-ui): GasDemandPanel (power + heating + industrial)"
```

---

## Task 4: GasBalancePanel (Pro hero + toggle)

**Files:**
- Create: `frontend/src/components/GasBalancePanel.jsx`

Endpoint `/api/gas/balance?days=120` returns `{ available, latest:{date, supply_gwh, demand_gwh, exports_gwh, implied_delta, actual_delta, residual, residual_7d, z_score, flag}, active_flags, data:[ …same per-day fields… ] }`. `flag` is `null | "WATCH" | "SIGNAL"`.

- [ ] **Step 1: Create the component**

```jsx
import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'

const API = '/api'

function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }

const FLAG_COLOR = { WATCH: '#fb923c', SIGNAL: '#f87171' }

// Custom dot: render a colored marker only on flagged days, empty group otherwise.
function FlagDot(props) {
  const { cx, cy, payload } = props
  const color = payload?.flag ? FLAG_COLOR[payload.flag] : null
  if (!color || cx == null || cy == null) return <g key={payload?.date} />
  return <circle key={payload.date} cx={cx} cy={cy} r={3} fill={color} stroke="none" />
}

function GasBalanceInner() {
  const { data, loading, error } = useFetchWithError(`${API}/gas/balance?days=120`)
  const [view, setView] = useState('residual') // 'residual' | 'decomp'

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS BALANCE // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const flaggedCount = rows.filter((r) => r.flag).length
  const flag = latest?.flag
  const flagColor = flag ? FLAG_COLOR[flag] : '#737373'

  const ToggleBtn = ({ id, label }) => (
    <button
      type="button"
      onClick={() => setView(id)}
      className={`font-mono text-[9px] tracking-wider px-2 py-0.5 rounded border transition-colors ${
        view === id ? 'text-cyan-glow border-cyan-glow/50 bg-cyan-glow/5' : 'text-neutral-600 border-border hover:text-neutral-400'
      }`}
    >
      {label}
    </button>
  )

  return (
    <Panel
      id="gas-balance"
      title="EU GAS BALANCE · RESIDUAL"
      info="The residual = implied ΔStorage (supply − demand − exports) vs actual ΔStorage (AGSI), 7d-smoothed and z-scored. Persistent deviation = demand destruction or unexpected flows the market hasn't priced. Descriptive, not predictive."
      collapsible
      headerRight={
        latest && (
          <span className="font-mono text-[10px] font-bold" style={{ color: flagColor }}>
            {flag || 'OK'}
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Computing balance…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="font-mono text-3xl font-bold" style={{ color: flagColor }}>
                {latest.residual_7d >= 0 ? '+' : ''}{Math.round(latest.residual_7d).toLocaleString()}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/7d residual</span>
              <span className="font-mono text-[10px] text-yellow-400 border border-yellow-400/30 rounded px-1.5 py-0.5">
                z {latest.z_score?.toFixed(2)}
              </span>
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              {flaggedCount} flagged days / 120 · supply {Math.round(latest.supply_gwh).toLocaleString()} − demand {Math.round(latest.demand_gwh).toLocaleString()} GWh
            </div>
          </div>

          <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border/30">
            <ToggleBtn id="residual" label="RESIDUAL" />
            <ToggleBtn id="decomp" label="IMPLIED vs ACTUAL" />
          </div>

          <div className="px-2 py-2">
            {view === 'residual' && rows.length > 1 && (
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={34} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${Math.round(v).toLocaleString()} GWh`, 'residual']} labelFormatter={fmtDate} />
                  <ReferenceLine y={0} stroke="#444" />
                  <Area type="monotone" dataKey="residual" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.05} strokeWidth={1.5} dot={<FlagDot />} activeDot={{ r: 3 }} />
                </AreaChart>
              </ResponsiveContainer>
            )}
            {view === 'decomp' && rows.length > 1 && (
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={34} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v, name) => [`${Math.round(v).toLocaleString()} GWh`, name]} labelFormatter={fmtDate} />
                  <Line type="monotone" dataKey="implied_delta" name="implied ΔStorage" stroke="#22d3ee" strokeWidth={1.5} dot={false} />
                  <Line type="monotone" dataKey="actual_delta" name="actual ΔStorage" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
            <div className="flex items-center justify-center gap-4 mt-1 font-mono text-[8px] text-neutral-600">
              {view === 'residual' ? (
                <>
                  <span className="text-cyan-glow">▬ residual</span>
                  <span style={{ color: FLAG_COLOR.WATCH }}>● WATCH</span>
                  <span style={{ color: FLAG_COLOR.SIGNAL }}>● SIGNAL</span>
                </>
              ) : (
                <>
                  <span className="text-cyan-glow">▬ implied (supply−demand)</span>
                  <span className="text-neutral-400">▬ actual (AGSI)</span>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}

export default function GasBalancePanel() {
  return <GasBalanceInner />
}
```

Note: `ProGate` wrapping happens in `App.jsx` (Task 5), matching how `CrackSpreadPanel`/`STSPanel` are gated at the call site — keep the panel itself gate-agnostic.

- [ ] **Step 2: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GasBalancePanel.jsx
git commit -m "feat(gas-ui): GasBalancePanel (residual hero + implied/actual toggle)"
```

---

## Task 5: Wire the GAS tab into App.jsx

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add the four imports**

After the existing panel imports (e.g. after the `AlertRulesPanel` import on line ~33), add:

```jsx
import GasBalancePanel from './components/GasBalancePanel'
import GasStoragePanel from './components/GasStoragePanel'
import GasSupplyPanel from './components/GasSupplyPanel'
import GasDemandPanel from './components/GasDemandPanel'
```

- [ ] **Step 2: Add the GAS tab to the TABS array**

Change the `TABS` constant (around line 39) from:

```jsx
const TABS = [
  { key: 'overview', label: 'OVERVIEW' },
  { key: 'market', label: 'MARKET' },
  { key: 'signals', label: 'SIGNALS' },
  { key: 'sentiment', label: 'SENTIMENT' },
  { key: 'alerts', label: 'ALERTS' },
]
```

to:

```jsx
const TABS = [
  { key: 'overview', label: 'OVERVIEW' },
  { key: 'market', label: 'MARKET' },
  { key: 'signals', label: 'SIGNALS' },
  { key: 'gas', label: 'GAS' },
  { key: 'sentiment', label: 'SENTIMENT' },
  { key: 'alerts', label: 'ALERTS' },
]
```

- [ ] **Step 3: Add the GAS tab content block**

In `Dashboard`, immediately after the SIGNALS tab block closes (the `{activeTab === 'signals' && ( … )}` expression ends with `)}` around line 474) and before the `{/* ALERTS TAB … */}` comment, insert:

```jsx
        {/* GAS TAB */}
        {activeTab === 'gas' && (
          <>
            {/* Row 1: Residual balance hero (Pro) */}
            <ErrorBoundary name="gas-balance">
              <ProGate feature="EU Gas Balance">
                <GasBalancePanel />
              </ProGate>
            </ErrorBoundary>

            {/* Row 2: Free driver panels */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
              <ErrorBoundary name="gas-storage">
                <GasStoragePanel />
              </ErrorBoundary>
              <ErrorBoundary name="gas-supply">
                <GasSupplyPanel />
              </ErrorBoundary>
              <ErrorBoundary name="gas-demand">
                <GasDemandPanel />
              </ErrorBoundary>
            </div>
          </>
        )}
```

(`ProGate` and `ErrorBoundary` are already imported in `App.jsx`.)

- [ ] **Step 4: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat(gas-ui): wire GAS tab (Pro balance hero + free driver panels)"
```

---

## Task 6: Manual smoke test + verification

**Files:** none (verification only).

- [ ] **Step 1: Start the backend**

Run (from repo root):
```bash
.venv/bin/python -m uvicorn backend.main:app --port 8000
```
Expected: `Startup complete`, `/health` returns `{"status":"ok"}`. (`.env` already holds `ENTSOE_API_TOKEN` + `GIE_API_KEY`; gas tables are backfilled.)

- [ ] **Step 2: Start the frontend dev server**

Run (separate shell, from `frontend/`):
```bash
npm run dev
```
Open the printed URL with `/app` (e.g. `http://localhost:5173/app`) to force the dashboard.

- [ ] **Step 3: Verify the GAS tab as an anonymous/free user**

- A `GAS` tab appears between `SIGNALS` and `SENTIMENT`. Click it.
- `EU GAS STORAGE`, `EU GAS SUPPLY`, `EU GAS DEMAND` panels render real numbers + sparklines (no errors, no infinite spinners).
- `EU GAS BALANCE · RESIDUAL` is **blurred** behind the `ProGate` "EU Gas Balance / Upgrade to Pro" overlay.

- [ ] **Step 4: Verify the GAS tab as a Pro user**

Sign in and start the 14-day trial (the in-app "Start trial" flow sets the subscription to `trialing`, which `is_pro()` treats as Pro), then reload the GAS tab:
- The residual hero renders: big `residual_7d` value, `z` badge, `OK/WATCH/SIGNAL` state, "N flagged days / 120".
- The `RESIDUAL` ⇄ `IMPLIED vs ACTUAL` toggle switches the chart: RESIDUAL shows the residual area with zero line + flagged-day dots; IMPLIED vs ACTUAL shows the two ΔStorage lines.

- [ ] **Step 5: Empty-state sanity (optional)**

Confirm no crash if an endpoint returns `{available:false}` — e.g. temporarily request a panel's data with the backend stopped: the panel shows its `// FETCH ERROR` box (balance/storage/supply/demand) rather than a blank crash.

- [ ] **Step 6: Final regression build**

Run: `cd frontend && npm run lint && npm run build`
Expected: clean lint, successful build. Existing tabs (OVERVIEW/MARKET/SIGNALS/SENTIMENT/ALERTS) still render unchanged.

- [ ] **Step 7: Push branch + open PR (on user confirmation)**

```bash
git push -u origin feat/eu-gas-vertical-ui
gh pr create --base main --title "feat: EU gas vertical UI (GAS tab)" --body "Adds the GAS tab: Pro residual-balance hero + free storage/supply/demand panels. Spec: docs/superpowers/specs/2026-06-24-eu-gas-vertical-ui-design.md"
```

---

## Notes for the implementer

- **No frontend unit tests exist in this repo** (no vitest/jest/RTL). Do not add a test framework for this feature — verification is `npm run lint`, `npm run build`, and the Task 6 manual smoke test. This matches the established codebase.
- **Theme classes** (`text-cyan-glow`, `text-green-glow`, `bg-surface`, `border-border`) are defined in the Tailwind config and used throughout existing panels — reuse them, don't hardcode hex except inside recharts props (recharts needs literal colors, as existing panels do).
- **recharts is v3** — the `AreaChart`/`LineChart`/`Area`/`Line`/`XAxis`/`YAxis`/`Tooltip`/`CartesianGrid`/`ReferenceLine`/`ResponsiveContainer` imports used here all exist in v3 and are already used elsewhere in the codebase.
- All four panels follow the exact state-handling contract of `DisruptionScorePanel.jsx`; if in doubt, mirror it.
