import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { CatalogItem, UserConnection } from '@/lib/types'
import { useAuth } from '@/lib/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ProviderIcon } from '@/components/provider-icon'
import { toast } from 'sonner'
import { Check, Search, Trash2, Plus, ArrowLeft, Key } from 'lucide-react'
import { useUserApiKeys, useSaveApiKey, useDeleteApiKey } from '@/hooks/use-user-api-keys'

const TABS = ['All', 'Connected'] as const

export function IntegrationsPage() {
  const { profile } = useAuth()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'All' | 'Connected'>('All')
  const [search, setSearch] = useState('')
  const [connecting, setConnecting] = useState<string | null>(null)
  const [selected, setSelected] = useState<CatalogItem | null>(null)
  const [apiKeyModal, setApiKeyModal] = useState<CatalogItem | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')

  const catalog = useQuery<CatalogItem[]>({ queryKey: ['catalog'], queryFn: () => api('/api/v1/catalog') })
  const connections = useQuery<UserConnection[]>({
    queryKey: ['connections'],
    queryFn: () => api(`/api/v1/connections?user_id=${profile!.id}`),
    enabled: !!profile,
  })
  const apiKeys = useUserApiKeys()
  const saveApiKey = useSaveApiKey()
  const deleteApiKey = useDeleteApiKey()

  const deleteMutation = useMutation({
    mutationFn: ({ id, provider }: { id: string; provider: string }) =>
      api(`/api/v1/connections/${id}?provider=${provider}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success('Connection removed')
      queryClient.invalidateQueries({ queryKey: ['connections'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const connectionsByProvider = (connections.data ?? []).reduce<Record<string, UserConnection[]>>((acc, c) => {
    ;(acc[c.provider] ??= []).push(c)
    return acc
  }, {})

  const apiKeyProviders = new Set((apiKeys.data ?? []).map((k) => k.provider))

  const filtered = (catalog.data ?? []).filter((item) => {
    if (tab === 'Connected') {
      const hasOAuth = !!connectionsByProvider[item.nango_provider_key]?.length
      const hasApiKey = apiKeyProviders.has(item.provider)
      if (!hasOAuth && !hasApiKey) return false
    }
    if (search && !item.name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const handleConnect = async (item: CatalogItem, allowMulti = false) => {
    // API-key providers: open the API key modal
    if (item.auth_type === 'api_key') {
      setApiKeyModal(item)
      setApiKeyInput('')
      return
    }

    const existing = connectionsByProvider[item.nango_provider_key] ?? []
    if (existing.length > 0 && !allowMulti) {
      toast.error(`${item.name} is already connected`)
      return
    }
    try {
      setConnecting(item.nango_provider_key)
      const session = await api<{ token: string; connect_url: string }>('/api/v1/connections/session', {
        method: 'POST',
        body: JSON.stringify({ provider: item.nango_provider_key }),
      })
      if (!session.connect_url) throw new Error('No connect URL returned')
      // Magic link: Connect UI loads with the session token baked in.
      // (The SDK popup flow needs a public key + websocket path this
      // Nango build doesn't serve, so we open the link directly.)
      const popup = window.open(session.connect_url, '_blank', 'width=500,height=700')
      if (!popup) throw new Error('Popup blocked — allow popups and try again')
      await new Promise<void>((resolve) => {
        const timer = setInterval(() => {
          if (popup.closed) {
            clearInterval(timer)
            resolve()
          }
        }, 500)
      })

      // Server-side dedup: remove older duplicates, keep newest
      const dedup = await api<{ removed: number }>(`/api/v1/connections/dedup/${item.nango_provider_key}`, { method: 'POST' })
      // Pull-sync the read-model (Nango won't push webhooks on this build)
      await api('/api/v1/connections/sync', { method: 'POST' }).catch(() => null)
      await connections.refetch()

      if (dedup.removed > 0) {
        toast.info(`${item.name} already connected`)
      } else {
        toast.success(`${item.name} connected`)
      }
    } catch (err) {
      if (err instanceof Error && err.message.includes('window_closed')) {
        toast.error('Authorization window was closed')
      } else {
        toast.error(err instanceof Error ? err.message : 'Failed to connect')
      }
    } finally {
      setConnecting(null)
    }
  }

  const handleSaveApiKey = async () => {
    if (!apiKeyModal || !apiKeyInput.trim()) return
    try {
      await saveApiKey.mutateAsync({ provider: apiKeyModal.provider, api_key: apiKeyInput.trim() })
      toast.success(`${apiKeyModal.name} API key saved`)
      setApiKeyModal(null)
      setApiKeyInput('')
    } catch {
      toast.error('Failed to save API key')
    }
  }

  const handleDeleteApiKey = async (provider: string) => {
    try {
      await deleteApiKey.mutateAsync(provider)
      toast.success('API key removed')
    } catch {
      toast.error('Failed to remove API key')
    }
  }

  if (selected) {
    const conns = connectionsByProvider[selected.nango_provider_key] ?? []
    const hasApiKey = apiKeyProviders.has(selected.provider)
    const isApiKeyProvider = selected.auth_type === 'api_key'

    return (
      <div className="space-y-5">
        <button onClick={() => setSelected(null)} className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> All Apps
        </button>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ProviderIcon name={selected.name} provider={selected.provider} baseUrl={selected.base_url} size="lg" />
            <h2 className="text-lg font-semibold">{selected.name}</h2>
          </div>
          {isApiKeyProvider ? (
            <Button onClick={() => { setApiKeyModal(selected); setApiKeyInput('') }} variant={hasApiKey ? 'outline' : 'default'}>
              <Key className="size-4 mr-2" />
              {hasApiKey ? 'Update Key' : 'Add API Key'}
            </Button>
          ) : (
            <Button onClick={() => handleConnect(selected)} disabled={connecting !== null}>
              {connecting ? 'Connecting...' : 'Connect New'}
            </Button>
          )}
        </div>

        {isApiKeyProvider ? (
          <div>
            <h3 className="mb-3 text-sm font-medium text-muted-foreground">API Key</h3>
            {hasApiKey ? (
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="flex items-center gap-2">
                  <span className="inline-block size-2 rounded-full bg-green-500" />
                  <span className="text-sm font-medium">Key configured</span>
                </div>
                <Button size="sm" variant="destructive" onClick={() => handleDeleteApiKey(selected.provider)}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No API key configured. Click &quot;Add API Key&quot; to set one up.</p>
            )}
          </div>
        ) : (
          <div>
            <h3 className="mb-3 text-sm font-medium text-muted-foreground">Connected Accounts ({conns.length})</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {conns.map((c) => (
                <div key={c.nango_connection_id} className="flex items-center justify-between rounded-lg border p-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="inline-block size-2 rounded-full bg-green-500" />
                      <span className="text-sm font-medium">Active</span>
                      <span className="text-xs text-muted-foreground">
                        {(() => {
                          const diff = Date.now() - new Date(c.created_at).getTime()
                          const mins = Math.floor(diff / 60000)
                          if (mins < 1) return 'just now'
                          if (mins < 60) return `${mins}m ago`
                          const hrs = Math.floor(mins / 60)
                          if (hrs < 24) return `${hrs}h ago`
                          return `${Math.floor(hrs / 24)}d ago`
                        })()}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">{c.nango_connection_id.slice(0, 8)}...</div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => deleteMutation.mutate({ id: c.nango_connection_id, provider: selected!.nango_provider_key })}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              <button
                onClick={() => handleConnect(selected, true)}
                disabled={connecting !== null}
                className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-4 text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
              >
                <Plus className="size-5" />
                <span className="text-sm">Connect another account</span>
              </button>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder="Search integrations..." className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="flex gap-1 rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${tab === t ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}>
              {t}
            </button>
          ))}
        </div>
      </div>

      {catalog.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-lg border p-3">
              <Skeleton className="size-10 rounded-lg" />
              <Skeleton className="h-4 flex-1" />
              <Skeleton className="h-8 w-20 rounded-md" />
            </div>
          ))}
        </div>
      ) : filtered.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((item) => {
            const conns = connectionsByProvider[item.nango_provider_key] ?? []
            const hasApiKey = apiKeyProviders.has(item.provider)
            const isApiConnected = item.auth_type === 'api_key' && hasApiKey
            const isOAuthConnected = conns.length > 0
            const isConnected = isApiConnected || isOAuthConnected
            const isConnecting = connecting === item.nango_provider_key
            return (
              <div
                key={item.provider}
                onClick={() => isConnected ? setSelected(item) : handleConnect(item)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && (isConnected ? setSelected(item) : handleConnect(item))}
                className="flex items-center gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 cursor-pointer"
              >
                <ProviderIcon name={item.name} provider={item.provider} baseUrl={item.base_url} />
                <span className="flex-1 truncate text-sm font-medium">{item.name}</span>
                {isConnected ? (
                  <span className="flex items-center gap-1 text-sm text-green-600 dark:text-green-500">
                    <Check className="size-3.5" />
                    {isApiConnected ? 'Key Set' : `${conns.length} Active`}
                  </span>
                ) : (
                  <Button size="sm" variant="default" onClick={(e) => { e.stopPropagation(); handleConnect(item) }} disabled={isConnecting}>
                    {isConnecting ? 'Connecting...' : 'Connect'}
                  </Button>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <p className="py-12 text-center text-sm text-muted-foreground">{search ? 'No matches' : 'No providers available'}</p>
      )}

      {/* API Key Input Modal */}
      {apiKeyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-background p-6 shadow-lg">
            <div className="flex items-center gap-3 mb-4">
              <ProviderIcon name={apiKeyModal.name} provider={apiKeyModal.provider} baseUrl={apiKeyModal.base_url} size="lg" />
              <div>
                <h3 className="font-semibold">{apiKeyModal.name}</h3>
                <p className="text-sm text-muted-foreground">Enter your API key</p>
              </div>
            </div>
            <Input
              type="password"
              placeholder="Paste your API key here"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSaveApiKey()}
              className="mb-4"
              autoFocus
            />
            <p className="text-xs text-muted-foreground mb-4">
              Your API key is stored securely in our database and never shared.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setApiKeyModal(null)}>Cancel</Button>
              <Button onClick={handleSaveApiKey} disabled={!apiKeyInput.trim() || saveApiKey.isPending}>
                {saveApiKey.isPending ? 'Saving...' : 'Save Key'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
