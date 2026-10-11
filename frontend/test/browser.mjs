import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1100, height: 680 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.addInitScript(() => {
  window.events = []
  window.fixture = { curves: [{ name: 'Fixture response', color: '#2f6fb0', points: [[20, 0], [1000, 4], [20000, -2]] }],
    filters: [{ id: 1, frequency: 1000, gain: 2, q: 0.7, kind: 'PEAK', enabled: true }],
    raw: [], nodeBase: [], rawBase: [], addBase: [], label: 'Synthetic interaction fixture', low: -24, high: 24,
    legend: [{ key: 'original', label: 'Fixture response', color: '#2f6fb0', checked: true, group: 'acoustic' },
      { key: 'target', label: 'Target', color: '#f2c14e', dashed: true, checked: false, group: 'acoustic' }],
    theme: { mode: 'light', tokens: { bg: '#f6f8fb', accent: '#2563d4' } } }
  window.qt = { webChannelTransport: {} }
  window.QWebChannel = class {
    constructor(transport, callback) {
      let receive
      callback({ objects: { bridge: {
        publish: { connect: fn => { receive = fn } },
        ready: () => receive(JSON.stringify(window.fixture)),
        submit: text => {
          const event = JSON.parse(text)
          window.events.push(event)
          if (event.op === 'filter') window.fixture.filters[event.index] = event.filter
          if (event.op === 'add') window.fixture.filters.push({ id: 2, frequency: event.frequency, gain: event.gain, q: 0.7, kind: 'PEAK', enabled: true })
          if (event.op === 'delete') window.fixture.filters.splice(event.index, 1)
          if (event.op === 'view') window.fixture.legend.find(item => item.key === event.key).checked = event.value
          if (event.op !== 'begin') receive(JSON.stringify(window.fixture))
        }
      } } })
    }
  }
})
try {
  await page.goto(pathToFileURL(path.join(root, 'src/heytap_eq/web/index.html')).href)
  await page.waitForFunction(() => window.studioInspect?.().svgPaths > 0)
  const plot = page.locator('.plot')
  const box = await plot.boundingBox()
  await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.6)
  await page.mouse.wheel(0, -100)
  await page.waitForTimeout(100)
  const zoomed = await page.evaluate(() => window.studioInspect().view)
  assert.ok(zoomed.minFreq > 20 && zoomed.maxFreq < 20000)
  await page.mouse.dblclick(box.x + box.width * 0.6, box.y + box.height * 0.5)
  await page.waitForFunction(() => window.fixture.filters.length === 2)
  assert.ok((await page.evaluate(() => window.events.at(-1).frequency)) > zoomed.minFreq)
  assert.deepEqual(await page.evaluate(() => window.studioInspect().view), zoomed)

  const node = page.locator('[data-filter="0"] circle')
  const point = await node.boundingBox()
  await page.mouse.move(point.x + point.width / 2, point.y + point.height / 2)
  await page.mouse.down()
  await page.mouse.move(point.x + 60, point.y - 25, { steps: 5 })
  await page.mouse.up()
  await page.waitForTimeout(150)
  const drag = await page.evaluate(() => window.events.filter(e => e.op === 'filter'))
  assert.ok(drag.some(e => e.phase === 'change'))
  assert.equal(drag.filter(e => e.phase === 'end').length, 1)
  assert.ok(drag.at(-1).filter.frequency > 1000)
  assert.ok(drag.at(-1).filter.gain > 2)
  const moved = await node.boundingBox()
  await page.mouse.move(moved.x + moved.width / 2, moved.y + moved.height / 2)
  await page.mouse.wheel(0, 100)
  await page.waitForTimeout(100)
  assert.ok((await page.evaluate(() => window.fixture.filters[0].q)) > 0.7)
  assert.deepEqual(await page.evaluate(() => window.studioInspect().view), zoomed)
  await page.evaluate(() => {
    window.fixture.filters[0].q = 24
    window.studioUpdate(window.fixture)
  })
  await page.waitForTimeout(50)
  await page.mouse.wheel(0, 100)
  await page.waitForFunction(() => window.fixture.filters[0].q > 24)
  await node.click({ button: 'right' })
  await page.getByLabel('增益 dB', { exact: true }).fill('0')
  await page.getByLabel('增益 dB', { exact: true }).press('Enter')
  await page.waitForFunction(() => window.fixture.filters[0].gain === 0)
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await page.waitForFunction(() => window.fixture.filters.length === 1)
  // Schema 2: legend chips toggle curves through Python, theme tokens restyle the page.
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()), '#2563d4')
  assert.equal(await page.evaluate(() => window.studioInspect().theme), 'light')
  // High contrast: the page is told so, draws curves 1.5x thicker and thickens control edges.
  const strokeWidth = () => page.evaluate(() => {
    const curve = [...document.querySelectorAll('.plot svg path')].find(p => getComputedStyle(p).stroke === 'rgb(47, 111, 176)')
    return curve ? parseFloat(getComputedStyle(curve).strokeWidth) : null
  })
  const standardWidth = await strokeWidth()
  assert.ok(standardWidth > 0)
  const setContrast = (contrast, lineScale) => page.evaluate(([contrast, lineScale]) => {
    window.fixture.theme = { ...window.fixture.theme, contrast, lineScale }
    window.studioUpdate(JSON.stringify(window.fixture))  // a fresh payload, as the bridge sends
  }, [contrast, lineScale])
  await setContrast('high', 1.5)
  await page.waitForFunction(() => window.studioInspect().contrast === 'high')
  assert.equal(await strokeWidth(), standardWidth * 1.5)
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('header button')).borderTopWidth), '2px')
  await setContrast('standard', 1)
  await page.waitForFunction(() => window.studioInspect().contrast === 'standard')
  assert.equal(await strokeWidth(), standardWidth)
  await page.locator('.chip[data-key="target"]').click()
  assert.deepEqual(await page.evaluate(() => window.events.at(-1)), { op: 'view', key: 'target', value: true })
  assert.equal(await page.locator('.chip[data-key="target"]').getAttribute('aria-pressed'), 'true')
  await page.getByRole('button', { name: '适配', exact: true }).click()
  const fitted = await page.evaluate(() => window.studioInspect().view)
  assert.ok(fitted.maxGain - fitted.minGain < 48 && fitted.minGain <= -2 && fitted.maxGain >= 4)
  await page.locator('[data-filter="0"] circle').click({ button: 'right' })
  await page.keyboard.press('Delete')
  await page.waitForFunction(() => window.fixture.filters.length === 0)
  await page.evaluate(() => {
    window.fixture.curves = []
    window.fixture.actions = [{ label: '导入测量', command: 'import_measurements' }]
    window.studioUpdate(window.fixture)
  })
  await page.getByRole('button', { name: '导入测量' }).click()
  assert.deepEqual(await page.evaluate(() => window.events.at(-1)), { op: 'command', name: 'import_measurements' })
  assert.deepEqual(errors, [])
  await mkdir(path.join(root, 'reports'), { recursive: true })
  await page.screenshot({ path: path.join(root, 'reports/dsssp-editor.png') })
  await writeFile(path.join(root, 'reports/frontend-interactions.json'), JSON.stringify({ status: 'passed', checks: ['log resampling', 'cursor zoom', 'double click add', 'live drag', 'wheel Q', 'zoom retained', 'zero gain edit', 'delete', 'theme tokens', 'high contrast', 'legend toggle', 'auto-fit', 'delete key', 'empty-state command'], errors }, null, 2))
} finally {
  await browser.close()
}
