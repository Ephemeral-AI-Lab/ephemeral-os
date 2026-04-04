import type { BackendEvent, FrontendRequest } from './types'

type EventHandler = (event: BackendEvent) => void

class HarnessSocket {
  private ws: WebSocket | null = null
  private listeners = new Map<string, Set<EventHandler>>()
  private globalListeners = new Set<EventHandler>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private _url: string = ''
  private _connected = false

  get connected() {
    return this._connected
  }

  connect(url: string = `ws://${window.location.host}/ws`) {
    this._url = url
    this._tryConnect()
  }

  private _tryConnect() {
    if (this.ws?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(this._url)

    ws.onopen = () => {
      this._connected = true
      this._notifyGlobal({ type: '__connected' } as unknown as BackendEvent)
    }

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as BackendEvent
        this._dispatch(event)
      } catch { /* ignore malformed messages */ }
    }

    ws.onclose = () => {
      this._connected = false
      this.ws = null
      this._notifyGlobal({ type: '__disconnected' } as unknown as BackendEvent)
      this.reconnectTimer = setTimeout(() => this._tryConnect(), 2000)
    }

    ws.onerror = () => {
      ws.close()
    }

    this.ws = ws
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
    this._connected = false
  }

  send(request: FrontendRequest) {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    this.ws.send(JSON.stringify(request))
  }

  /** Subscribe to a specific event type */
  on(eventType: string, handler: EventHandler): () => void {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set())
    }
    this.listeners.get(eventType)!.add(handler)
    return () => this.listeners.get(eventType)?.delete(handler)
  }

  /** Subscribe to ALL events */
  onAny(handler: EventHandler): () => void {
    this.globalListeners.add(handler)
    return () => this.globalListeners.delete(handler)
  }

  private _dispatch(event: BackendEvent) {
    const handlers = this.listeners.get(event.type)
    if (handlers) {
      for (const h of handlers) h(event)
    }
    this._notifyGlobal(event)
  }

  private _notifyGlobal(event: BackendEvent) {
    for (const h of this.globalListeners) h(event)
  }
}

export const socket = new HarnessSocket()
