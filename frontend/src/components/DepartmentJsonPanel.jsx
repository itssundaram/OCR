import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, Download, RefreshCw } from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { extractService } from '../services/extract';

/**
 * The JSON a department system receives: template fields as flat key -> value, other fields found on the page
 * apart, every table as keyed rows. It is fetched from the server (which builds it from the saved result), and
 * fetched again shortly after any field or cell is corrected (`editSignature` changes), so what is shown is what
 * would be sent. "Review data" shows the screen's own working data instead.
 */
const DepartmentJsonPanel = ({ jobId, editSignature, reviewData }) => {
  const [view, setView] = useState('department'); // 'department' | 'review'
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!jobId) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const res = await extractService.getExport(jobId);
      if (mine !== seq.current) return;           // a newer request is on its way
      setData(res.data);
      setError('');
    } catch (err) {
      if (mine === seq.current) setError(err.message || 'Could not load the JSON.');
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    const timer = setTimeout(load, 700);          // lets a save that was just sent reach the server first
    return () => clearTimeout(timer);
  }, [load, editSignature]);

  const shown = view === 'department' ? data : reviewData;
  const text = shown ? JSON.stringify(shown, null, 2) : '';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard can be blocked on an http page; the text stays selectable */
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${view === 'department' ? 'extraction' : 'review-data'}-${String(jobId).slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const tab = (id, label) => (
    <button
      type="button"
      onClick={() => setView(id)}
      className={`px-2.5 py-1 rounded text-[11px] font-bold cursor-pointer ${view === id ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {tab('department', 'Department JSON')}
        {tab('review', 'Review data')}
        <span className="text-[10px] text-slate-400">
          {view === 'department' ? 'What is sent to the department: stable keys, your corrections included.' : 'The review screen’s working data (with confidences and boxes).'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" onClick={load} className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-300 hover:text-white cursor-pointer" title="Reload">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          <button type="button" onClick={copy} disabled={!text} className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-300 hover:text-white cursor-pointer disabled:opacity-40">
            {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" onClick={download} disabled={!text} className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-300 hover:text-white cursor-pointer disabled:opacity-40">
            <Download size={12} /> Download
          </button>
        </div>
      </div>
      {error && view === 'department' && <div className="text-[11px] text-red-300">{error}</div>}
      {shown ? <JsonView data={shown} shouldExpandNode={allExpanded} style={darkStyles} /> : (!error && <div className="text-[11px] text-slate-400">Loading…</div>)}
    </div>
  );
};

export default DepartmentJsonPanel;
