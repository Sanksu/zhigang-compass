/**
 * Token 管理 — access_token 由后端 Set-Cookie（httpOnly），前端不可读
 *
 * refresh_token 仅在内存中存留，不写入 localStorage / sessionStorage
 */

let _refreshToken: string | null = null

/** 设置 refresh token（登录后由后端返回） */
export function setRefreshToken(token: string | null) {
  _refreshToken = token
}

/** 获取 refresh token（仅在需要刷新 access_token 时调用） */
export function getRefreshToken(): string | null {
  return _refreshToken
}
