import React from 'react'

// Each chip toggles one curve family; Python owns the state and republishes it.
export function Legend({ items, dacMode, onToggle, onDacMode }) {
  let group = null
  const children = []
  for (const item of items) {
    if (group !== null && item.group !== group) children.push(<span className="sep" key={`sep-${item.key}`} />)
    group = item.group
    children.push(<button key={item.key} type="button" className={`chip${item.checked ? ' on' : ''}`}
      aria-pressed={item.checked} title={item.hint || item.label} data-key={item.key} disabled={item.locked}
      onClick={() => onToggle(item.key, !item.checked)}>
      <i className={`swatch${item.dashed ? ' dashed' : ''}${item.dot ? ' dot' : ''}`} style={{ '--c': item.color }} />
      {item.label}</button>)
  }
  if (dacMode) {
    children.push(<select key="dac-mode" className="mini" aria-label="DAC 曲线模式" value={dacMode.value}
      title="仅改变显示方式，不影响最终写入范围" onChange={e => onDacMode(e.target.value)}>
      {dacMode.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>)
  }
  return <div className="legend" role="group" aria-label="曲线显示">{children}</div>
}
