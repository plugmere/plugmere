import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { supabase } from '@/lib/supabase'

export function OAuthApprovePage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { session } = useAuth()
  const clientId = params.get('client_id') || ''
  const redirectUri = params.get('redirect_uri') || ''
  const scope = params.get('scope') || ''
  const state = params.get('state') || ''
  const codeChallenge = params.get('code_challenge') || ''

  const [clientName, setClientName] = useState(clientId)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [googleLoading, setGoogleLoading] = useState(false)

  useEffect(() => {
    if (clientId) {
      api<{ name: string }>(`/api/v1/oauth/client/${clientId}/info`).then(
        (d) => setClientName(d.name || clientId),
        () => setClientName(clientId),
      )
    }
  }, [clientId])

  const handleGoogleLogin = async () => {
    setGoogleLoading(true)
    const approveUrl = window.location.href
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: approveUrl },
    })
    setGoogleLoading(false)
    if (error) toast.error(error.message)
  }

  const handleApprove = async () => {
    if (!clientId || !redirectUri) {
      setError('Missing client_id or redirect_uri')
      return
    }
    setLoading(true)
    try {
      const res = await api<{ redirect_to: string }>('/api/v1/oauth/approve', {
        method: 'POST',
        body: JSON.stringify({
          client_id: clientId,
          redirect_uri: redirectUri,
          scope,
          state,
          code_challenge: codeChallenge || undefined,
        }),
      })
      window.location.href = res.redirect_to
    } catch (e: any) {
      setLoading(false)
      const msg = e?.message || 'Approval failed'
      if (msg.includes('Not invited')) {
        setError('Your account is not on the invite list. Ask the admin for access.')
      } else {
        setError(msg)
      }
      toast.error(msg)
    }
  }

  if (!clientId || !redirectUri) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle>Invalid Request</CardTitle>
            <CardDescription>Missing client_id or redirect_uri in the authorization request.</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Authorize Access</CardTitle>
          <CardDescription>
            <strong>{clientName}</strong> is requesting access to your Plugmere tools.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!session ? (
            <>
              <p className="text-sm text-muted-foreground text-center">Sign in with Google to approve this request.</p>
              <Button variant="outline" className="w-full" onClick={handleGoogleLogin} disabled={googleLoading}>
                {googleLoading ? 'Redirecting to Google...' : 'Sign in with Google'}
              </Button>
            </>
          ) : (
            <>
              {scope && (
                <div className="rounded-lg bg-muted p-3 text-sm">
                  <div className="font-medium mb-1">Requested permissions:</div>
                  <div className="text-muted-foreground">{scope}</div>
                </div>
              )}
              {error && (
                <div className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
              )}
              <div className="flex gap-3">
                <Button variant="outline" className="flex-1" onClick={() => window.history.back()} disabled={loading}>
                  Deny
                </Button>
                <Button className="flex-1" onClick={handleApprove} disabled={loading}>
                  {loading ? 'Approving...' : 'Approve'}
                </Button>
              </div>
            </>
          )}
          <p className="text-center text-xs text-muted-foreground">
            You can revoke this access anytime from your dashboard under Connected Apps.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
