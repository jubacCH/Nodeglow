'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { Sparkles, X, Send, AlertCircle, ShieldOff } from 'lucide-react';
import { useGlowStore } from '@/stores/glow';
import { useAuthStore } from '@/stores/auth';
import { sanitizeHtml } from '@/lib/sanitize';
import { getCsrfToken } from '@/lib/api';
import { cn } from '@/lib/utils';
import { IconButton, buttonClasses } from '@/components/ui/Button';
import { AI_STATUS_KEY, aiUnavailableMessage, useAiStatus } from '@/hooks/queries/useAiStatus';
import { hasFeature, useFeatures } from '@/hooks/queries/useFeatures';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

/** Lightweight markdown → HTML for chat messages (no external deps). */
function renderMarkdown(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<strong class="glow-heading text-meta uppercase tracking-wide">$1</strong>')
    .replace(/^## (.+)$/gm, '<strong class="glow-heading text-ui">$1</strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="glow-bold">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="glow-code px-1 py-0.5 rounded text-meta">$1</code>')
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
  const { data: features } = useFeatures(isOpen);
  // Glow is an enterprise feature (/api/v1/glow/chat only exists there).
  const installed = hasFeature(features, 'ai_assistant');
  const { data: aiStatus } = useAiStatus(isOpen && installed);
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

  if (!isOpen || !installed) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-[500px] w-[420px] max-w-[calc(100vw-32px)] flex-col rounded-ng-lg border border-border-2 bg-surface shadow-overlay">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-accent" aria-hidden="true" />
          <span className="text-body font-semibold text-fg">Glow</span>
        </div>
        <IconButton aria-label="Close Glow" size="sm" onClick={close}>
          <X size={16} aria-hidden="true" />
        </IconButton>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {aiUnavailable && messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center px-4 text-center" data-testid="glow-ai-disabled">
            <ShieldOff size={32} className="mb-3 text-fg-3" aria-hidden="true" />
            <p className="mb-2 text-ui font-medium text-fg">Glow is not available</p>
            <p className="mb-4 text-meta text-fg-3">{aiUnavailableMessage(aiStatus, isAdmin)}</p>
            {isAdmin && (
              <Link
                href="/settings?tab=ai"
                onClick={close}
                className={buttonClasses({ variant: 'secondary', size: 'sm' })}
              >
                Open AI settings
              </Link>
            )}
          </div>
        )}
        {!aiUnavailable && messages.length === 0 && !error && (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <Sparkles size={32} className="mb-3 text-accent/40" aria-hidden="true" />
            <p className="mb-4 text-ui text-fg-3">Ask about your infrastructure</p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => sendMessage(s)}
                  className="rounded-pill border border-border-2 px-3 py-1.5 text-meta text-fg-2 transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-accent"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}>
            <div
              className={cn(
                'max-w-[85%] rounded-ctl border px-3 py-2 text-ui text-fg',
                msg.role === 'user' ? 'border-accent/40 bg-accent-soft' : 'border-border bg-surface-2',
              )}
            >
              {msg.role === 'assistant' ? (
                <div className="whitespace-pre-wrap break-words leading-relaxed" dangerouslySetInnerHTML={{ __html: sanitizeHtml(renderMarkdown(msg.content)) }} />
              ) : (
                <div className="whitespace-pre-wrap break-words">{msg.content}</div>
              )}
              {msg.role === 'assistant' && isStreaming && i === messages.length - 1 && (
                <span className="ml-1 inline-flex gap-0.5" aria-hidden="true">
                  <span className="h-1 w-1 animate-bounce rounded-full bg-fg-3 [animation-delay:0ms]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-fg-3 [animation-delay:150ms]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-fg-3 [animation-delay:300ms]" />
                </span>
              )}
            </div>
          </div>
        ))}

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-ctl border border-down/30 bg-down-soft p-3">
            <AlertCircle size={14} className="mt-0.5 shrink-0 text-down" aria-hidden="true" />
            <p className="text-meta text-down">{error}</p>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={aiUnavailable ? 'AI features are off' : 'Ask about your infrastructure...'}
            aria-label="Message to Glow"
            rows={1}
            className="ng-input min-h-[36px] flex-1 resize-none"
            disabled={isStreaming || aiUnavailable}
          />
          <IconButton
            variant="primary"
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || isStreaming || aiUnavailable}
            aria-label="Send message"
          >
            <Send size={16} aria-hidden="true" />
          </IconButton>
        </div>
      </div>
    </div>
  );
}
