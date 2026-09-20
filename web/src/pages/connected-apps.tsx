import { useState, useEffect } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Shield, Trash2 } from 'lucide-react'

interface Grant {
  id: string
  client_id: string
  name: string
  scopes: string[]
  created_at: string
  last_used_at: string | null
}

export function ConnectedAppsPage() {
  const [grants, setGrants] = useState<Grant[]>([])
  const [loading, setLoading] = useState(true)
  const [revoking, setRevoking] = useState<string | null>(null)

  const load = async () => {
    try {
      const data = await api<Grant[]>('/api/v1/oauth/grants')
      setGrants(data)
    } catch {
      toast.error('Failed to load connected apps')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleRevoke = async (grantId: string, name: string) => {
    if (!confirm(`Revoke access for "${name}"? They will lose all access immediately.`)) return
    setRevoking(grantId)
    try {
      await api(`/api/v1/oauth/grants/${grantId}`, { method: 'DELETE' })
      toast.success(`Access revoked for ${name}`)
      setGrants((prev) => prev.filter((g) => g.id !== grantId))
    } catch {
      toast.error('Failed to revoke')
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Connected Apps</h1>
        <p className="text-muted-foreground">
          Apps you've authorized to use your Plugmere tools. Revoke any time.
        </p>
      </div>

      {loading ? (
        <div className="text-muted-foreground py-8 text-center">Loading...</div>
      ) : grants.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            <Shield className="mx-auto mb-3 size-8 opacity-40" />
            No apps connected yet.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {grants.map((g) => (
            <Card key={g.id}>
              <CardContent className="flex items-center justify-between py-4">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{g.name || g.client_id}</div>
                  <div className="text-sm text-muted-foreground">
                    {g.scopes.length > 0 ? g.scopes.join(', ') : 'Full access'}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    Connected {new Date(g.created_at).toLocaleDateString()}
                    {g.last_used_at && ` · Last used ${new Date(g.last_used_at).toLocaleDateString()}`}
                  </div>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => handleRevoke(g.id, g.name || g.client_id)}
                  disabled={revoking === g.id}
                >
                  <Trash2 className="mr-1 size-3" />
                  {revoking === g.id ? 'Revoking...' : 'Revoke'}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
