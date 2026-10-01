export interface ToolCall { name: string; args: unknown; result: unknown }
export interface Turn { message: string; response: string; tool_calls: ToolCall[]; failed?: boolean }
export interface Session { session_id: string; title: string; updated_at: string }
export interface Conversation { session_id: string; title: string; turns: Turn[] }
export interface Metric {
  label: string; value: number | null; unit?: string; estimated?: boolean
  unavailable_reason?: string; formula?: string; inputs?: unknown
}
export interface Panel {
  player?: string; team?: string; headshot?: string; position?: string; jersey?: string
  provisional?: boolean; metrics?: Record<string, Metric>; note?: string
}
