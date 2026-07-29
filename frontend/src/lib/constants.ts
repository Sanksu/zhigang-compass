/** 用户角色 */
export type Role = 'guest' | 'user' | 'admin'

/** 角色中文名映射 */
export const ROLES: Record<Role, string> = {
  guest: '访客',
  user: '用户',
  admin: '管理员',
}

/** Token 刷新阈值（秒）— 提前 5 分钟刷新 */
export const TOKEN_REFRESH_MARGIN = 5 * 60

/** 应用名 */
export const APP_NAME = '智岗罗盘'
