import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import type { RegistryTool } from '@/lib/types'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { toast } from 'sonner'
import { Upload, Pencil, Trash2 } from 'lucide-react'

export function ToolsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [providerFilter, setProviderFilter] = useState('')
  const [editTarget, setEditTarget] = useState<RegistryTool | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<RegistryTool | null>(null)
  const [editDesc, setEditDesc] = useState('')
  const [editMethod, setEditMethod] = useState('GET')
  const [editPath, setEditPath] = useState('')

  const tools = useQuery<RegistryTool[]>({
    queryKey: ['registry-tools', providerFilter, search],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '200' })
      if (providerFilter) params.set('provider', providerFilter)
      if (search) params.set('q', search)
      return api(`/api/v1/tools?${params}`)
    },
  })

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api(`/api/v1/tools/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['registry-tools'] })
      toast.success('Tool updated')
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Failed to update tool')
    },
  })

  const patchTool = useMutation({
    mutationFn: ({ id, ...body }: { id: string; description: string; method: string; path: string }) =>
      api(`/api/v1/tools/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => {
      setEditTarget(null)
      queryClient.invalidateQueries({ queryKey: ['registry-tools'] })
      toast.success('Tool updated')
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Failed to update tool')
    },
  })

  const deleteTool = useMutation({
    mutationFn: (id: string) => api(`/api/v1/tools/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      setDeleteTarget(null)
      queryClient.invalidateQueries({ queryKey: ['registry-tools'] })
      toast.success('Tool deleted')
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Failed to delete tool')
    },
  })

  const importTools = useMutation({
    mutationFn: () => api('/api/v1/tools/import', { method: 'POST' }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['registry-tools'] })
      const r = data as { inserted: number; updated: number; skipped: number }
      toast.success(`Import: ${r.inserted} inserted, ${r.updated} updated, ${r.skipped} unchanged`)
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Import failed')
    },
  })

  const providers = tools.data ? [...new Set(tools.data.map((t) => t.provider))].sort() : []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Tools Registry</h1>
        <Button variant="outline" onClick={() => importTools.mutate()} disabled={importTools.isPending}>
          <Upload className="mr-1.5 size-4" />
          {importTools.isPending ? 'Importing...' : 'Import providers.json'}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <div className="grid gap-2">
              <Label>Search</Label>
              <Input
                placeholder="Tool name or description"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-64"
              />
            </div>
            <div className="grid gap-2">
              <Label>Provider</Label>
              <Select value={providerFilter} onValueChange={setProviderFilter}>
                <SelectTrigger className="w-48"><SelectValue placeholder="All providers" /></SelectTrigger>
                <SelectContent>
                  {providers.map((p) => (
                    <SelectItem key={p} value={p}>{p}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <CardDescription>
            {tools.data ? `${tools.data.length} tools` : 'Loading...'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {tools.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : tools.data && tools.data.length > 0 ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Method</TableHead>
                    <TableHead>Path</TableHead>
                    <TableHead>Version</TableHead>
                    <TableHead>Enabled</TableHead>
                    <TableHead className="w-20" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tools.data.map((t) => (
                    <TableRow key={t.id}>
                      <TableCell>
                        <div className="font-mono text-xs">{t.name}</div>
                        {t.description && (
                          <div className="max-w-[300px] truncate text-xs text-muted-foreground">{t.description}</div>
                        )}
                      </TableCell>
                      <TableCell><Badge variant="outline" className="text-xs">{t.provider}</Badge></TableCell>
                      <TableCell><Badge variant={t.method === 'GET' ? 'secondary' : 'default'} className="text-xs">{t.method}</Badge></TableCell>
                      <TableCell className="max-w-[200px] truncate font-mono text-xs text-muted-foreground">{t.path}</TableCell>
                      <TableCell className="text-xs tabular-nums">v{t.version}</TableCell>
                      <TableCell>
                        <Switch
                          checked={t.enabled}
                          onCheckedChange={(enabled) => toggleEnabled.mutate({ id: t.id, enabled })}
                        />
                      </TableCell>
                      <TableCell className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setEditTarget(t)
                            setEditDesc(t.description)
                            setEditMethod(t.method)
                                setEditPath(t.path)
                              }}
                            >
                              <Pencil className="size-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setDeleteTarget(t)}
                              className="text-destructive hover:text-destructive"
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No tools in registry. Click "Import providers.json" to populate.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Edit Dialog */}
      <Dialog open={!!editTarget} onOpenChange={() => setEditTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Tool</DialogTitle>
            <DialogDescription>{editTarget?.name}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label>Description</Label>
              <Input value={editDesc} onChange={(e) => setEditDesc(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label>Method</Label>
              <Select value={editMethod} onValueChange={setEditMethod}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                    <SelectItem key={m} value={m}>{m}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Path</Label>
              <Input value={editPath} onChange={(e) => setEditPath(e.target.value)} className="font-mono text-xs" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>Cancel</Button>
            <Button onClick={() => editTarget && patchTool.mutate({ id: editTarget.id, description: editDesc, method: editMethod, path: editPath })} disabled={patchTool.isPending}>
              {patchTool.isPending ? 'Saving...' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete tool?</DialogTitle>
            <DialogDescription>
              This permanently removes <strong>{deleteTarget?.name}</strong> from the registry.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => deleteTarget && deleteTool.mutate(deleteTarget.id)} disabled={deleteTool.isPending}>
              {deleteTool.isPending ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
