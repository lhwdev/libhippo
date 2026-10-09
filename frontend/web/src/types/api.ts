export interface CommandItem {
  command: string;
  name: string;
  description: string;
  syntax: string;
  type: "builtin" | "skill";
  tools?: string[];
}

export interface FileItem {
  path: string;
  name: string;
}

export interface ToolItem {
  name: string;
  description: string;
  parameters_schema: Record<string, any>;
  requires_approval: boolean;
  requires_sandbox_bypass: boolean;
}

export interface TaskItem {
  task_id: string;
  command: string;
  cwd: string;
  pid: number | null;
  status: string;
  exit_code: number | null;
  log_path: string;
  start_time?: string;
}

export interface SubagentItem {
  subagent_id: string;
  role: string;
  prompt: string;
  state: string;
  model: string;
  start_time?: string;
}

export interface KnowledgeMount {
  namespace: string;
  physical_path: string;
  read_only: boolean;
}

export interface ArtifactItem {
  name: string;
  path: string;
  metadata: {
    Summary?: string;
    UserFacing?: boolean;
    RequestFeedback?: boolean;
    [key: string]: any;
  };
}

export interface ConversationItem {
  id: string;
  name?: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
  metadata?: Record<string, any>;
  is_active?: boolean;
}

