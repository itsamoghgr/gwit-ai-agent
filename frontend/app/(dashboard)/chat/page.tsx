"use client";

import { useEffect, useRef, useState } from "react";
import { useRun } from "@/lib/RunContext";
import { MessageSquare, Send, Loader2, ChevronDown, ChevronUp, Trash2 } from "lucide-react";
import type { ChatMessage, KBSource } from "@/lib/types";

const ARCHIE_INTRO = "Hi, I'm **Archie**, the GW IT Help Desk AI assistant.\n\nAsk me anything about GWU IT systems, account issues, software, or hardware — I'll answer based on the GW Knowledge Base.";

function md(text: string) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br/>");
}

function SourceExpander({ sources }: { sources: KBSource[] }) {
  const [open, setOpen] = useState(false);
  const relevant = sources.filter(s => s.similarity >= 0.87);
  if (!relevant.length) return null;
  return (
    <div className="mt-2">
      <button
        className="text-[11px] text-primary hover:underline flex items-center gap-1"
        onClick={() => setOpen(o => !o)}
      >
        {open ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        {relevant.length} source{relevant.length > 1 ? "s" : ""} used
      </button>
      {open && (
        <div className="mt-2 space-y-1.5">
          {relevant.map((src, i) => (
            <div key={i} className="bg-base-200 rounded-lg p-2.5 border border-base-300 text-[11px]">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="font-semibold text-base-content">{src.title}</span>
                <span className={`badge badge-xs ${src.is_generated ? "badge-success" : "badge-info"}`}>
                  {src.is_generated ? "Generated" : "Existing"}
                </span>
                <span className="text-base-content/30">{(src.similarity * 100).toFixed(0)}% match</span>
              </div>
              <p className="text-base-content/50 line-clamp-2 leading-relaxed">{src.snippet}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold shadow-sm mt-0.5">
          A
        </div>
      )}
      <div className="max-w-xl">
        <div
          className={`rounded-xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
            isUser
              ? "bg-primary text-primary-content rounded-tr-sm"
              : "bg-base-200 text-base-content border border-base-300 rounded-tl-sm"
          }`}
          dangerouslySetInnerHTML={{ __html: md(msg.content) }}
        />
        {!isUser && msg.sources && <SourceExpander sources={msg.sources} />}
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-base-300 flex items-center justify-center flex-shrink-0 text-base-content text-[11px] font-bold mt-0.5">
          U
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  const { runId }    = useRun();
  const [messages, setMessages]   = useState<ChatMessage[]>([]);
  const [input, setInput]         = useState("");
  const [streaming, setStreaming] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/chat/suggested-questions")
      .then(r => r.json())
      .then(d => setSuggestions(d.questions ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function sendMessage(text: string) {
    if (!text.trim() || streaming || !runId) return;
    setMessages(prev => [...prev, { role: "user", content: text, sources: [] }]);
    setInput("");
    setStreaming(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, message: text, top_k: 4 }),
      });
      if (!res.body) throw new Error("No response body");

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", reply = "", sources: KBSource[] = [];
      let assistantPushed = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") break;
          try {
            const event = JSON.parse(payload);
            if (event.type === "sources") { sources = event.sources; }
            else if (event.type === "token") {
              reply += event.text;
              if (!assistantPushed) {
                assistantPushed = true;
                setMessages(prev => [...prev, { role: "assistant", content: reply, sources }]);
              } else {
                setMessages(prev => {
                  const updated = [...prev];
                  updated[updated.length - 1] = { role: "assistant", content: reply, sources };
                  return updated;
                });
              }
            }
          } catch { /* skip */ }
        }
      }
      if (!assistantPushed) {
        setMessages(prev => [...prev, { role: "assistant", content: reply || "…", sources }]);
      } else {
        setMessages(prev => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: reply || "…", sources };
          return updated;
        });
      }
    } catch {
      setMessages(prev => [...prev, { role: "assistant", content: "Sorry, something went wrong. Please try again.", sources: [] }]);
    } finally {
      setStreaming(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(input); }
  }

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-base-content/30">
        <MessageSquare size={36} strokeWidth={1.5} />
        <p className="text-sm">Select a pipeline run from the sidebar to chat with Archie.</p>
      </div>
    );
  }

  const hasMessages = messages.length > 0;

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)]">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-base-content tracking-tight">AI Chat</h1>
          <p className="text-sm text-base-content/45 mt-0.5">Chat with Archie — powered by the GW Knowledge Base</p>
        </div>
        {hasMessages && (
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-base-content/40 hover:text-base-content border border-base-300 rounded-lg bg-base-100 hover:bg-base-200 transition-colors"
            onClick={() => setMessages([])}
          >
            <Trash2 size={12} /> Clear
          </button>
        )}
      </div>

      {/* Message area */}
      <div className="flex-1 overflow-y-auto bg-base-100 border border-base-300 rounded-xl p-5 space-y-4">
        {!hasMessages && (
          <>
            <div className="flex gap-2.5">
              <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold shadow-sm mt-0.5">A</div>
              <div className="bg-base-200 border border-base-300 rounded-xl rounded-tl-sm px-3.5 py-2.5 max-w-xl text-[13px] leading-relaxed text-base-content"
                dangerouslySetInnerHTML={{ __html: md(ARCHIE_INTRO) }}
              />
            </div>
            {suggestions.length > 0 && (
              <div className="ml-9">
                <p className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-2">Try asking</p>
                <div className="flex flex-wrap gap-2">
                  {suggestions.map(s => (
                    <button key={s}
                      className="px-3 py-1.5 rounded-lg text-[12px] text-base-content/55 border border-base-300 bg-base-100 hover:border-primary hover:text-primary transition-colors"
                      onClick={() => sendMessage(s)}
                    >{s}</button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {messages.map((msg, i) => <Bubble key={i} msg={msg} />)}

        {streaming && messages[messages.length - 1]?.role === "user" && (
          <div className="flex gap-2.5">
            <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold mt-0.5">A</div>
            <div className="bg-base-200 border border-base-300 rounded-xl rounded-tl-sm px-3.5 py-2.5 flex items-center gap-2">
              <Loader2 size={13} className="animate-spin text-base-content/40" />
              <span className="text-[12px] text-base-content/40">Thinking…</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="mt-3 flex gap-2 items-end">
        <textarea
          className="textarea textarea-bordered flex-1 resize-none text-[13px] bg-base-100 leading-relaxed"
          placeholder="Ask me anything about GWU IT…"
          rows={2}
          value={input}
          disabled={!runId || streaming}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          className="btn btn-primary self-end px-5 h-[68px]"
          disabled={!input.trim() || streaming || !runId}
          onClick={() => sendMessage(input)}
        >
          {streaming
            ? <Loader2 size={16} className="animate-spin" />
            : <><Send size={14} className="mr-1" />Send</>
          }
        </button>
      </div>
      <p className="text-[10px] text-base-content/20 mt-1.5 ml-0.5">Enter to send · Shift+Enter for new line</p>
    </div>
  );
}
