import React from 'react';

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

const LiveFieldsPanel = ({ live }) => {
  if (!live || live.events === 0 || live.status === 'idle') return null;
  const rows = Object.values(live.fields);
  return (
    <div className="absolute top-3 right-3 z-40 w-72 max-h-[70%] overflow-auto rounded-lg border border-slate-200 bg-white/95 shadow-lg backdrop-blur pointer-events-auto text-xs">
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100 sticky top-0 bg-white/95">
        <span className="font-semibold text-slate-700">Live results</span>
        <span className="text-slate-500">
          {live.pagesTotal ? `page ${live.pagesDone}/${live.pagesTotal}` : 'starting'}
          {live.tables.length > 0 && ` · ${live.tables.length} table${live.tables.length > 1 ? 's' : ''}`}
        </span>
      </div>
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
      {live.status === 'failed' && (
        <div className="px-3 py-2 text-red-600 border-t border-slate-100">{live.error?.message || 'Job failed'}</div>
      )}
    </div>
  );
};

export default LiveFieldsPanel;
