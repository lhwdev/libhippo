export type LifecyclePhase = "alignment" | "planning" | "implementation" | "review" | "maintenance";

export interface TokenChunkEvent {
  type: "token_chunk";
  delta: string;
}

export interface PhaseTransitionEvent {
  type: "phase_transition";
  from_phase: string;
  to_phase: LifecyclePhase;
}

export interface ToolCallStartEvent {
  type: "tool_call_start";
  tool_call_id: string;
  name: string;
  arguments: Record<string, any>;
}

export interface ToolCallResultEvent {
  type: "tool_call_result";
  tool_call_id: string;
  name: string;
  result: any;
  error?: string | null;
}

export interface ApprovalRequestEvent {
  type: "approval_request";
  request_id: string;
  action: string;
  details: Record<string, any>;
}

export interface ModalQuestion {
  question: string;
  options: string[];
  is_multi_select?: boolean;
}

export interface ModalQuestionEvent {
  type: "modal_question";
  question_id: string;
  questions: ModalQuestion[];
}

export interface TaskNotificationEvent {
  type: "task_notification";
  task_id: string;
  status: string;
  log_path: string;
  exit_code?: number | null;
}

export interface TurnCompletedEvent {
  type: "turn_completed";
  turn_index: number;
  total_tokens: number;
  duration_seconds: number;
  response: string;
}

export interface InterruptEvent {
  type: "interrupt";
  reason: string;
  interrupted_at: string;
}

export interface ConnectionEstablishedEvent {
  type: "connection_established";
  project_id: string;
  conversation_id: string;
  conversation_name?: string;
  conversation_metadata?: Record<string, any>;
  current_phase: LifecyclePhase;
  total_tokens: number;
  reasoning_effort?: string;
  has_active_knowledge_workers?: boolean;
  chat_messages?: ChatMessage[];
}

export interface SidecarResponseEvent {
  type: "sidecar_response";
  query: string;
  response: string;
}

export interface SteerResultEvent {
  type: "steer_result";
  guidance: string;
  result: any;
}

export interface KnowledgeAgentEvent {
  type: "knowledge_agent";
  agent: string;
  action: string;
  status: string;
  target_path: string;
  details?: Record<string, any>;
  input_summary?: string;
  output_summary?: string;
  timestamp?: string;
}

export interface KnowledgeWorkerStatusEvent {
  type: "knowledge_worker_status";
  active: boolean;
  count: number;
}

export interface UndoResultEvent {
  type: "undo_result";
  status: string;
  prompt?: string;
  message_index?: number;
  chat_messages?: ChatMessage[];
  total_tokens?: number;
}

export interface SessionUpdatedEvent {
  type: "session_updated";
  chat_messages: ChatMessage[];
  total_tokens: number;
}

export interface ReasoningEffortUpdatedEvent {
  type: "reasoning_effort_updated";
  effort: string;
}

export type HarnessIncomingEvent =
  | TokenChunkEvent
  | PhaseTransitionEvent
  | ToolCallStartEvent
  | ToolCallResultEvent
  | ApprovalRequestEvent
  | ModalQuestionEvent
  | TaskNotificationEvent
  | TurnCompletedEvent
  | InterruptEvent
  | ConnectionEstablishedEvent
  | SidecarResponseEvent
  | SteerResultEvent
  | KnowledgeAgentEvent
  | KnowledgeWorkerStatusEvent
  | UndoResultEvent
  | SessionUpdatedEvent
  | ReasoningEffortUpdatedEvent;

export interface ChatMessage {
  id: string;
  role: "system" | "user" | "assistant" | "tool" | "interrupt";
  content: string;
  timestamp?: string;
  message_index?: number;
  raw_prompt?: string;
  toolCalls?: Array<{
    id: string;
    name: string;
    arguments: Record<string, any>;
    result?: any;
    error?: string;
  }>;
  knowledgeEvents?: KnowledgeAgentEvent[];
}
