export interface SecurityRuleList {
  allow: string[];
  deny: string[];
  ask: string[];
}

export interface ProjectSecurityPolicy {
  project_id: string;
  workspace_root: string;
  read_file: SecurityRuleList;
  write_file: SecurityRuleList;
  command: SecurityRuleList;
  allow_network: boolean;
  allowed_domains: string[];
}

export interface McpServerConfig {
  command: string;
  args: string[];
  env: Record<string, string>;
}

export interface GlobalMcpConfig {
  mcpServers: Record<string, McpServerConfig>;
}

export interface SkillDefinition {
  name: string;
  description: string;
  skill_path: string;
  system_prompt: string;
  tool_dependencies: string[];
}

export interface RuntimeConfig {
  model: string;
  mode: "turbo" | "default" | "request_review";
  transport_mode: "websocket" | "http";
  soft_token_watermark: number;
  hard_token_limit: number;
  compaction_target_tokens: number;
  max_turns: number;
  allow_sandbox_bypass: boolean;
}
