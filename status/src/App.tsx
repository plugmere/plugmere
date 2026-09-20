import { useCallback, useEffect, useState } from 'react'
import {
  Activity, Bell, BellOff, CheckCircle2, CircleAlert, Plug, TriangleAlert, XCircle,
} from 'lucide-react'

const API = import.meta.env.VITE_API_URL ?? 'https://plugmere-api.onrender.com'

interface ServiceState {
  ok: boolean
  latency_ms: number
  checked_at: string
}

interface DayBucket {
  day: string
  checks: number
  ups: number
}

interface ProviderState {
  provider: string
  calls: number
  errors: number
  error_rate: number
  state: 'up' | 'flaky' | 'down'
  last_seen: string
}

interface Incident {
  summary: string
  at: string
}

const SERVICE_LABELS: Record<string, string> = {
  api: 'API · Gateway',
  neon: 'Postgres · Neon',
  nango: 'OAuth vault · Nango',
}

function uptimePct(days: DayBucket[]): number {
  const checks = days.reduce((a, d) => a + d.checks, 0)
  const ups = days.reduce((a, d) => a + d.ups, 0)
  return checks === 0 ? 100 : (ups / checks) * 100
}

function dayColor(b: DayBucket): string {
  if (b.checks === 0) return 'bg-zinc-800'
  const pct = b.ups / b.checks
  if (pct === 1) return 'bg-emerald-500'
  if (pct >= 0.9) return 'bg-amber-400'
  return 'bg-red-500'
}

function StateDot({ ok, className = 'size-2.5' }: { ok: boolean; className?: string }) {
  return (
    <span
      className={`pulse-dot inline-block rounded-full ${className} ${ok ? 'bg-emerald-400 text-emerald-400' : 'bg-red-500 text-red-500'}`}
    />
  )
}

export default function App() {
  const [overall, setOverall] = useState<'up' | 'degraded' | 'loading'>('loading')
  const [services, setServices] = useState<Record<string, ServiceState>>({})
  const [history, setHistory] = useState<Record<string, DayBucket[]>>({})
  const [providers, setProviders] = useState<ProviderState[]>([])
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [email, setEmail] = useState('')
  const [subState, setSubState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const [updatedAt, setUpdatedAt] = useState<string>('')

  const load = useCallback(async () => {
    try {
      const [cur, hist, prov, inc] = await Promise.all([
        fetch(`${API}/api/v1/status`).then((r) => r.json()),
        fetch(`${API}/api/v1/status/history?days=14`).then((r) => r.json()),
        fetch(`${API}/api/v1/status/providers`).then((r) => r.json()),
        fetch(`${API}/api/v1/status/incidents?limit=10`).then((r) => r.json()),
      ])
      setOverall(cur.overall === 'up' ? 'up' : 'degraded')
      setServices(cur.services ?? {})
      setHistory(hist.history ?? {})
      setProviders(Array.isArray(prov) ? prov : [])
      setIncidents(Array.isArray(inc) ? inc : [])
      setUpdatedAt(new Date().toLocaleTimeString())
    } catch {
      setOverall('degraded')
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [load])

  const subscribe = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.includes('@')) return
    setSubState('sending')
    try {
      const r = await fetch(`${API}/api/v1/status/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      setSubState(r.ok ? 'done' : 'error')
    } catch {
      setSubState('error')
    }
  }

  const allUp = overall === 'up'

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#0b0d12] text-zinc-100">
      {/* backdrop */}
      <div className="scanline pointer-events-none absolute inset-0 overflow-hidden" />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: 'radial-gradient(oklch(0.35 0.08 160 / 0.12) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />

      <div className="relative mx-auto max-w-5xl px-4 py-10 sm:px-6">
        {/* header */}
        <header className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-3">
            <span className="flex size-11 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/30">
              <Plug className="size-6" />
            </span>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Plugmere Status</h1>
              <p className="text-sm text-zinc-400">Every plug in its place — live.</p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900/70 px-4 py-2">
            {overall === 'loading' ? (
              <Activity className="size-4 animate-pulse text-zinc-400" />
            ) : (
              <StateDot ok={allUp} />
            )}
            <span className={`text-sm font-semibold ${allUp ? 'text-emerald-400' : overall === 'loading' ? 'text-zinc-400' : 'text-amber-400'}`}>
              {overall === 'loading' ? 'Checking…' : allUp ? 'All systems operational' : 'Degraded — see below'}
            </span>
          </div>
        </header>

        {/* services */}
        <section className="mt-8 grid gap-4 sm:grid-cols-3">
          {Object.entries(SERVICE_LABELS).map(([key, label]) => {
            const s = services[key]
            const days = history[key] ?? []
            return (
              <div key={key} className="rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 backdrop-blur">
                <div className="flex items-center gap-2">
                  {s ? <StateDot ok={s.ok} /> : <span className="inline-block size-2.5 rounded-full bg-zinc-700" />}
                  <h2 className="text-sm font-semibold text-zinc-200">{label}</h2>
                </div>
                <p className="mt-3 font-mono text-3xl font-bold tracking-tight">
                  {s ? `${s.latency_ms}` : '—'}
                  <span className="ml-1 text-sm font-normal text-zinc-500">ms</span>
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {days.length > 0 ? `${uptimePct(days).toFixed(2)}% uptime · 14d` : 'collecting history…'}
                </p>
                {/* history strip */}
                <div className="mt-3 flex gap-1">
                  {days.map((d) => (
                    <div
                      key={d.day}
                      title={`${d.day}: ${d.ups}/${d.checks} checks up`}
                      className={`h-8 flex-1 rounded-sm ${dayColor(d)}`}
                    />
                  ))}
                  {days.length === 0 && <div className="h-8 flex-1 rounded-sm bg-zinc-800/60" />}
                </div>
              </div>
            )
          })}
        </section>

        {/* providers */}
        <section className="mt-8 rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 backdrop-blur">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Providers · last hour</h2>
            <span className="text-xs text-zinc-500">{providers.length} active</span>
          </div>
          {providers.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">No traffic in the last hour — quiet.</p>
          ) : (
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {providers.map((p) => (
                <div key={p.provider} className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2.5">
                  {p.state === 'up' ? (
                    <CheckCircle2 className="size-4 shrink-0 text-emerald-400" />
                  ) : p.state === 'flaky' ? (
                    <TriangleAlert className="size-4 shrink-0 text-amber-400" />
                  ) : (
                    <XCircle className="size-4 shrink-0 text-red-500" />
                  )}
                  <div className="min-w-0">
                    <div className="truncate font-mono text-sm font-medium">{p.provider}</div>
                    <div className="text-xs text-zinc-500">
                      {p.calls} calls · {p.errors} err
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* incidents + subscribe */}
        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5 backdrop-blur">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-zinc-400">
              <CircleAlert className="size-4" /> Incidents
            </h2>
            {incidents.length === 0 ? (
              <p className="mt-4 text-sm text-zinc-500">No incidents recorded. Quiet is good.</p>
            ) : (
              <ol className="mt-4 space-y-0">
                {incidents.map((inc, i) => (
                  <li key={i} className="relative border-l border-zinc-800 pb-4 pl-4 last:pb-0">
                    <span className="absolute -left-1 top-1 size-2 rounded-full bg-amber-400" />
                    <p className="font-mono text-xs text-zinc-300">{inc.summary}</p>
                    <p className="text-xs text-zinc-500">{new Date(inc.at).toLocaleString()}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.04] p-5 backdrop-blur">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-zinc-300">
              <Bell className="size-4" /> Get notified
            </h2>
            <p className="mt-2 text-sm text-zinc-400">
              Email when a provider goes down. No spam — outages only.
            </p>
            {subState === 'done' ? (
              <p className="mt-4 flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2.5 text-sm text-emerald-300">
                <CheckCircle2 className="size-4" /> You're on the list.
              </p>
            ) : (
              <form onSubmit={subscribe} className="mt-4 flex gap-2">
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-600 focus:border-emerald-500/60"
                />
                <button
                  type="submit"
                  disabled={subState === 'sending'}
                  className="shrink-0 rounded-xl bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:opacity-50"
                >
                  {subState === 'sending' ? '…' : 'Subscribe'}
                </button>
              </form>
            )}
            {subState === 'error' && (
              <p className="mt-2 flex items-center gap-1 text-xs text-red-400">
                <BellOff className="size-3" /> Couldn't subscribe — try again.
              </p>
            )}
          </div>
        </section>

        <footer className="mt-10 flex flex-wrap items-center gap-2 text-xs text-zinc-600">
          <span>Plugmere Status</span>
          <span>·</span>
          <span>refreshed {updatedAt || '—'} · auto-refresh 60s</span>
          <span className="ml-auto">Built on Plugmere itself — this page eats its own API.</span>
        </footer>
      </div>
    </div>
  )
}
