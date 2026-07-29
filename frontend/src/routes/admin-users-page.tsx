import { PagePlaceholder } from '@/components/layout/page-placeholder'

export function AdminUsersPage() {
  return (
    <PagePlaceholder
      title="账户管理"
      description="用户 CRUD、RBAC 权限分配、启用/禁用"
      specRef="§12.2 账户管理 · GET /api/v1/admin/users"
    />
  )
}
