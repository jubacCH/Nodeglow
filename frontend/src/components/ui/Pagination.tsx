'use client';

import { ChevronLeft, ChevronRight } from 'lucide-react';

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  const from = page * pageSize + 1;
  const to = Math.min((page + 1) * pageSize, total);

  return (
    <div className="flex items-center justify-between px-4 py-3 border-t border-white/[0.06]">
      <span className="text-xs text-slate-500">
        {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-200 hover:bg-white/[0.08] transition-colors disabled:opacity-30 disabled:pointer-events-none"
        >
          <ChevronLeft size={16} />
        </button>
        {Array.from({ length: totalPages }).map((_, i) => {
          // Show first, last, and pages near current
          if (totalPages > 7 && i > 1 && i < totalPages - 2 && Math.abs(i - page) > 1) {
            if (i === 2 && page > 3) return <span key={i} className="px-1 text-xs text-slate-600">...</span>;
            if (i === totalPages - 3 && page < totalPages - 4) return <span key={i} className="px-1 text-xs text-slate-600">...</span>;
            return null;
          }
          return (
            <button
              key={i}
              onClick={() => onPageChange(i)}
              className={`min-w-[28px] h-7 rounded-md text-xs font-medium transition-colors ${
                i === page
                  ? 'accent-bg text-white'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.08]'
              }`}
            >
              {i + 1}
            </button>
          );
        })}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-200 hover:bg-white/[0.08] transition-colors disabled:opacity-30 disabled:pointer-events-none"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
