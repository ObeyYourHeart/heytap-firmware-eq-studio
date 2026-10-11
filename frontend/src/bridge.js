// Qt WebChannel boundary. Python owns every edit; this side only reports gestures.
export const SCHEMA = 2

let bridge = null

export const send = event => bridge?.submit(JSON.stringify(event))

export function connect(onUpdate) {
  window.studioEdit = send
  window.studioUpdate = value => onUpdate(typeof value === 'string' ? JSON.parse(value) : value)
  if (window.qt && window.QWebChannel) {
    new window.QWebChannel(window.qt.webChannelTransport, channel => {
      bridge = channel.objects.bridge
      bridge.publish.connect(window.studioUpdate)
      bridge.ready()
    })
  }
}

// Theme tokens arrive from Python so Qt widgets and the graph always share one palette.
export function applyTheme(theme) {
  if (!theme) return
  const root = document.documentElement
  for (const [key, value] of Object.entries(theme.tokens || {})) root.style.setProperty(`--${key}`, value)
  root.dataset.theme = theme.mode || 'dark'
  root.dataset.contrast = theme.contrast || 'standard'
}
