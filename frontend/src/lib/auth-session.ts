/** 登录、退出和账户切换使在途旧请求失效；令牌自动轮换沿用同一会话代次。 */
let epoch = 0
export function authSessionEpoch() { return epoch }
export function invalidateAuthSession() { epoch += 1 }
export class AuthSessionChangedError extends Error {
  constructor() { super('登录状态已变更，请重试'); this.name = 'AuthSessionChangedError' }
}
