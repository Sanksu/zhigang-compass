/** 用户角色 */
export type Role = 'guest' | 'user' | 'admin'

/** 角色中文名映射 */
export const ROLES: Record<Role, string> = {
  guest: '访客',
  user: '用户',
  admin: '管理员',
}
