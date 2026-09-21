import { useCallback, useEffect, useState } from 'react'
import {
  Bell, CheckCircle2, CircleAlert, TriangleAlert, XCircle,
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

const SERVICE_META: { key: string; label: string; sub: string }[] = [
  { key: 'api', label: 'Core API · Gateway', sub: 'Edge ingress · MCP transport' },
  { key: 'neon', label: 'Primary Database', sub: 'Postgres · Neon' },
  { key: 'nango', label: 'OAuth Vault', sub: 'Nango · token & proxy' },
]

function dayState(b: DayBucket): 'up' | 'blip' | 'down' | 'none' {
  if (b.checks === 0) return 'none'
  if (b.ups === b.checks) return 'up'
  if (b.ups === 0) return 'down'
  return 'blip'
}

const DAY_CLASS: Record<string, string> = {
  up: 'bg-[#10b981]',
  blip: 'bg-[#f59e0b]',
  down: 'bg-[#f43f5e]',
  none: 'bg-white/10',
}

function uptimePct(days: DayBucket[]): number {
  const checks = days.reduce((a, d) => a + d.checks, 0)
  const ups = days.reduce((a, d) => a + d.ups, 0)
  return checks === 0 ? 100 : (ups / checks) * 100
}

function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs} hr ago`
  const days = Math.round(hrs / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

export default function App() {
  const [overall, setOverall] = useState<'up' | 'degraded' | 'loading'>('loading')
  const [services, setServices] = useState<Record<string, ServiceState>>({})
  const [history, setHistory] = useState<Record<string, DayBucket[]>>({})
  const [providers, setProviders] = useState<ProviderState[]>([])
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [email, setEmail] = useState('')
  const [subState, setSubState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const [updatedAt, setUpdatedAt] = useState('')

  const load = useCallback(async () => {
    try {
      const [cur, hist, prov, inc] = await Promise.all([
        fetch(`${API}/api/v1/status`).then((r) => r.json()),
        fetch(`${API}/api/v1/status/history?days=90`).then((r) => r.json()),
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
  const latencies = Object.values(services).map((s) => s.latency_ms)
  const medianLatency = latencies.length
    ? Math.round([...latencies].sort((a, b) => a - b)[Math.floor(latencies.length / 2)])
    : null
  const lastIncident = incidents[0]

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#0b0f17] text-[#f9fafb]">
      {/* backdrop */}
      <div
        className="drift pointer-events-none absolute inset-0"
        style={{
          backgroundImage: 'radial-gradient(rgba(16,185,129,0.05) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-96"
        style={{ background: 'radial-gradient(60% 100% at 50% 0%, rgba(16,185,129,0.08), transparent)' }}
      />

      <div className="relative mx-auto max-w-6xl px-4 py-10 sm:px-6">
        {/* top bar */}
        <nav className="flex flex-wrap items-center gap-3 text-sm">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            <span className={`halo inline-block size-2 rounded-full ${allUp ? 'bg-[#10b981] text-[#10b981]' : 'bg-[#f59e0b] text-[#f59e0b]'}`} />
            Plugmere Status
          </span>
          <span className="font-mono2 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-0.5 text-[11px] text-[#94a3b8]">
            LIVE TELEMETRY
          </span>
          <span className="ml-auto font-mono2 hidden text-[11px] text-[#64748b] sm:block">
            refreshed {updatedAt || '—'} · 60s poll
          </span>
        </nav>

        {/* hero */}
        <section className="mt-10 text-center">
          <div
            className={`mx-auto flex size-14 items-center justify-center rounded-2xl ${
              allUp ? 'bg-[#10b981]/15 text-[#10b981] glow-operational' : 'bg-[#f59e0b]/15 text-[#f59e0b] glow-degraded'
            }`}
          >
            {allUp ? <CheckCircle2 className="size-7" /> : <TriangleAlert className="size-7" />}
          </div>
          <p className="font-mono2 mt-5 text-[11px] tracking-[0.2em] text-[#10b981]">
            ● {allUp ? 'ALL SYSTEMS NOMINAL' : 'DEGRADED — SEE BELOW'}
          </p>
          <h1 className="-tracking-[0.03em] mt-2 text-4xl font-semibold sm:text-5xl" style={{ lineHeight: 1.1 }}>
            {allUp ? 'All Systems Fully Operational' : 'Experiencing Degradation'}
          </h1>
          <p className="mx-auto mt-2 max-w-xl text-[15px] text-[#94a3b8]">
            Continuous 15-minute probes across API, database, and OAuth vault reporting{' '}
            {allUp ? 'normal behavior.' : 'an anomaly.'}
          </p>

          {/* KPI strip */}
          <div className="mx-auto mt-8 grid max-w-3xl grid-cols-2 gap-px overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.07] sm:grid-cols-4">
            {[
              {
                label: 'Median latency',
                value: medianLatency === null ? '—' : `${medianLatency}ms`,
                sub: 'across edge probes',
              },
              {
                label: 'Services monitored',
                value: String(SERVICE_META.length),
                sub: 'api · db · vault',
              },
              {
                label: 'Provider calls · 1h',
                value: String(providers.reduce((a, p) => a + p.calls, 0)),
                sub: `${providers.length} active`,
              },
              {
                label: 'Last incident',
                value: lastIncident ? timeAgo(lastIncident.at) : 'none yet',
                sub: 'fully resolved',
              },
            ].map((k) => (
              <div key={k.label} className="bg-[#0b0f17]/95 px-4 py-4 text-left">
                <p className="text-[11px] uppercase tracking-wider text-[#94a3b8]">{k.label}</p>
                <p className="font-mono2 mt-1 text-xl font-semibold text-[#f9fafb]">{k.value}</p>
                <p className="font-mono2 text-[11px] text-[#64748b]">{k.sub}</p>
              </div>
            ))}
          </div>
        </section>

        {/* components */}
        <section className="mt-12">
          <div className="flex items-center gap-2">
            <span className="h-4 w-1 rounded-full bg-[#10b981]" />
            <h2 className="text-xl font-semibold tracking-tight">System Components</h2>
            <span className="font-mono2 ml-2 text-[11px] text-[#64748b]">{SERVICE_META.length} monitored</span>
            <span className="font-mono2 ml-auto hidden gap-3 text-[11px] text-[#64748b] sm:flex">
              <span className="flex items-center gap-1"><span className="size-1.5 rounded-full bg-[#10b981]" /> Operational</span>
              <span className="flex items-center gap-1"><span className="size-1.5 rounded-full bg-[#f59e0b]" /> Degraded</span>
              <span className="flex items-center gap-1"><span className="size-1.5 rounded-full bg-[#f43f5e]" /> Downtime</span>
            </span>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            {SERVICE_META.map(({ key, label, sub }) => {
              const s = services[key]
              const days: DayBucket[] = history[key] ?? []
              const up = s?.ok ?? false
              return (
                <div
                  key={key}
                  className="rounded-lg border border-white/[0.08] bg-[#111827]/85 p-5 backdrop-blur-xl transition-all hover:-translate-y-px hover:border-white/[0.18]"
                >
                  <div className="flex items-center gap-2">
                    <span className={`halo inline-block size-1.5 rounded-full ${s ? (up ? 'bg-[#10b981] text-[#10b981]' : 'bg-[#f43f5e] text-[#f43f5e]') : 'bg-[#64748b] text-[#64748b]'}`} />
                    <h3 className="text-[15px] font-medium">{label}</h3>
                    <span
                      className={`font-mono2 ml-auto rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
                        s
                          ? up
                            ? 'bg-[#10b981]/15 text-[#10b981]'
                            : 'bg-[#f43f5e]/15 text-[#f43f5e]'
                          : 'bg-white/[0.06] text-[#94a3b8]'
                      }`}
                    >
                      {s ? (up ? 'Operational' : 'Down') : 'Probing…'}
                    </span>
                  </div>
                  <p className="font-mono2 mt-1 text-[11px] text-[#64748b]">{sub} · 90-day window</p>

                  <div className="mt-3 flex items-baseline gap-2">
                    <span className="font-mono2 text-3xl font-semibold">{s ? s.latency_ms : '—'}</span>
                    <span className="font-mono2 text-xs text-[#64748b]">ms avg response</span>
                    <span className="font-mono2 ml-auto text-xs text-[#10b981]">{uptimePct(days).toFixed(2)}%</span>
                  </div>

                  {/* 90-day sticks */}
                  <div className="mt-2 flex h-8 items-stretch gap-[2px]">
                    {Array.from({ length: 90 }).map((_, i) => {
                      const b = days[days.length - 90 + i]
                      const st = b ? dayState(b) : 'none'
                      const title = b
                        ? `${b.day}: ${b.ups}/${b.checks} probes up`
                        : 'no data yet'
                      return (
                        <div
                          key={i}
                          title={title}
                          className={`flex-1 cursor-pointer rounded-[1px] transition-all duration-200 hover:scale-y-125 hover:opacity-100 ${DAY_CLASS[st]} ${st === 'none' ? '' : ''}`}
                          style={{ opacity: st === 'none' ? 0.5 : 1 }}
                        />
                      )
                    })}
                  </div>
                  <div className="font-mono2 mt-1.5 flex justify-between text-[10px] text-[#64748b]">
                    <span>90 days ago</span>
                    <span>Today</span>
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* providers */}
        <section className="mt-10">
          <div className="flex items-center gap-2">
            <span className="h-4 w-1 rounded-full bg-[#06b6d4]" />
            <h2 className="text-xl font-semibold tracking-tight">Provider Traffic · last hour</h2>
            <span className="font-mono2 ml-2 text-[11px] text-[#64748b]">{providers.length} active</span>
          </div>
          {providers.length === 0 ? (
            <p className="mt-3 rounded-lg border border-white/[0.08] bg-[#111827]/85 px-5 py-6 text-sm text-[#94a3b8]">
              No traffic in the last hour — quiet.
            </p>
          ) : (
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {providers.map((p) => (
                <div
                  key={p.provider}
                  className="flex items-center gap-3 rounded-lg border border-white/[0.08] bg-[#111827]/85 px-4 py-3 transition-all hover:-translate-y-px hover:border-white/[0.18]"
                >
                  {p.state === 'up' ? (
                    <CheckCircle2 className="size-4 shrink-0 text-[#10b981]" />
                  ) : p.state === 'flaky' ? (
                    <TriangleAlert className="size-4 shrink-0 text-[#f59e0b]" />
                  ) : (
                    <XCircle className="size-4 shrink-0 text-[#f43f5e]" />
                  )}
                  <div className="min-w-0">
                    <div className="font-mono2 truncate text-sm font-medium">{p.provider}</div>
                    <div className="font-mono2 text-[11px] text-[#64748b]">
                      {p.calls} calls · {p.errors} err
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* incidents + subscribe */}
        <section className="mt-10 grid gap-4 lg:grid-cols-5">
          <div className="rounded-lg border border-white/[0.08] bg-[#111827]/85 p-5 lg:col-span-3">
            <div className="flex items-center gap-2">
              <CircleAlert className="size-4 text-[#94a3b8]" />
              <h2 className="text-xl font-semibold tracking-tight">Incident History</h2>
            </div>
            {incidents.length === 0 ? (
              <p className="mt-3 text-sm text-[#94a3b8]">No incidents recorded. Quiet is good.</p>
            ) : (
              <ol className="mt-4 space-y-5">
                {incidents.map((inc, i) => (
                  <li key={i} className="relative border-l border-white/10 pb-1 pl-4">
                    <span className="absolute -left-1 top-1.5 size-2 rounded-full bg-[#f59e0b]" />
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono2 rounded-full bg-[#10b981]/15 px-2.5 py-0.5 text-[11px] font-semibold text-[#10b981]">
                        Resolved
                      </span>
                      <span className="font-mono2 text-[11px] text-[#94a3b8]">
                        {new Date(inc.at).toLocaleDateString()} · {new Date(inc.at).toLocaleTimeString()}
                      </span>
                    </div>
                    <p className="font-mono2 mt-2 text-xs text-[#dfe2ee]">{inc.summary}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <div className="rounded-lg border border-[#10b981]/25 bg-[#10b981]/[0.05] p-5 lg:col-span-2">
            <h2 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
              <Bell className="size-4" /> Get notified
            </h2>
            <p className="mt-1 text-sm text-[#94a3b8]">Email when a provider goes down. Outages only.</p>
            {subState === 'done' ? (
              <p className="mt-4 flex items-center gap-2 rounded-lg border border-[#10b981]/30 bg-[#10b981]/10 px-3 py-2.5 text-sm text-[#6ffbbe]">
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
                  className="font-mono2 min-w-0 flex-1 rounded-md border border-white/10 bg-[#0b0f17] px-3 py-2.5 text-sm outline-none placeholder:text-[#64748b] focus:border-[#06b6d4] focus:shadow-[0_0_12px_rgba(6,182,212,0.2)]"
                />
                <button
                  type="submit"
                  disabled={subState === 'sending'}
                  className="shrink-0 rounded-md bg-[#f9fafb] px-4 py-2.5 text-sm font-semibold text-[#0b0f17] transition hover:brightness-110 disabled:opacity-50"
                >
                  {subState === 'sending' ? '…' : 'Subscribe'}
                </button>
              </form>
            )}
            {subState === 'error' && (
              <p className="mt-2 text-xs text-[#f43f5e]">Couldn't subscribe — try again.</p>
            )}
          </div>
        </section>

        <footer className="font-mono2 mt-10 flex flex-wrap gap-2 pb-4 text-[11px] text-[#64748b]">
          <span>© 2026 Plugmere · metrics refresh continuously</span>
          <span className="ml-auto">Median latency {medianLatency ?? '—'}ms · All systems nominal</span>
        </footer>
      </div>
    </div>
  )
}
