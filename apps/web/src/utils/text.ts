export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export type MarkedHtml = { __html: string }

export function markHitHighlight(
  text: string,
  highlights: string[],
): MarkedHtml {
  if (!text) {
    return { __html: '' }
  }
  const cleanHits = highlights
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0)
  if (cleanHits.length === 0) {
    return { __html: text }
  }
  const escapedHits = cleanHits.map(escapeRegExp)
  const pattern = new RegExp(`(${escapedHits.join('|')})`, 'gi')
  if (!pattern.test(text)) {
    return { __html: text }
  }
  pattern.lastIndex = 0
  const escaped = text.replace(/[&<>"]/g, (ch) => {
    switch (ch) {
      case '&':
        return '&amp;'
      case '<':
        return '&lt;'
      case '>':
        return '&gt;'
      case '"':
        return '&quot;'
      default:
        return ch
    }
  })
  const wrapPattern = new RegExp(
    cleanHits.map((hit) => escapeRegExp(hit.replace(/[&<>"]/g, (ch) => {
      switch (ch) {
        case '&': return '&amp;'
        case '<': return '&lt;'
        case '>': return '&gt;'
        case '"': return '&quot;'
        default: return ch
      }
    }))).join('|'),
    'gi',
  )
  const marked = escaped.replace(wrapPattern, (match) => `<mark class="hit-hl">${match}</mark>`)
  return { __html: marked }
}

type TimeUnit = {
  unit: Intl.RelativeTimeFormatUnitSingular
  ms: number
  threshold: number
}

const UNITS: TimeUnit[] = [
  { unit: 'year', ms: 365 * 24 * 60 * 60 * 1000, threshold: 12 * 30 * 24 * 60 * 60 * 1000 },
  { unit: 'month', ms: 30 * 24 * 60 * 60 * 1000, threshold: 30 * 24 * 60 * 60 * 1000 },
  { unit: 'day', ms: 24 * 60 * 60 * 1000, threshold: 24 * 60 * 60 * 1000 },
  { unit: 'hour', ms: 60 * 60 * 1000, threshold: 60 * 60 * 1000 },
  { unit: 'minute', ms: 60 * 1000, threshold: 60 * 1000 },
]

const LOCAL_ZH: Intl.RelativeTimeFormatUnit = 'second'
void LOCAL_ZH

export function relativeTime(ts: string | number | Date): string {
  const date = ts instanceof Date ? ts : new Date(ts)
  const now = Date.now()
  const diff = now - date.getTime()
  if (!isFinite(diff)) return ''
  const absDiff = Math.abs(diff)
  if (absDiff < 60 * 1000) {
    return '刚刚'
  }
  for (const { unit, ms, threshold } of UNITS) {
    if (absDiff >= threshold) {
      const value = Math.round(diff / ms)
      const absValue = Math.abs(value)
      switch (unit) {
        case 'year':
          return `${absValue} 年前`
        case 'month':
          return `${absValue} 个月前`
        case 'day':
          return `${absValue} 天前`
        case 'hour':
          return `${absValue} 小时前`
        case 'minute':
          return `${absValue} 分钟前`
      }
    }
  }
  return '刚刚'
}
