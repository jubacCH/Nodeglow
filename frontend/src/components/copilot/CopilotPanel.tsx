'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Sparkles, X, Send, AlertCircle } from 'lucide-react';
import { useGlowStore } from '@/stores/glow';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

/** Lightweight markdown → HTML for chat messages (no external deps). */
function renderMarkdown(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')  // escape HTML
    .replace(/^### (.+)$/gm, '<strong class="text-sky-300 text-xs uppercase tracking-wide">$1</strong>')
    .replace(/^## (.+)$/gm, '<strong class="text-sky-300 text-sm">$1</strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-slate-50">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-white/[0.08] text-sky-200 text-xs">$1</code>')
    .replace(/^- (.+)$/gm, '<span class="flex gap-1.5"><span class="text-slate-500">•</span><span>$1</span></span>')
    .replace(/^(\d+)\. (.+)$/gm, '<span class="flex gap-1.5"><span class="text-slate-500">$1.</span><span>$2</span></span>');
}

const SUGGESTIONS = [
  'What\'s unusual right now?',
  'Show error trends',
  'Which hosts need attention?',
];

function getCsrfToken(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/ng_csrf=([^;]+)/);
  if (!match) return '';
  return decodeURIComponent(match[1]);
}

export function GlowPanel() {
  const { isOpen, close } = useGlowStore();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isStreaming]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isStreaming) return;

    const userMsg: Message = { role: 'user', content: text.trim() };
    const history = messages.map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsStreaming(true);
    setError(null);

    // Add a placeholder assistant message
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);

    try {
      const res = await fetch('/api/v1/glow/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-csrf-token': getCsrfToken(),
        },
        credentials: 'include',
        body: JSON.stringify({ message: text.trim(), history }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        setError(data.error || `Request failed (${res.status})`);
        // Remove the empty assistant message
        setMessages((prev) => prev.slice(0, -1));
        setIsStreaming(false);
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        setError('Streaming not supported');
        setMessages((prev) => prev.slice(0, -1));
        setIsStreaming(false);
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const data = JSON.parse(jsonStr);
            if (data.done) continue;
            if (data.delta) {
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last && last.role === 'assistant') {
                  updated[updated.length - 1] = {
                    ...last,
                    content: last.content + data.delta,
                  };
                }
                return updated;
              });
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection failed');
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'assistant' && !last.content) {
          return prev.slice(0, -1);
        }
        return prev;
      });
    } finally {
      setIsStreaming(false);
    }
  }, [messages, isStreaming]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  if (!isOpen) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col w-[420px] h-[500px] rounded-xl border bg-[#0a0e1a]/95 backdrop-blur-xl shadow-2xl"
      style={{ borderColor: 'rgba(255,255,255,0.08)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'rgba(255,255,255,0.08)' }}>
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-violet-400" />
          <span className="text-sm font-semibold bg-gradient-to-r from-sky-400 to-violet-400 bg-clip-text text-transparent">
            Glow
          </span>
        </div>
        <button
          onClick={close}
          className="p-1 rounded-md text-slate-400 hover:text-slate-200 hover:bg-white/[0.08] transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Sparkles size={32} className="text-violet-400/40 mb-3" />
            <p className="text-sm text-slate-400 mb-4">Ask about your infrastructure</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="px-3 py-1.5 text-xs rounded-full border border-white/[0.08] text-slate-400 hover:text-sky-400 hover:border-sky-500/30 hover:bg-sky-500/5 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] px-3 py-2 rounded-lg text-sm ${
                msg.role === 'user'
                  ? 'bg-sky-500/15 text-sky-50 border border-sky-500/20'
                  : 'bg-white/[0.06] text-slate-100 border border-white/[0.08]'
              }`}
            >
              {msg.role === 'assistant' ? (
                <div className="whitespace-pre-wrap break-words leading-relaxed" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
              ) : (
                <div className="whitespace-pre-wrap break-words">{msg.content}</div>
              )}
              {msg.role === 'assistant' && isStreaming && i === messages.length - 1 && (
                <span className="inline-flex gap-0.5 ml-1">
                  <span className="w-1 h-1 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-1 h-1 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-1 h-1 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                </span>
              )}
            </div>
          </div>
        ))}

        {error && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20">
            <AlertCircle size={14} className="text-red-400 shrink-0 mt-0.5" />
            <p className="text-xs text-red-300">{error}</p>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t" style={{ borderColor: 'rgba(255,255,255,0.08)' }}>
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your infrastructure..."
            rows={1}
            className="flex-1 resize-none rounded-lg px-3 py-2 text-sm bg-white/[0.04] border border-white/[0.08] text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-sky-500/50 transition-colors"
            disabled={isStreaming}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || isStreaming}
            className="p-2 rounded-lg bg-sky-500/20 text-sky-400 hover:bg-sky-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
