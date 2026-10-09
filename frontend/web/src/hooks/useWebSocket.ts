import { useState, useEffect, useRef, useCallback } from "react";
import {
  ChatMessage,
  LifecyclePhase,
  ApprovalRequestEvent,
  ModalQuestionEvent,
  HarnessIncomingEvent,
} from "../types/events";

export function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingResponse, setStreamingResponse] = useState("");
  const [currentPhase, setCurrentPhase] = useState<LifecyclePhase>("alignment");
  const [totalTokens, setTotalTokens] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [conversationName, setConversationName] = useState("");

  const [pendingApproval, setPendingApproval] = useState<ApprovalRequestEvent | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<ModalQuestionEvent | null>(null);
  const [sidecarMessages, setSidecarMessages] = useState<Array<{ query: string; response: string; timestamp: string }>>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const activeToolCallsRef = useRef<Map<string, any>>(new Map());

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/events`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onclose = () => {
      setConnected(false);
      // Attempt reconnect after 2 seconds
      setTimeout(connect, 2000);
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      ws.close();
    };

    ws.onmessage = (event) => {
      try {
        const data: HarnessIncomingEvent = JSON.parse(event.data);

        switch (data.type) {
          case "connection_established":
            setProjectId(data.project_id);
            setConversationId(data.conversation_id);
            if (data.conversation_name) setConversationName(data.conversation_name);
            setCurrentPhase(data.current_phase);
            setTotalTokens(data.total_tokens);
            setMessages(data.chat_messages || []);
            try {
              localStorage.setItem("libhippo_active_conv", data.conversation_id);
            } catch (e) {}
            break;

          case "phase_transition":
            setCurrentPhase(data.to_phase);
            break;

          case "token_chunk":
            setStreamingResponse((prev) => prev + data.delta);
            break;

          case "tool_call_start": {
            const toolCall = {
              id: data.tool_call_id,
              name: data.name,
              arguments: data.arguments,
            };
            activeToolCallsRef.current.set(data.tool_call_id, toolCall);
            // Append or update active tool in messages
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === "assistant") {
                const tools = last.toolCalls ? [...last.toolCalls, toolCall] : [toolCall];
                return [...prev.slice(0, -1), { ...last, toolCalls: tools }];
              } else {
                return [
                  ...prev,
                  {
                    id: `asst-${Date.now()}`,
                    role: "assistant",
                    content: "",
                    toolCalls: [toolCall],
                    timestamp: new Date().toLocaleTimeString(),
                  },
                ];
              }
            });
            break;
          }

          case "tool_call_result": {
            const existing = activeToolCallsRef.current.get(data.tool_call_id);
            if (existing) {
              existing.result = data.result;
              existing.error = data.error;
            }
            setMessages((prev) => {
              return prev.map((msg) => {
                if (msg.role === "assistant" && msg.toolCalls) {
                  const hasCall = msg.toolCalls.some((t) => t.id === data.tool_call_id);
                  if (hasCall) {
                    const updated = msg.toolCalls.map((t) =>
                      t.id === data.tool_call_id ? { ...t, result: data.result, error: data.error ?? undefined } : t
                    );
                    return { ...msg, toolCalls: updated };
                  }
                }
                return msg;
              });
            });
            break;
          }

          case "approval_request":
            setPendingApproval(data);
            break;

          case "modal_question":
            setPendingQuestion(data);
            break;

          case "task_notification":
            setMessages((prev) => [
              ...prev,
              {
                id: `task-${Date.now()}`,
                role: "system",
                content: `Background Task [${data.task_id}] ${data.status} (exit code: ${data.exit_code ?? 0}). Log: ${data.log_path}`,
              },
            ]);
            break;

          case "knowledge_agent": {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === "assistant") {
                const current = last.knowledgeEvents || [];
                return [
                  ...prev.slice(0, -1),
                  { ...last, knowledgeEvents: [...current, data] },
                ];
              } else {
                return [
                  ...prev,
                  {
                    id: `asst-${Date.now()}`,
                    role: "assistant",
                    content: "",
                    knowledgeEvents: [data],
                    timestamp: new Date().toLocaleTimeString(),
                  },
                ];
              }
            });
            break;
          }

          case "turn_completed":
            setIsStreaming(false);
            setTotalTokens(data.total_tokens);
            if (data.response) {
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "assistant" && (!last.content || last.content === "")) {
                  return [...prev.slice(0, -1), { ...last, content: data.response }];
                }
                return [
                  ...prev,
                  {
                    id: `asst-${Date.now()}`,
                    role: "assistant",
                    content: data.response,
                    timestamp: new Date().toLocaleTimeString(),
                  },
                ];
              });
            }
            setStreamingResponse("");
            break;

          case "interrupt":
            setIsStreaming(false);
            setMessages((prev) => [
              ...prev,
              {
                id: `int-${Date.now()}`,
                role: "interrupt",
                content: `Agent execution interrupted (${data.reason}).`,
                timestamp: new Date().toLocaleTimeString(),
              },
            ]);
            setStreamingResponse("");
            break;

          case "sidecar_response":
            setSidecarMessages((prev) => [
              ...prev,
              {
                query: data.query,
                response: data.response,
                timestamp: new Date().toLocaleTimeString(),
              },
            ]);
            break;

          case "steer_result":
            setMessages((prev) => [
              ...prev,
              {
                id: `steer-${Date.now()}`,
                role: "system",
                content: `Mid-turn steering sent: "${data.guidance}"`,
                timestamp: new Date().toLocaleTimeString(),
              },
            ]);
            break;

          default:
            break;
        }
      } catch (err) {
        console.error("Failed to parse websocket message:", err);
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  const sendPrompt = useCallback((content: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content,
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
    setIsStreaming(true);
    setStreamingResponse("");
    wsRef.current.send(JSON.stringify({ type: "prompt", content }));
  }, []);

  const sendContinue = useCallback((guidance?: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const trimmed = guidance ? guidance.trim() : "";
    const content = trimmed || "Continue working on the previous task.";
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content: trimmed ? `/continue ${trimmed}` : "/continue",
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
    setIsStreaming(true);
    setStreamingResponse("");
    wsRef.current.send(JSON.stringify({ type: "continue", content }));
  }, []);

  const sendSteer = useCallback((guidance: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "steer", guidance }));
  }, []);

  const sendInterrupt = useCallback((reason = "paused_by_user", steerText?: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "interrupt", reason, steer_text: steerText }));
  }, []);

  const sendApproval = useCallback((requestId: string, approved: boolean) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "approval_response", request_id: requestId, approved }));
    setPendingApproval(null);
  }, []);

  const sendModalAnswer = useCallback((questionId: string, answers: any[]) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "modal_answer", question_id: questionId, answers }));
    setPendingQuestion(null);
  }, []);

  const sendSidecar = useCallback((query: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "sidecar", query }));
  }, []);

  const resetSession = useCallback(async (customName?: string) => {
    const res = await fetch("/api/session/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: customName }),
    });
    const data = await res.json();
    if (data && data.conversation_id) {
      setConversationId(data.conversation_id);
      setConversationName(data.name || "");
      try {
        localStorage.setItem("libhippo_active_conv", data.conversation_id);
      } catch (e) {}
    } else {
      setConversationName("");
    }
    setMessages([]);
    setStreamingResponse("");
    setIsStreaming(false);
    setCurrentPhase("alignment");
  }, []);

  const loadConversation = useCallback(async (convId: string) => {
    try {
      const res = await fetch(`/api/conversations/${encodeURIComponent(convId)}/load`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setConversationId(data.conversation_id);
        if (data.name) setConversationName(data.name);
        if (data.chat_messages && Array.isArray(data.chat_messages)) {
          setMessages(data.chat_messages);
        }
        if (typeof data.total_tokens === "number") {
          setTotalTokens(data.total_tokens);
        }
        setStreamingResponse("");
        setIsStreaming(false);
        try {
          localStorage.setItem("libhippo_active_conv", data.conversation_id);
        } catch (e) {}
      }
    } catch (err) {
      console.error("Failed to load conversation:", err);
    }
  }, []);

  const compactContext = useCallback(async () => {
    const res = await fetch("/api/session/compact", { method: "POST" });
    const data = await res.json();
    setTotalTokens(data.total_tokens);
    return data.evicted_tokens;
  }, []);

  return {
    connected,
    messages,
    isStreaming,
    streamingResponse,
    currentPhase,
    totalTokens,
    projectId,
    conversationId,
    conversationName,
    pendingApproval,
    pendingQuestion,
    sidecarMessages,
    sendPrompt,
    sendContinue,
    sendSteer,
    sendInterrupt,
    sendApproval,
    sendModalAnswer,
    sendSidecar,
    resetSession,
    loadConversation,
    compactContext,
  };
}
