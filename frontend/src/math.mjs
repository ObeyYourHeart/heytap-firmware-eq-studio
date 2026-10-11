export const clamp = (v, low, high) => Math.min(high, Math.max(low, v))
export const frequencyAt = (x, width, view) => 10 ** (
  Math.log10(view.minFreq) + clamp(x / width, 0, 1) * Math.log10(view.maxFreq / view.minFreq)
)
export const gainAt = (y, height, view) => view.maxGain - clamp(y / height, 0, 1) * (view.maxGain - view.minGain)
export const xAt = (frequency, width, view) => width * Math.log10(frequency / view.minFreq) / Math.log10(view.maxFreq / view.minFreq)
export const yAt = (gain, height, view) => height * (view.maxGain - gain) / (view.maxGain - view.minGain)

// Measurement files may be irregularly sampled. DSSSP positions curve values
// by array index, so resample explicitly on the CURRENT logarithmic viewport.
export function interpolate(points, frequency) {
  if (!points?.length) return 0
  if (frequency <= points[0][0]) return points[0][1]
  if (frequency >= points.at(-1)[0]) return points.at(-1)[1]
  let low = 0, high = points.length - 1
  while (high - low > 1) {
    const middle = (low + high) >> 1
    if (points[middle][0] <= frequency) low = middle
    else high = middle
  }
  const a = points[low], b = points[high]
  return a[1] + (b[1] - a[1]) * Math.log(frequency / a[0]) / Math.log(b[0] / a[0])
}

export function resample(points, view, count) {
  return Array.from({ length: count }, (_, i) => {
    const frequency = frequencyAt(i, count - 1, view)
    return { frequency, magnitude: interpolate(points, frequency) }
  })
}

function boundedRange(low, high, min, max) {
  const span = Math.min(high - low, max - min)
  low = clamp(low, min, max - span)
  return [low, low + span]
}

export function zoom(view, factor, xRatio, yRatio, vertical = false) {
  if (vertical) {
    const span = clamp((view.maxGain - view.minGain) * factor, 2, 180)
    const anchor = view.maxGain - yRatio * (view.maxGain - view.minGain)
    const [low, high] = boundedRange(anchor - (1 - yRatio) * span, anchor + yRatio * span, -100, 120)
    return { ...view, minGain: low, maxGain: high }
  }
  const low = Math.log10(view.minFreq), high = Math.log10(view.maxFreq)
  const span = clamp((high - low) * factor, 0.025, 3)
  const anchor = low + xRatio * (high - low)
  const [a, b] = boundedRange(anchor - xRatio * span, anchor + (1 - xRatio) * span, Math.log10(20), Math.log10(20000))
  return { ...view, minFreq: 10 ** a, maxFreq: 10 ** b }
}

export function pan(view, dxRatio, dyRatio) {
  const low = Math.log10(view.minFreq), high = Math.log10(view.maxFreq)
  const delta = -dxRatio * (high - low)
  const [a, b] = boundedRange(low + delta, high + delta, Math.log10(20), Math.log10(20000))
  const dy = dyRatio * (view.maxGain - view.minGain)
  const [c, d] = boundedRange(view.minGain + dy, view.maxGain + dy, -100, 120)
  return { ...view, minFreq: 10 ** a, maxFreq: 10 ** b, minGain: c, maxGain: d }
}

export function frequencyTicks(view, width) {
  const result = []
  for (let decade = 1; decade <= 4; decade++) {
    for (const n of (Math.log10(view.maxFreq / view.minFreq) > 1 ? [1, 2, 5] : [1, 2, 3, 4, 5, 6, 7, 8, 9])) {
      const f = n * 10 ** decade
      if (f >= view.minFreq && f <= view.maxFreq) result.push(f)
    }
  }
  let previous = -Infinity
  return result.filter(f => {
    const x = xAt(f, width, view)
    if (x - previous < 55) return false
    previous = x
    return true
  })
}

export const formatHz = f => f >= 1000 ? `${Number((f / 1000).toFixed(2))}k` : `${Math.round(f)}`

// Vertical range that shows every visible curve inside the current frequency window,
// padded and snapped to the grid step so the axis labels stay round.
export function fitGain(curves, view, { padding = 3, step = 5, minSpan = 12 } = {}) {
  let low = Infinity, high = -Infinity
  for (const curve of curves) {
    for (const [frequency, gain] of curve.points) {
      if (frequency < view.minFreq || frequency > view.maxFreq || !Number.isFinite(gain)) continue
      low = Math.min(low, gain)
      high = Math.max(high, gain)
    }
  }
  if (!Number.isFinite(low)) return null
  low = Math.floor((low - padding) / step) * step
  high = Math.ceil((high + padding) / step) * step
  if (high - low < minSpan) {
    const middle = (low + high) / 2
    low = Math.floor((middle - minSpan / 2) / step) * step
    high = low + Math.ceil(minSpan / step) * step
  }
  return { minGain: clamp(low, -100, 110), maxGain: clamp(high, -90, 120) }
}
