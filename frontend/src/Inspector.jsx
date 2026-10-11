import React, { useEffect, useState } from 'react'
import { clamp } from './math.mjs'
import { send } from './bridge.js'

export const kinds = [['PEAK', '峰值'], ['LS', '低搁架'], ['HS', '高搁架'], ['LP', '低通'], ['HP', '高通'],
  ['NOTCH', '陷波'], ['BAND_PASS', '带通'], ['ALL_PASS', '全通']]
export const noGain = new Set(['LP', 'HP', 'NOTCH', 'BAND_PASS', 'ALL_PASS'])

const fields = [['frequency', '频率 Hz', 20, 20000, 1], ['gain', '增益 dB', -60, 60, 0.1], ['q', 'Q', 0.01, 100, 0.1]]

export function Inspector({ filter, index, close }) {
  const [draft, setDraft] = useState(filter)
  useEffect(() => setDraft(filter), [filter])
  const update = (key, value) => {
    const next = { ...draft, [key]: value }
    setDraft(next)
    send({ op: 'filter', phase: 'end', index, filter: next })
  }
  return <section className="inspector" aria-label="PEQ 参数">
    <header><strong>PEQ {filter.id}</strong><button className="ghost" onClick={close} aria-label="关闭">×</button></header>
    <label>类型<select value={draft.kind} onChange={e => update('kind', e.target.value)}>
      {kinds.map(([value, title]) => <option key={value} value={value}>{value} · {title}</option>)}</select></label>
    {fields.map(([key, title, min, max, step]) => {
      const disabled = key === 'gain' && noGain.has(draft.kind)
      return <label key={key} className={disabled ? 'disabled' : ''}>{title}<input aria-label={title} type="number" min={min}
        max={max} step={step} value={draft[key]} disabled={disabled}
        onChange={e => setDraft({ ...draft, [key]: e.target.value })}
        onBlur={() => update(key, clamp(Number.isFinite(Number(draft[key])) ? Number(draft[key]) : min, min, max))}
        onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} /></label>
    })}
    <footer><label className="check"><input type="checkbox" checked={draft.enabled}
      onChange={e => update('enabled', e.target.checked)} />启用</label>
      <button className="danger" onClick={() => { send({ op: 'delete', index }); close() }}>删除</button></footer>
  </section>
}
