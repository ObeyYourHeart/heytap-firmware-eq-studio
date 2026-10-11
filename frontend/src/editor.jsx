import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { FrequencyResponseGraph, FrequencyResponseCurve, FilterPoint } from 'dsssp'
import { clamp, fitGain, formatHz, frequencyAt, gainAt, xAt, yAt, interpolate, resample, zoom, pan, frequencyTicks } from './math.mjs'
import { SCHEMA, applyTheme, connect, send } from './bridge.js'
import { Inspector, noGain } from './Inspector.jsx'
import { Legend } from './Legend.jsx'
import './editor.css'

const defaultView = { minFreq: 20, maxFreq: 20000, minGain: -24, maxGain: 24 }
const initial = { schema: SCHEMA, curves: [], filters: [], raw: [], legend: [], label: '修正 EQ', rawNodes: true,
  empty: '双击图形添加 PEQ，或导入 EQ／频响。' }

const gestures = [
  ['双击空白处', '添加 PEQ'], ['拖动节点', '调频率／增益'], ['节点上滚轮', '调 Q'], ['右键或单击节点', '编辑参数'],
  ['Delete', '删除所选 PEQ'], ['空白处滚轮', '缩放频率'], ['Shift＋滚轮', '缩放 dB'], ['拖动空白处', '平移']]

function Editor() {
  const [data, setData] = useState(initial)
  const [view, setView] = useState(defaultView)
  const [size, setSize] = useState(null)
  const [selected, setSelected] = useState(null)
  const [pointer, setPointer] = useState(null)
  const [help, setHelp] = useState(false)
  const plotRef = useRef(null)
  const panRef = useRef(null)
  const latest = useRef({})
  latest.current = { data, view, size, selected }

  const fitTo = (payload, base) => {
    const gain = fitGain(payload.curves || [], base)
    return gain ? { ...base, ...gain } : base
  }

  useEffect(() => {
    connect(payload => {
      applyTheme(payload.theme)
      setData(payload)
      if (payload.reset || payload.fit) {
        setView(v => {
          const base = payload.reset ? { ...defaultView, minGain: payload.low, maxGain: payload.high } : v
          return payload.fit ? fitTo(payload, base) : base
        })
      }
    })
    window.studioInspect = () => ({ ready: true, schema: SCHEMA, curves: latest.current.data.curves.length,
      filters: latest.current.data.filters.length, legend: latest.current.data.legend?.length || 0,
      view: latest.current.view, svgPaths: document.querySelectorAll('.plot svg path').length,
      width: latest.current.size?.width || 0, height: latest.current.size?.height || 0,
      selected: latest.current.selected, theme: document.documentElement.dataset.theme || null })
    const resize = new ResizeObserver(entries => {
      const r = entries[0].contentRect
      setSize({ width: Math.max(10, r.width), height: Math.max(10, r.height) })
    })
    resize.observe(plotRef.current)
    const keys = e => {
      if (e.target.closest?.('input, select, textarea')) return
      const index = latest.current.selected
      if (e.key === 'Escape') { setSelected(null); setHelp(false) }
      if ((e.key === 'Delete' || e.key === 'Backspace') && index !== null && latest.current.data.filters[index]) {
        send({ op: 'delete', index })
        setSelected(null)
      }
    }
    window.addEventListener('keydown', keys)
    return () => { resize.disconnect(); window.removeEventListener('keydown', keys) }
  }, [])

  useEffect(() => {
    const el = plotRef.current
    const wheel = e => {
      e.preventDefault()
      const node = e.target.closest('[data-node]')
      if (node) {
        if (node.dataset.filter !== undefined) {
          const index = Number(node.dataset.filter)
          const f = latest.current.data.filters[index]
          if (f) send({ op: 'filter', phase: 'end', index,
            filter: { ...f, q: Number(clamp(f.q + (e.deltaY > 0 ? 0.1 : -0.1), 0.01, 100).toFixed(2)) } })
        }
        return
      }
      const rect = el.getBoundingClientRect()
      const x = clamp((e.clientX - rect.left) / rect.width, 0, 1)
      const y = clamp((e.clientY - rect.top) / rect.height, 0, 1)
      setView(v => zoom(v, Math.exp(clamp(e.deltaY, -100, 100) * 0.0025), x, y, e.shiftKey))
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [])

  const width = size?.width || 0, height = size?.height || 0
  const coords = e => {
    const r = plotRef.current.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }
  const changeFilter = (f, index, event) => {
    const frequency = clamp(event.freq, 20, 20000)
    const base = interpolate(data.nodeBase, frequency)
    send({ op: 'filter', phase: event.ended ? 'end' : 'change', index,
      filter: { ...f, frequency, gain: noGain.has(f.kind) ? f.gain : clamp(event.gain - base, -60, 60), q: event.q } })
  }
  const addPoint = e => {
    if (e.target.closest('[data-node]') || !size) return
    const p = coords(e), frequency = frequencyAt(p.x, width, view)
    const gain = gainAt(p.y, height, view) - interpolate(data.addBase, frequency)
    send({ op: 'add', frequency, gain: clamp(gain, -60, 60) })
  }
  const toggle = (key, value) => {
    setData(d => ({ ...d, legend: d.legend.map(item => item.key === key ? { ...item, checked: value } : item) }))
    send({ op: 'view', key, value })
  }

  const ticks = useMemo(() => size ? frequencyTicks(view, width) : [], [view, width, size])
  const step = (view.maxGain - view.minGain) <= 12 ? 2 : (view.maxGain - view.minGain) <= 60 ? 5 : 10
  const yTicks = []
  for (let v = Math.ceil(view.minGain / step) * step; v <= view.maxGain; v += step) yTicks.push(v)
  const scale = useMemo(() => ({ ...view, dbSteps: step, dbLabels: false, majorTicks: ticks, octaveTicks: 0, octaveLabels: [] }),
    [view, step, ticks])
  const curves = useMemo(() => size ? data.curves.map(c => ({ ...c,
    magnitudes: resample(c.points, view, Math.min(4000, Math.max(800, Math.ceil(width * 2)))),
    left: clamp(xAt(c.points[0][0], width, view), 0, width),
    right: clamp(xAt(c.points.at(-1)[0], width, view), 0, width)
  })) : [], [data.curves, view, width, size])
  const selectedFilter = selected === null ? null : data.filters[selected]
  const tokens = data.theme?.tokens || {}
  const graphTheme = useMemo(() => ({
    background: { grid: { lineColor: tokens['grid'] || '#2b3a50' }, label: { color: 'transparent' },
      gradient: { start: tokens['plot-top'] || '#131e2d', stop: tokens['plot-bottom'] || '#0c131d' } },
    filters: { point: { radius: 9, label: { fontSize: 11 } } } }), [tokens['grid'], tokens['plot-top'], tokens['plot-bottom']])
  const spl = data.splOffset !== null && data.splOffset !== undefined

  return <main className="editor">
    <header className="top">
      <span className="title">{data.label}</span>
      {data.notice && <span className={`notice ${data.notice.tone || 'info'}`} title={data.notice.detail}>{data.notice.text}</span>}
      <span className="spacer" />
      <button type="button" onClick={() => send({ op: 'add', frequency: Math.sqrt(view.minFreq * view.maxFreq), gain: 0 })}>＋ PEQ</button>
      <button type="button" title="按当前显示的曲线适配纵轴" onClick={() => setView(v => fitTo(data, v))}>适配</button>
      <button type="button" title="恢复 20 Hz–20 kHz 与默认 dB 范围"
        onClick={() => setView({ ...defaultView, minGain: data.low ?? -24, maxGain: data.high ?? 24 })}>复位</button>
      <button type="button" className={`ghost${help ? ' on' : ''}`} aria-label="操作说明" aria-expanded={help}
        onClick={() => setHelp(h => !h)}>?</button>
    </header>
    {data.legend?.length > 0 && <Legend items={data.legend} dacMode={data.dacMode} onToggle={toggle}
      onDacMode={value => send({ op: 'view', key: 'dac_mode', value })} />}
    <div className="graph-shell">
      {size && ticks.map(f => <span className="x-tick" key={f} style={{ left: 48 + xAt(f, width, view) }}>{formatHz(f)}</span>)}
      {size && yTicks.map(v => <span className="y-tick" key={v} style={{ top: 4 + yAt(v, height, view) }}>{v}</span>)}
      {size && spl && yTicks.map(v => <span className="spl-tick" key={v}
        style={{ top: 4 + yAt(v, height, view) }}>{(v + data.splOffset).toFixed(0)}</span>)}
      {spl && <span className="spl-label">SPL dB</span>}
      <div className="plot" ref={plotRef} style={{ right: spl ? 48 : 10 }} onDoubleClick={addPoint}
        onContextMenu={e => {
          e.preventDefault()
          const node = e.target.closest('[data-filter]')
          if (node) setSelected(Number(node.dataset.filter))
        }}
        onPointerDown={e => {
          if (e.button !== 0 || e.target.closest('[data-node], button')) return
          panRef.current = { ...coords(e), view }
          e.currentTarget.setPointerCapture(e.pointerId)
        }}
        onPointerMove={e => {
          if (!size) return
          const p = coords(e)
          setPointer({ frequency: frequencyAt(p.x, width, view), gain: gainAt(p.y, height, view) })
          const start = panRef.current
          if (start) setView(pan(start.view, (p.x - start.x) / width, (p.y - start.y) / height))
        }}
        onPointerUp={() => { panRef.current = null }}
        onPointerCancel={() => { panRef.current = null }}
        onPointerLeave={() => setPointer(null)}>
        {size && <FrequencyResponseGraph width={width} height={height} scale={scale} ariaLabel="交互式频响与 PEQ 编辑器"
          theme={graphTheme}>
          <defs><clipPath id="plot-clip"><rect width={width} height={height} /></clipPath>
            {curves.map((c, i) => <clipPath id={`curve-clip-${i}`} key={c.name}>
              <rect x={c.left} width={Math.max(0, c.right - c.left)} height={height} /></clipPath>)}
          </defs>
          <g clipPath="url(#plot-clip)">
            {(data.bands || []).map((band, i) => {
              const left = clamp(xAt(band.low, width, view), 0, width)
              const right = clamp(xAt(band.high, width, view), 0, width)
              return <g key={`band-${i}`} pointerEvents="none" className="band">
                <rect x={left} width={Math.max(0, right - left)} height={height} />
                <text x={left + 5} y={15}>{band.label}</text></g>
            })}
            {curves.map((c, i) => <g key={c.name} clipPath={`url(#curve-clip-${i})`}>
              <FrequencyResponseCurve color={c.color} lineWidth={c.width || 2} dotted={c.dashed} magnitudes={c.magnitudes} /></g>)}
            {data.rawNodes && data.raw.map((point, index) => <g key={`raw-${index}`} data-node="raw">
              <FilterPoint filter={{ type: 'PEAK', freq: point[0], gain: point[1] + interpolate(data.rawBase, point[0]), q: 1 }}
                index={index} dragX={false} wheelQ={false} radius={3.5} color={tokens['raw-node'] || '#8fa2b8'} label=""
                onDrag={active => { if (active) send({ op: 'begin' }) }}
                onChange={e => send({ op: 'raw', phase: e.ended ? 'end' : 'change', index,
                  gain: clamp(e.gain - interpolate(data.rawBase, point[0]), -60, 60) })} />
            </g>)}
            {data.filters.map((f, index) => f.enabled && <g key={`peq-${f.id}`} data-node="peq" data-filter={index}
              onClick={() => setSelected(index)}>
              <FilterPoint filter={{ type: 'PEAK', freq: f.frequency,
                gain: (noGain.has(f.kind) ? 0 : f.gain) + interpolate(data.nodeBase, f.frequency), q: f.q }}
                index={index} color={tokens['accent'] || '#3ccfb8'} label={String(f.id)} active={selected === index}
                dragY={!noGain.has(f.kind)} wheelQ={false}
                onDrag={active => { if (active) send({ op: 'begin' }) }}
                onChange={e => changeFilter(f, index, e)} />
            </g>)}
            {pointer && <g pointerEvents="none" className="crosshair"><line x1={xAt(pointer.frequency, width, view)}
              x2={xAt(pointer.frequency, width, view)} y1={0} y2={height} />
              <line x1={0} x2={width} y1={yAt(pointer.gain, height, view)} y2={yAt(pointer.gain, height, view)} /></g>}
          </g>
        </FrequencyResponseGraph>}
        {!data.curves.length && <div className="empty"><p>{data.empty}</p>
          {data.actions?.length > 0 && <div className="actions">{data.actions.map(a =>
            <button type="button" key={a.command} onClick={() => send({ op: 'command', name: a.command })}>{a.label}</button>)}</div>}
        </div>}
      </div>
      {selectedFilter && <Inspector filter={selectedFilter} index={selected} close={() => setSelected(null)} />}
      {help && <section className="help" aria-label="操作说明">
        {gestures.map(([gesture, effect]) => <div key={gesture}><kbd>{gesture}</kbd><span>{effect}</span></div>)}
      </section>}
    </div>
    <footer className="footer">
      <span className="readout">{pointer ? `${formatHz(pointer.frequency)} Hz · ${pointer.gain.toFixed(2)} dB` : '—'}</span>
      <span className="range">{formatHz(view.minFreq)}–{formatHz(view.maxFreq)} Hz · {view.minGain.toFixed(0)}…{view.maxGain.toFixed(0)} dB</span>
    </footer>
  </main>
}

createRoot(document.getElementById('root')).render(<Editor />)
