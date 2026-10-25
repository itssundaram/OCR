import React from 'react';
import { FileSearch } from 'lucide-react';

/**
 * What kind of form each page looks like (ocr_metadata.page_types: {"1": {type, score}, ...}). Informational:
 * nothing is filtered or blocked by it. Nothing is drawn when the backend recognised no page.
 */
export const pageTypeLabel = (type) => {
  const parts = String(type || '').split('_').filter(Boolean);
  if (parts.length === 0) return '';
  const family = parts[0].toUpperCase();
  const rest = parts.slice(1).join(' ');
  return rest ? `${family} · ${rest.charAt(0).toUpperCase()}${rest.slice(1)}` : family;
};

const PageTypeStrip = ({ pageTypes }) => {
  const entries = Object.entries(pageTypes || {})
    .filter(([, v]) => v && v.type)
    .sort((a, b) => Number(a[0]) - Number(b[0]));
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]" data-testid="page-types">
      <span className="inline-flex items-center gap-1 font-bold text-slate-500"><FileSearch size={12} /> Form type</span>
      {entries.map(([page, v]) => (
        <span
          key={page}
          title={`Match ${Math.round((Number(v.score) || 0) * 100)}% — informational, nothing is filtered by it`}
          className="px-2 py-0.5 rounded-full bg-blue-50 text-blue-800 border border-blue-200 font-medium"
        >
          p{page}: {pageTypeLabel(v.type)}
        </span>
      ))}
    </div>
  );
};

export default PageTypeStrip;
