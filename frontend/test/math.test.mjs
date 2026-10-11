import test from 'node:test'
import assert from 'node:assert/strict'
import { frequencyAt, gainAt, xAt, yAt, resample, zoom, pan, frequencyTicks, fitGain } from '../src/math.mjs'

const view = { minFreq: 20, maxFreq: 20000, minGain: -24, maxGain: 24 }
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`)

test('irregular measurements resample on the visible log frequency range', () => {
  const output = resample([[20, 0], [200, 20], [20000, 0]], { ...view, minFreq: 200, maxFreq: 2000 }, 3)
  close(output[0].frequency, 200)
  close(output[0].magnitude, 20)
  close(output[1].frequency, Math.sqrt(200 * 2000))
  close(output[1].magnitude, 15)
  close(output[2].magnitude, 10)
})

test('zoom keeps frequency and dB beneath cursor fixed', () => {
  const next = zoom(view, 0.5, 0.6, 0.3)
  close(frequencyAt(600, 1000, next), frequencyAt(600, 1000, view))
  const vertical = zoom(view, 0.5, 0.6, 0.3, true)
  close(gainAt(90, 300, vertical), gainAt(90, 300, view))
  close(xAt(1000, 1000, next), 1000 * Math.log10(1000 / next.minFreq) / Math.log10(next.maxFreq / next.minFreq))
  close(yAt(gainAt(60, 300, vertical), 300, vertical), 60)
})

test('panning and zooming stay inside the audible frequency limits', () => {
  const narrow = zoom(view, 0.1, 0.5, 0.5)
  const left = pan(narrow, 100, 0)
  close(left.minFreq, 20)
  const right = pan(narrow, -100, 0)
  close(right.maxFreq, 20000)
  const reset = zoom(narrow, 1000, 0.5, 0.5)
  close(reset.minFreq, 20)
  close(reset.maxFreq, 20000)
  assert.ok(frequencyTicks({ ...view, minFreq: 8000, maxFreq: 9500 }, 800).includes(9000))
})

test('auto-fit covers visible curves with padded, grid-aligned limits', () => {
  const curves = [{ points: [[20, -40], [100, -6], [1000, 0], [8000, 9.2], [20000, 50]] },
    { points: [[200, 2], [5000, -1]] }]
  const fitted = fitGain(curves, { ...view, minFreq: 50, maxFreq: 10000 })
  assert.deepEqual(fitted, { minGain: -10, maxGain: 15 })
  const flat = fitGain([{ points: [[20, 1], [20000, 1.5]] }], view)
  assert.ok(flat.maxGain - flat.minGain >= 12 && flat.minGain <= 1 && flat.maxGain >= 1.5)
  assert.equal(fitGain([], view), null)
  assert.equal(fitGain([{ points: [[20, NaN]] }], view), null)
})
