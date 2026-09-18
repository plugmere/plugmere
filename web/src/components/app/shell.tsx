import { Navigate, Outlet, useLocation, Link } from 'react-router-dom'
import { useAuth } from '@/lib/auth'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { LogOut, LayoutDashboard, Link2, Key, Users, ScrollText, Cable, Wrench, Menu } from 'lucide-react'
import { supabase } from '@/lib/supabase'

const NAV_ITEMS: { to: string; label: string; icon: typeof LayoutDashboard; admin?: boolean }[] = [
  { to: '/overview', label: 'Overview', icon: LayoutDashboard, admin: true },
  { to: '/integrations', label: 'Integrations', icon: Link2 },
  { to: '/connect', label: 'Connect', icon: Cable },
  { to: '/api-keys', label: 'API Keys', icon: Key },
  { to: '/users', label: 'Users', icon: Users, admin: true },
  { to: '/tools', label: 'Tools', icon: Wrench, admin: true },
  { to: '/logs', label: 'Logs', icon: ScrollText, admin: true },
]

export function RequireAuth() {
  const { session, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Skeleton className="h-8 w-48" />
      </div>
    )
  }
  if (!session) return <Navigate to="/login" state={{ from: location }} replace />
  return <Outlet />
}

export function RequireAdmin() {
  const { profile, loading } = useAuth()
  if (loading) return null
  // Members bounce to their home page (/overview would loop)
  if (profile?.role !== 'admin') return <Navigate to="/integrations" replace />
  return <Outlet />
}

export function Shell() {
  const { profile, session } = useAuth()
  const location = useLocation()
  const isAdmin = profile?.role === 'admin'

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  const visibleNav = NAV_ITEMS.filter(({ admin }) => !admin || isAdmin)

  return (
    <div className="flex h-screen flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4 sm:gap-4 sm:px-6">
        <Link to="/overview" className="text-lg font-semibold tracking-tight hover:opacity-80 transition-opacity"><span className="hidden sm:inline">Plugmere</span><span className="sm:hidden">P</span></Link>
        {/* Desktop nav */}
        <nav className="ml-8 hidden gap-1 md:flex">
          {visibleNav.map(({ to, label, icon: Icon }) => {
            const active = location.pathname === to
            return (
              <Button
                key={to}
                variant={active ? 'secondary' : 'ghost'}
                size="sm"
                asChild
              >
                <Link to={to}>
                  <Icon className="mr-1.5 size-4" />
                  {label}
                </Link>
              </Button>
            )
          })}
        </nav>
        {/* Mobile: one corner menu with nav + account */}
        <div className="ml-auto md:hidden">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Menu">
                <Menu className="size-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              {visibleNav.map(({ to, label, icon: Icon }) => {
                const active = location.pathname === to
                return (
                  <DropdownMenuItem key={to} asChild className={active ? 'font-semibold' : ''}>
                    <Link to={to} className="cursor-pointer">
                      <Icon className="mr-2 size-4" />
                      {label}
                    </Link>
                  </DropdownMenuItem>
                )
              })}
              <DropdownMenuSeparator />
              <div className="px-2 py-1.5 text-sm font-medium break-all">{profile?.name || profile?.email}</div>
              <div className="px-2 pb-1.5 text-xs text-muted-foreground capitalize">{profile?.role}</div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={signOut} className="cursor-pointer text-destructive focus:text-destructive">
                <LogOut className="mr-2 size-4" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {/* Desktop account menu */}
        <div className="ml-auto hidden items-center gap-3 md:flex">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm">
                <span className="truncate">{profile?.email ?? session?.user?.email ?? 'Account'}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <div className="px-2 py-1.5 text-sm font-medium">{profile?.name || profile?.email}</div>
              <div className="px-2 pb-1.5 text-xs text-muted-foreground capitalize">{profile?.role}</div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={signOut} className="cursor-pointer text-destructive focus:text-destructive">
                <LogOut className="mr-2 size-4" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto p-4 sm:p-6">
        <Outlet />
      </main>
      <footer className="border-t px-4 py-2 text-center text-xs text-muted-foreground sm:px-6">
        Logos provided by <a href="https://logo.dev" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">Logo.dev</a>
      </footer>
    </div>
  )
}
