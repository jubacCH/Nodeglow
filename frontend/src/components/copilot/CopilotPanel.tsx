'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { Sparkles, X, Send, AlertCircle, ShieldOff } from 'lucide-react';
import { useGlowStore } from '@/stores/glow';
import { useAuthStore } from '@/stores/auth';
import { sanitizeHtml } from '@/lib/sanitize';
import { getCsrfToken } from '@/lib/api';
import { AI_STATUS_KEY, aiUnavailableMessage, useAiStatus } from '@/hooks/queries/useAiStatus';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

/** Lightweight markdown → HTML for chat messages (no external deps). */
function renderMarkdown(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<strong class="glow-heading text-xs uppercase tracking-wide">$1</strong>')
    .replace(/^## (.+)$/gm, '<strong class="glow-heading text-sm">$1</strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="glow-bold">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="glow-code px-1 py-0.5 rounded text-xs">$1</code>')
    .replace(/^- (.+)$/gm, '<span class="flex gap-1.5"><span class="glow-bullet">•</span><span>$1</span></span>')
    .replace(/^(\d+)\. (.+)$/gm, '<span class="flex gap-1.5"><span class="glow-bullet">$1.</span><span>$2</span></span>');
}

const SUGGESTIONS = [
  'What\'s unusual right now?',
  'Show error trends',
  'Which hosts need attention?',
];

export function GlowPanel() {
  const isOpen = useGlowStore((s) => s.isOpen);
  const close = useGlowStore((s) => s.close);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { data: aiStatus } = useAiStatus(isOpen);
  // Unknown status (still loading / old backend) does not block the chat;
  // the server refuses anyway when AI is off.
  const aiUnavailable = aiStatus ? !aiStatus.available : false;

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
    if (!text.trim() || isStreaming || aiUnavailable) return;

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
        // AI was switched off (or never set up) since the status was fetched.
        if (data.code === 'ai_disabled' || data.code === 'ai_not_configured') {
          qc.invalidateQueries({ queryKey: AI_STATUS_KEY });
        }
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
            if (data.error) {
              setError(data.error);
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                return last && last.role === 'assistant' && !last.content ? prev.slice(0, -1) : prev;
              });
            }
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
  }, [messages, isStreaming, aiUnavailable, qc]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  if (!isOpen) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col w-[420px] h-[500px] rounded-xl border backdrop-blur-xl shadow-2xl"
      style={{
        background: 'var(--ng-surface)',
        borderColor: 'var(--ng-glass-border)',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 border-b"
        style={{ borderColor: 'var(--ng-glass-border)' }}
      >
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-violet-400" />
          <span className="text-sm font-semibold bg-gradient-to-r from-sky-400 to-violet-400 bg-clip-text text-transparent">
            Glow
          </span>
        </div>
        <button
          onClick={close}
          aria-label="Close Glow"
          className="p-1 rounded-md transition-colors"
          style={{ color: 'var(--ng-text-muted)' }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {aiUnavailable && messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center px-4" data-testid="glow-ai-disabled">
            <ShieldOff size={32} className="mb-3" style={{ color: 'var(--ng-text-muted)' }} />
            <p className="text-sm font-medium mb-2" style={{ color: 'var(--ng-text-primary)' }}>
              Glow is not available
            </p>
            <p className="text-xs mb-4" style={{ color: 'var(--ng-text-muted)' }}>
              {aiUnavailableMessage(aiStatus, isAdmin)}
            </p>
            {isAdmin && (
              <Link
                href="/settings?tab=ai"
                onClick={close}
                className="px-3 py-1.5 text-xs rounded-full border text-sky-400 border-sky-500/30 hover:bg-sky-500/5 transition-colors"
              >
                Open AI settings
              </Link>
            )}
          </div>
        )}
        {!aiUnavailable && messages.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Sparkles size={32} className="text-violet-400/40 mb-3" />
            <p className="text-sm mb-4" style={{ color: 'var(--ng-text-muted)' }}>
              Ask about your infrastructure
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="px-3 py-1.5 text-xs rounded-full border hover:text-sky-400 hover:border-sky-500/30 hover:bg-sky-500/5 transition-colors"
                  style={{
                    borderColor: 'var(--ng-glass-border)',
                    color: 'var(--ng-text-secondary)',
                  }}
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
              className="max-w-[85%] px-3 py-2 rounded-lg text-sm border"
              style={
                msg.role === 'user'
                  ? {
                      background: 'var(--ng-accent-bg, rgba(56, 189, 248, 0.12))',
                      borderColor: 'var(--ng-accent-border, rgba(56, 189, 248, 0.2))',
                      color: 'var(--ng-text-primary)',
                    }
                  : {
                      background: 'var(--ng-glass-bg)',
                      borderColor: 'var(--ng-glass-border)',
                      color: 'var(--ng-text-primary)',
                    }
              }
            >
              {msg.role === 'assistant' ? (
                <div className="whitespace-pre-wrap break-words leading-relaxed" dangerouslySetInnerHTML={{ __html: sanitizeHtml(renderMarkdown(msg.content)) }} />
              ) : (
                <div className="whitespace-pre-wrap break-words">{msg.content}</div>
              )}
              {msg.role === 'assistant' && isStreaming && i === messages.length - 1 && (
                <span className="inline-flex gap-0.5 ml-1">
                  <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: 'var(--ng-text-muted)', animationDelay: '0ms' }} />
                  <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: 'var(--ng-text-muted)', animationDelay: '150ms' }} />
                  <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: 'var(--ng-text-muted)', animationDelay: '300ms' }} />
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
      <div className="px-4 py-3 border-t" style={{ borderColor: 'var(--ng-glass-border)' }}>
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={aiUnavailable ? 'AI features are off' : 'Ask about your infrastructure...'}
            rows={1}
            className="flex-1 resize-none rounded-lg px-3 py-2 text-sm border focus:outline-none focus:ring-1 focus:ring-sky-500/50 transition-colors"
            style={{
              background: 'var(--ng-glass-bg)',
              borderColor: 'var(--ng-glass-border)',
              color: 'var(--ng-text-primary)',
            }}
            disabled={isStreaming || aiUnavailable}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || isStreaming || aiUnavailable}
            aria-label="Send message"
            className="p-2 rounded-lg bg-sky-500/20 text-sky-400 hover:bg-sky-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
