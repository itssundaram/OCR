import React, { useMemo, useState } from 'react';
import { Check, Copy, Edit2, RefreshCw, Search, Trash2, X, Zap, ScanSearch, LayoutTemplate } from 'lucide-react';

/**
 * Key : value view of everything extracted from a document.
 *
 * Two groups:
 *   - "Template fields"   — what the template asks for (always listed, even when empty)
 *   - "Found on document" — every "Label: value" pair discovered by scanning the page
 *
 * This component only renders; the parent owns the data and the save / re-OCR /
 * remove behaviour and passes handlers in.
 */

const scoreTone = (pct) =>
  pct >= 80 ? 'text-emerald-600' : pct >= 50 ? 'text-amber-600' : 'text-red-500';
const dotTone = (pct) =>
  pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-400';

const prettyKey = (key) => key.replace(/_/g, ' ');

const KeyValuePanel = ({
  fields,
  selectedField,
  hoveredField,
  onSelect,
  onHover,
  editingField,
  editValue,
  setEditValue,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onCopy,
  copiedKey,
  onReOcr,
  reOcrLoading,
  onRemove,
  rowRefs,
}) => {
  const [query, setQuery] = useState('');
  const [copiedAll, setCopiedAll] = useState(false);

  const { template, discovered } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = ([key, d]) => {
      if (!q) return true;
      const label = (d?.label || prettyKey(key)).toLowerCase();
      return label.includes(q) || String(d?.value ?? '').toLowerCase().includes(q);
    };
    const all = Object.entries(fields || {});
    return {
      template: all.filter(([, d]) => d?.source !== 'discovered').filter(match),
      discovered: all.filter(([, d]) => d?.source === 'discovered').filter(match),
    };
  }, [fields, query]);

  const totals = useMemo(() => {
    const all = Object.values(fields || {});
    return {
      template: all.filter((d) => d?.source !== 'discovered').length,
      discovered: all.filter((d) => d?.source === 'discovered').length,
    };
  }, [fields]);

  const handleCopyAll = async () => {
    const out = {};
    Object.entries(fields || {}).forEach(([key, d]) => {
      if (d?.found && d?.value !== undefined && String(d.value).trim() !== '') out[key] = d.value;
    });
    try {
      await navigator.clipboard.writeText(JSON.stringify(out, null, 2));
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 1800);
    } catch {
      /* clipboard unavailable: nothing to do */
    }
  };

  const renderRow = ([key, data]) => {
    const d = data || {};
    const isDiscovered = d.source === 'discovered';
    const label = d.label || prettyKey(key);
    const pct = Math.round((d.confidence || 0) * 100);
    const hasValue = Boolean(d.found && d.value !== undefined && String(d.value).trim() !== '');
    const hasBbox = d.bbox && d.bbox.x1 !== undefined;
    const isSelected = selectedField === key;
    const isHovered = hoveredField === key;
    const isEditing = editingField === key;

    return (
      <div
        key={key}
        ref={(el) => {
          if (rowRefs) rowRefs.current[key] = el;
        }}
        onClick={() => onSelect(key)}
        onMouseEnter={() => onHover(key)}
        onMouseLeave={() => onHover(null)}
        className={`group relative grid grid-cols-[minmax(6.5rem,0.8fr)_minmax(0,2fr)_auto] gap-x-3 items-start px-3.5 py-2 cursor-pointer border-l-4 transition-colors ${
          isSelected
            ? 'bg-blue-50/80 border-l-blue-600'
            : isHovered
            ? 'bg-slate-50 border-l-blue-300'
            : 'border-l-transparent hover:bg-slate-50/70'
        }`}
      >
        {/* key : */}
        <div className="flex items-baseline gap-1 min-w-0 pt-0.5">
          <span
            className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 break-words"
            title={label}
          >
            {label}
          </span>
          <span className="text-slate-300 font-bold flex-shrink-0">:</span>
        </div>

        {/* value */}
        <div className="min-w-0" onClick={(e) => isEditing && e.stopPropagation()}>
          {isEditing ? (
            <div className="flex items-center gap-1">
              <input
                type="text"
                autoFocus
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSaveEdit(key);
                  if (e.key === 'Escape') onCancelEdit();
                }}
                className="w-full bg-white border border-blue-500 rounded px-2 py-1 text-xs font-mono font-semibold text-slate-900 outline-none"
              />
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSaveEdit(key);
                }}
                className="p-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded cursor-pointer"
                title="Save"
              >
                <Check size={11} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCancelEdit();
                }}
                className="p-1 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded cursor-pointer"
                title="Cancel"
              >
                <X size={11} />
              </button>
            </div>
          ) : hasValue ? (
            <span className="font-mono text-xs font-semibold text-slate-900 break-words whitespace-pre-wrap">
              {String(d.value)}
            </span>
          ) : (
            <span className="text-xs italic text-slate-400">not found</span>
          )}
        </div>

        {/* score */}
        <div className="flex items-center justify-end gap-1.5 pt-0.5 w-14">
          {d.manually_edited && (
            <span className="text-[9px] font-bold px-1 rounded bg-purple-100 text-purple-700 font-mono">edited</span>
          )}
          {hasValue && !d.manually_edited && (
            <>
              <span className={`w-1.5 h-1.5 rounded-full ${dotTone(pct)}`} />
              <span className={`font-mono text-[11px] font-bold ${scoreTone(pct)}`}>{pct}</span>
            </>
          )}
        </div>

        {/* actions: float over the right edge on hover / selection so the layout never shifts */}
        <div
          className={`absolute right-2 top-1.5 items-center gap-0.5 rounded-md border border-slate-200 bg-white px-1 py-0.5 shadow-sm ${
            (isSelected || isHovered) && !isEditing ? 'flex' : 'hidden'
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          {hasBbox && (
            <button
              onClick={() => onReOcr(key)}
              disabled={reOcrLoading[key]}
              className="p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded cursor-pointer"
              title="Re-run OCR on this box"
            >
              {reOcrLoading[key] ? <RefreshCw size={11} className="animate-spin" /> : <Zap size={11} />}
            </button>
          )}
          <button
            onClick={() => onStartEdit(key, d.value)}
            className="p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded cursor-pointer"
            title="Edit value"
          >
            <Edit2 size={11} />
          </button>
          <button
            onClick={() => onCopy(key, d.value)}
            className="p-1 text-slate-400 hover:text-slate-800 hover:bg-slate-100 rounded cursor-pointer"
            title="Copy value"
          >
            {copiedKey === key ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />}
          </button>
          <button
            onClick={() => onRemove(key, isDiscovered)}
            className="p-1 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded cursor-pointer"
            title={isDiscovered ? 'Dismiss this pair' : 'Remove field from template'}
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>
    );
  };

  const renderGroup = (title, Icon, subtitle, entries, total) => (
    <section className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
      <header className="flex items-center justify-between gap-2 px-3.5 py-2 bg-slate-50 border-b border-slate-200">
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={13} className="text-slate-500 flex-shrink-0" />
          <h3 className="text-[11px] font-bold uppercase tracking-wider text-slate-700">{title}</h3>
          <span className="text-[10px] font-mono text-slate-400">
            {entries.length === total ? total : `${entries.length} of ${total}`}
          </span>
        </div>
        <span className="text-[10px] text-slate-400 hidden md:inline truncate">{subtitle}</span>
      </header>
      {entries.length === 0 ? (
        <div className="px-3.5 py-4 text-xs text-slate-400 italic">
          {total === 0 ? 'Nothing here.' : 'No match for this filter.'}
        </div>
      ) : (
        <div className="divide-y divide-slate-100">{entries.map(renderRow)}</div>
      )}
    </section>
  );

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50/50 p-3 space-y-3">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by label or value"
            className="w-full pl-7 pr-2.5 py-1.5 text-xs bg-white border border-slate-200 rounded-lg outline-none focus:border-blue-400 placeholder:text-slate-400"
          />
        </div>
        <button
          onClick={handleCopyAll}
          className="inline-flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer"
          title="Copy every found value as a JSON object"
        >
          {copiedAll ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />} Copy JSON
        </button>
      </div>

      {totals.template > 0 &&
        renderGroup('Template fields', LayoutTemplate, 'asked for by this template', template, totals.template)}

      {renderGroup(
        'Found on document',
        ScanSearch,
        'every "label: value" pair read from the page',
        discovered,
        totals.discovered
      )}
    </div>
  );
};

export default KeyValuePanel;
