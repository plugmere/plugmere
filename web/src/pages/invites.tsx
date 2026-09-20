import { useState, useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Trash2, UserPlus } from 'lucide-react'

interface Invite {
  email: string
  created_at: string
}

export function InvitesPage() {
  const [invites, setInvites] = useState<Invite[]>([])
  const [loading, setLoading] = useState(true)
  const [email, setEmail] = useState('')
  const [adding, setAdding] = useState(false)

  const load = async () => {
    try {
      const data = await api<Invite[]>('/api/v1/invites')
      setInvites(data)
    } catch {
      toast.error('Failed to load invites')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    const addr = email.trim().toLowerCase()
    if (!addr) return
    setAdding(true)
    try {
      await api('/api/v1/invites', { method: 'POST', body: JSON.stringify({ email: addr }) })
      toast.success(`Invited ${addr}`)
      setEmail('')
      setInvites((prev) => [{ email: addr, created_at: new Date().toISOString() }, ...prev])
    } catch {
      toast.error('Failed to add invite')
    } finally {
      setAdding(false)
    }
  }

  const handleRemove = async (addr: string) => {
    if (!confirm(`Remove ${addr} from invite list?`)) return
    try {
      await api(`/api/v1/invites/${encodeURIComponent(addr)}`, { method: 'DELETE' })
      toast.success(`Removed ${addr}`)
      setInvites((prev) => prev.filter((i) => i.email !== addr))
    } catch {
      toast.error('Failed to remove')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Invite List</h1>
        <p className="text-muted-foreground">
          Control who can connect via OAuth. People not on this list cannot authorize third-party apps.
        </p>
      </div>

      <Card>
        <CardContent className="py-4">
          <form onSubmit={handleAdd} className="flex gap-2">
            <Input
              type="email"
              placeholder="user@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex-1"
            />
            <Button type="submit" disabled={adding || !email.trim()}>
              <UserPlus className="mr-1 size-4" />
              {adding ? 'Adding...' : 'Add'}
            </Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <div className="text-muted-foreground py-8 text-center">Loading...</div>
      ) : invites.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No one invited yet. The invite list is empty — in this state, OAuth is effectively open (anyone with your dashboard login can connect).
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {invites.map((inv) => (
            <Card key={inv.email}>
              <CardContent className="flex items-center justify-between py-3">
                <div>
                  <div className="font-medium">{inv.email}</div>
                  <div className="text-xs text-muted-foreground">
                    Added {new Date(inv.created_at).toLocaleDateString()}
                  </div>
                </div>
                <Button variant="ghost" size="sm" onClick={() => handleRemove(inv.email)}>
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
