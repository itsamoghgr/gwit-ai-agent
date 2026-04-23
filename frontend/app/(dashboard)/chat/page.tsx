"use client";

import { useEffect, useRef, useState } from "react";
import { useRun } from "@/lib/RunContext";
import { MessageSquare, Send, Loader2, ChevronDown, ChevronUp, Trash2 } from "lucide-react";
import type { ChatMessage, KBSource } from "@/lib/types";

const ARCHIE_INTRO = "Hi, I'm **Archie**, the GW IT Help Desk AI assistant.\n\nAsk me anything about GWU IT systems, account issues, software, or hardware — I'll answer based on the GW Knowledge Base.";

// Minimal markdown → HTML for Archie replies. Handles bold, markdown links
// `[text](url)`, and auto-linking of bare phones / emails / https URLs /
// *.gwu.edu domains. We escape HTML first so GPT output can't inject tags.
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function md(text: string) {
  // Placeholder strategy: swap already-resolved links for tokens so subsequent
  // auto-link passes don't double-wrap text that's already inside an <a>.
  const slots: string[] = [];
  const stash = (html: string) => {
    const i = slots.length;
    slots.push(html);
    return `\u0000L${i}\u0000`;
  };

  // Normalize GPT's em/en dashes to a comma-space. Feels more like natural
  // typing and avoids the "AI-written" look. Handles optional surrounding
  // whitespace so " — " and "—" collapse cleanly.
  let out = escapeHtml(text).replace(/\s*[—–]\s*/g, ", ");

  // Markdown links: [label](href)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, href) =>
    stash(`<a href="${href}" target="_blank" rel="noopener" class="link link-primary">${label}</a>`)
  );

  // Bold
  out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Auto-link bare URLs / emails / phones / gwu.edu domains
  out = out.replace(
    /([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})|\b(https?:\/\/[^\s<>"]+)|\b(\d{3}-\d{3}-\d{4})\b|\b([a-zA-Z0-9-]+\.gwu\.edu[^\s<>",.)]*)/g,
    (_m, email, url, phone, domain) => {
      if (email)  return stash(`<a href="mailto:${email}" class="link link-primary">${email}</a>`);
      if (url)    return stash(`<a href="${url}" target="_blank" rel="noopener" class="link link-primary">${url}</a>`);
      if (phone)  return stash(`<a href="tel:${phone.replace(/-/g, "")}" class="link link-primary">${phone}</a>`);
      if (domain) return stash(`<a href="https://${domain}" target="_blank" rel="noopener" class="link link-primary">${domain}</a>`);
      return _m;
    }
  );

  // Line-aware pass: group consecutive list lines into <ul>/<ol>. Everything
  // else becomes <p> (or <br/> inside a paragraph on soft breaks). Tailwind
  // classes give the bullets/numbers a hanging indent so wrapped text aligns
  // under the first word, not under the bullet.
  const lines = out.split("\n");
  const blocks: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let listItems: string[] = [];
  let paraLines: string[] = [];

  const flushList = () => {
    if (!listType) return;
    const tag = listType;
    const cls = tag === "ul"
      ? "list-disc list-outside pl-5 space-y-1.5 mb-3"
      : "list-decimal list-outside pl-5 space-y-1.5 mb-3";
    blocks.push(`<${tag} class="${cls}">${listItems.map(i => `<li>${i}</li>`).join("")}</${tag}>`);
    listType = null;
    listItems = [];
  };
  const flushPara = () => {
    if (!paraLines.length) return;
    blocks.push(`<p class="mb-3">${paraLines.join("<br/>")}</p>`);
    paraLines = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet  = line.match(/^\s*[-*•]\s+(.*)$/);
    const ordered = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (bullet) {
      flushPara();
      if (listType !== "ul") { flushList(); listType = "ul"; }
      listItems.push(bullet[1]);
    } else if (ordered) {
      flushPara();
      if (listType !== "ol") { flushList(); listType = "ol"; }
      listItems.push(ordered[2]);
    } else if (line.trim() === "") {
      flushList();
      flushPara();
    } else {
      flushList();
      paraLines.push(line);
    }
  }
  flushList();
  flushPara();

  // Drop the trailing bottom-margin on the last block so the bubble hugs the
  // final line instead of leaving a phantom gap below it.
  if (blocks.length) {
    blocks[blocks.length - 1] = blocks[blocks.length - 1].replace(/\smb-3"/, ' mb-0"');
  }

  out = blocks.join("");

  // Re-insert resolved link HTML
  return out.replace(/\u0000L(\d+)\u0000/g, (_m, i) => slots[Number(i)]);
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

function Bubble({ msg, streaming = false }: { msg: ChatMessage; streaming?: boolean }) {
  const isUser = msg.role === "user";
  const html = md(msg.content) + (streaming ? '<span class="archie-caret">▍</span>' : "");
  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold shadow-sm mt-0.5">
          A
        </div>
      )}
      <div className={isUser ? "max-w-xl" : "max-w-2xl flex-1"}>
        <div
          className={
            isUser
              ? "rounded-2xl rounded-tr-sm bg-primary text-primary-content px-4 py-2.5 text-[13px] leading-relaxed"
              : "text-[13.5px] leading-[1.7] text-base-content"
          }
          dangerouslySetInnerHTML={{ __html: html }}
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

// Typewriter reveal rate: base chars per frame @ 60fps.
// 3/frame ≈ 180 cps ≈ 30 words/sec — reads like fast typing, not a burst.
// Adaptive: if the queue grows past CATCHUP_THRESHOLD (network outpacing us),
// we reveal proportionally faster so we never fall minutes behind the stream.
const REVEAL_RATE      = 3;
const CATCHUP_THRESHOLD = 120;
const CATCHUP_DIVISOR   = 20;

export default function ChatPage() {
  const { runId }    = useRun();
  const [messages, setMessages]   = useState<ChatMessage[]>([]);
  const [input, setInput]         = useState("");
  const [streaming, setStreaming] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Typewriter queue — tokens arrive from SSE in bursts, we drain ~REVEAL_RATE
  // chars per frame into the last assistant bubble so the reveal feels paced.
  const queueRef   = useRef<string>("");
  const rafRef     = useRef<number | null>(null);
  const streamDoneRef = useRef<boolean>(false);
  const latestSourcesRef = useRef<KBSource[]>([]);
  const assistantPushedRef = useRef<boolean>(false);

  useEffect(() => {
    fetch("/api/chat/suggested-questions")
      .then(r => r.json())
      .then(d => setSuggestions(d.questions ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // When Archie finishes replying, drop focus back into the composer so the
  // native cursor blinks and the user can just start typing their next turn.
  useEffect(() => {
    if (!streaming && messages.length > 0 && messages[messages.length - 1]?.role === "assistant") {
      textareaRef.current?.focus();
    }
  }, [streaming, messages]);

  useEffect(() => {
    return () => { if (rafRef.current !== null) cancelAnimationFrame(rafRef.current); };
  }, []);

  function startDrain() {
    if (rafRef.current !== null) return;
    const tick = () => {
      const queued = queueRef.current;
      if (queued.length === 0) {
        if (streamDoneRef.current) {
          rafRef.current = null;
          setStreaming(false);
          return;
        }
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      // Base rate, plus a catch-up bonus once the buffer gets large. Also,
      // if the stream is already done, empty the tail quickly so the caret
      // doesn't linger for seconds on the last few words.
      let rate = REVEAL_RATE;
      if (queued.length > CATCHUP_THRESHOLD) {
        rate += Math.floor((queued.length - CATCHUP_THRESHOLD) / CATCHUP_DIVISOR);
      }
      if (streamDoneRef.current) rate = Math.max(rate, 6);
      const take = Math.min(rate, queued.length);
      const chunk = queued.slice(0, take);
      queueRef.current = queued.slice(take);

      if (!assistantPushedRef.current) {
        assistantPushedRef.current = true;
        setMessages(prev => [...prev, { role: "assistant", content: chunk, sources: latestSourcesRef.current }]);
      } else {
        setMessages(prev => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          updated[updated.length - 1] = {
            role: "assistant",
            content: last.content + chunk,
            sources: latestSourcesRef.current,
          };
          return updated;
        });
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  async function sendMessage(text: string) {
    if (!text.trim() || streaming || !runId) return;
    // Snapshot prior turns for the backend before the optimistic append.
    // Strip `sources` — the backend only uses role+content.
    const historyPayload = messages.map(m => ({ role: m.role, content: m.content }));
    setMessages(prev => [...prev, { role: "user", content: text, sources: [] }]);
    setInput("");
    setStreaming(true);

    // Reset typewriter state for the new turn.
    queueRef.current = "";
    streamDoneRef.current = false;
    latestSourcesRef.current = [];
    assistantPushedRef.current = false;
    startDrain();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: runId,
          message: text,
          top_k: 4,
          history: historyPayload,
        }),
      });
      if (!res.body) throw new Error("No response body");

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

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
            if (event.type === "sources") {
              latestSourcesRef.current = event.sources;
            } else if (event.type === "token") {
              queueRef.current += event.text;
            }
          } catch { /* skip */ }
        }
      }
    } catch {
      queueRef.current = "";
      assistantPushedRef.current = true;
      setMessages(prev => [...prev, { role: "assistant", content: "Sorry, something went wrong. Please try again.", sources: [] }]);
    } finally {
      // Signal the drain loop that no more tokens are coming; it will flip
      // `streaming` off once the queue empties.
      streamDoneRef.current = true;
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

      {/* Message area — borderless, full-width chat feed */}
      <div className="flex-1 overflow-y-auto space-y-6 pr-1">
        {!hasMessages && (
          <>
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold shadow-sm mt-0.5">A</div>
              <div className="flex-1 text-[13.5px] leading-[1.7] text-base-content"
                dangerouslySetInnerHTML={{ __html: md(ARCHIE_INTRO) }}
              />
            </div>
            {suggestions.length > 0 && (
              <div className="ml-10">
                <p className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-2">Try asking</p>
                <div className="flex flex-wrap gap-2">
                  {suggestions.map(s => (
                    <button key={s}
                      className="px-3 py-1.5 rounded-full text-[12px] text-base-content/60 border border-base-300 bg-base-100 hover:border-primary hover:text-primary transition-colors"
                      onClick={() => sendMessage(s)}
                    >{s}</button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {messages.map((msg, i) => {
          const isLast = i === messages.length - 1;
          const showCaret = streaming && isLast && msg.role === "assistant";
          return <Bubble key={i} msg={msg} streaming={showCaret} />;
        })}

        {streaming && messages[messages.length - 1]?.role === "user" && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center flex-shrink-0 text-primary-content text-[11px] font-bold mt-0.5">A</div>
            <div className="flex items-center py-2 text-primary/70">
              <span className="archie-dots" aria-label="Thinking">
                <span /><span /><span />
              </span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input — unified pill with inline send button */}
      <div className="mt-4">
        <div className="relative flex items-end bg-base-200 border border-base-300/60 rounded-2xl focus-within:border-primary/50 focus-within:shadow-[0_0_0_2px_oklch(from_var(--color-primary)_l_c_h/0.10)] transition-all">
          <textarea
            ref={textareaRef}
            className="flex-1 bg-transparent resize-none text-[13.5px] leading-relaxed px-4 py-3 pr-16 placeholder:text-base-content/30 focus:outline-none"
            placeholder="Ask me anything about GWU IT…"
            rows={1}
            style={{ minHeight: "48px", maxHeight: "200px" }}
            value={input}
            disabled={!runId || streaming}
            autoFocus
            onChange={e => {
              setInput(e.target.value);
              // Auto-grow
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
            }}
            onKeyDown={handleKeyDown}
          />
          <button
            className="absolute right-2.5 bottom-2 w-9 h-9 rounded-xl bg-primary text-primary-content flex items-center justify-center shadow-sm disabled:opacity-30 disabled:cursor-not-allowed hover:bg-primary/90 active:scale-95 transition-all"
            disabled={!input.trim() || streaming || !runId}
            onClick={() => sendMessage(input)}
            aria-label="Send message"
          >
            {streaming
              ? <Loader2 size={16} className="animate-spin" />
              : <Send size={15} />
            }
          </button>
        </div>
        <p className="flex items-center flex-wrap gap-x-1.5 gap-y-1 text-[10.5px] text-base-content/35 mt-2 ml-1">
          <kbd className="inline-flex items-center justify-center min-w-[22px] h-[18px] px-1.5 rounded-md bg-base-200 border border-base-300 text-[9.5px] font-medium font-mono leading-none text-base-content/60">Enter</kbd>
          <span>to send</span>
          <span className="text-base-content/20">·</span>
          <kbd className="inline-flex items-center justify-center min-w-[22px] h-[18px] px-1.5 rounded-md bg-base-200 border border-base-300 text-[9.5px] font-medium font-mono leading-none text-base-content/60">Shift</kbd>
          <span className="text-base-content/30">+</span>
          <kbd className="inline-flex items-center justify-center min-w-[22px] h-[18px] px-1.5 rounded-md bg-base-200 border border-base-300 text-[9.5px] font-medium font-mono leading-none text-base-content/60">Enter</kbd>
          <span>for new line</span>
        </p>
      </div>
    </div>
  );
}
