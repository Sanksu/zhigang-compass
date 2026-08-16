import { lazy, Suspense } from 'react'
import { Navigate, createBrowserRouter, RouterProvider } from 'react-router'
import { AppShell } from '@/components/layout/app-shell'
import { AuthGuard, GuestGuard } from '@/routes/guards'
import { CompassMark } from '@/components/layout/compass-mark'

/** 路由级懒加载 — 设计文档 §10.1 React Router v7 */
const LoginPage = lazy(() => import('@/routes/login-page').then((m) => ({ default: m.LoginPage })))
const RegisterPage = lazy(() => import('@/routes/register-page').then((m) => ({ default: m.RegisterPage })))
const DashboardPage = lazy(() => import('@/routes/dashboard-page').then((m) => ({ default: m.DashboardPage })))
const GraphPage = lazy(() => import('@/routes/graph-page').then((m) => ({ default: m.GraphPage })))
const ResumeMatchPage = lazy(() => import('@/routes/resume-match-page').then((m) => ({ default: m.ResumeMatchPage })))
const EvolutionPage = lazy(() => import('@/routes/evolution-page').then((m) => ({ default: m.EvolutionPage })))
const ProfilePage = lazy(() => import('@/routes/profile-page').then((m) => ({ default: m.ProfilePage })))
const AdminDashboardPage = lazy(() => import('@/routes/admin-dashboard-page').then((m) => ({ default: m.AdminDashboardPage })))
const AdminUsersPage = lazy(() => import('@/routes/admin-users-page').then((m) => ({ default: m.AdminUsersPage })))
const AdminCrawlPage = lazy(() => import('@/routes/admin-crawl-page').then((m) => ({ default: m.AdminCrawlPage })))
const AdminReviewPage = lazy(() => import('@/routes/admin-review-page').then((m) => ({ default: m.AdminReviewPage })))
const AdminLlmPage = lazy(() => import('@/routes/admin-llm-page').then((m) => ({ default: m.AdminLlmPage })))
const AdminSettingsPage = lazy(() => import('@/routes/admin-settings-page').then((m) => ({ default: m.AdminSettingsPage })))

function RouteLoading() {
  return (
    <div className="flex h-full items-center justify-center">
      <CompassMark size="md" spinning />
    </div>
  )
}

const protectedRoutes = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Suspense fallback={<RouteLoading />}><DashboardPage /></Suspense> },
      { path: 'graph', element: <Suspense fallback={<RouteLoading />}><GraphPage /></Suspense> },
      {
        path: 'resume-match',
        element: <AuthGuard requireRole={['user', 'admin']}><Suspense fallback={<RouteLoading />}><ResumeMatchPage /></Suspense></AuthGuard>,
      },
      { path: 'evolution', element: <AuthGuard><Suspense fallback={<RouteLoading />}><EvolutionPage /></Suspense></AuthGuard> },
      {
        path: 'profile',
        element: <AuthGuard><Suspense fallback={<RouteLoading />}><ProfilePage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminDashboardPage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/users',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminUsersPage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/crawl',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminCrawlPage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/review',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminReviewPage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/llm',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminLlmPage /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/settings',
        element: <Navigate to="/admin/settings/tasks" replace />,
      },
      {
        path: 'admin/settings/tasks',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminSettingsPage section="tasks" /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/settings/crawl',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminSettingsPage section="crawl" /></Suspense></AuthGuard>,
      },
      {
        path: 'admin/settings/evolution',
        element: <AuthGuard requireRole={['admin']}><Suspense fallback={<RouteLoading />}><AdminSettingsPage section="evolution" /></Suspense></AuthGuard>,
      },
    ],
  },
]

const router = createBrowserRouter([
  {
    path: '/login',
    element: <GuestGuard><Suspense fallback={<RouteLoading />}><LoginPage /></Suspense></GuestGuard>,
  },
  {
    path: '/register',
    element: <GuestGuard><Suspense fallback={<RouteLoading />}><RegisterPage /></Suspense></GuestGuard>,
  },
  ...protectedRoutes,
  { path: '*', element: <NotFound /> },
])

function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3">
      <CompassMark size="lg" spinning />
      <p className="text-sm text-ink-muted">页面未找到</p>
      <a href="/" className="text-sm text-ink underline hover:no-underline">返回首页</a>
    </div>
  )
}

export function AppRouter() {
  return <RouterProvider router={router} />
}
