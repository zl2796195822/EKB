const GREETING_BY_HOUR = [
  '凌晨好',
  '凌晨好',
  '凌晨好',
  '凌晨好',
  '清晨好',
  '早上好',
  '早上好',
  '早上好',
  '上午好',
  '上午好',
  '上午好',
  '中午好',
  '中午好',
  '下午好',
  '下午好',
  '下午好',
  '下午好',
  '傍晚好',
  '傍晚好',
  '晚上好',
  '晚上好',
  '晚上好',
  '晚上好',
  '晚上好',
] as const

export function getLocalGreeting(date: Date = new Date()): string {
  return GREETING_BY_HOUR[date.getHours()] ?? '你好'
}
