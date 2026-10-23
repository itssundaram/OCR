import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

/**
 * Field-by-field results while a fast_ocr job is still running.
 * Purely presentational: the data comes from useJobStream. Shows nothing
 * until the first live event arrives, so other pipelines are unaffected.
 */
const STATE_STYLE = {
  extracted: 'bg-amber-100 text-amber-800 border-amber-200',
  processing: 'bg-sky-100 text-sky-800 border-sky-200',
  validated: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  review_required: 'bg-orange-100 text-orange-800 border-orange-200',
  failed: 'bg-red-100 text-red-700 border-red-200',
};
const STATE_LABEL = {
  extracted: 'reading', processing: 'repairing', validated: 'validated',
  review_required: 'review', failed: 'not found',
};

const STORE_KEY = 'docint.liveResults.collapsed';

const readCollapsed = () => {
  try {
    return window.localStorage.getItem(STORE_KEY) === '1';
  } catch {
    return false; // storage can be blocked: the panel simply starts open
  }
};

const LiveFieldsPanel = ({ live }) => {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  useEffect(() => {
    try {
      window.localStorage.setItem(STORE_KEY, collapsed ? '1' : '0');
    } catch {
      /* not persisted: fine */
    }
  }, [collapsed]);

  if (!live || live.events === 0 || live.status === 'idle') return null;
  const rows = Object.values(live.fields);
  const progress = live.pagesTotal ? `page ${live.pagesDone}/${live.pagesTotal}` : 'starting';
  const done = rows.filter((f) => f.value).length;
  return (
    <div className={`absolute top-3 right-3 z-40 ${collapsed ? 'w-auto' : 'w-72 max-h-[70%] overflow-auto'} rounded-lg border border-slate-200 bg-white/95 shadow-lg backdrop-blur pointer-events-auto text-xs`}>
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
        title={collapsed ? 'Show live results' : 'Hide live results'}
        className={`w-full flex items-center justify-between gap-3 px-3 py-2 ${collapsed ? '' : 'border-b border-slate-100 sticky top-0 bg-white/95'} cursor-pointer text-left`}
      >
        <span className="flex items-center gap-1.5 font-semibold text-slate-700">
          {collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
          Live results
        </span>
        <span className="text-slate-500">
          {collapsed ? `${done}/${rows.length || '…'} · ${progress}` : progress}
          {!collapsed && live.tables.length > 0 && ` · ${live.tables.length} table${live.tables.length > 1 ? 's' : ''}`}
        </span>
      </button>
      {!collapsed && (
      <ul className="divide-y divide-slate-100">
        {rows.length === 0 && <li className="px-3 py-2 text-slate-400">Reading the page…</li>}
        {rows.map((f) => (
          <li key={f.name} className="px-3 py-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-slate-500 truncate">{String(f.label || f.name).replace(/_/g, ' ')}</span>
              <span className={`shrink-0 border rounded px-1 text-[10px] ${STATE_STYLE[f.state] || STATE_STYLE.extracted}`}>
                {STATE_LABEL[f.state] || f.state}
              </span>
            </div>
            <div className={`font-mono break-words ${f.provisional ? 'text-slate-500 italic' : 'text-slate-800'}`}>
              {f.value || (f.state === 'failed' ? '—' : '')}
            </div>
            {f.reason && f.state !== 'validated' && <div className="text-[10px] text-slate-400">{f.reason.replace(/_/g, ' ')}</div>}
          </li>
        ))}
      </ul>
      )}
      {!collapsed && live.status === 'failed' && (
        <div className="px-3 py-2 text-red-600 border-t border-slate-100">{live.error?.message || 'Job failed'}</div>
      )}
    </div>
  );
};

export default LiveFieldsPanel;
