/**
 * DOCINT - live job events (SSE) for the fast_ocr pipeline.
 *
 * Opens GET /stream/jobs/{id} and folds the events into one small state object.
 * It never replaces polling: the result view keeps polling the job as before
 * and shows the final data; this only adds field-by-field results while the
 * job is still running. If SSE is unavailable the hook stays empty and the
 * page behaves exactly as it did.
 *
 * Event envelope v1: {v, job_id, seq, ts, type, page, data}. `seq` rises by one
 * per event, so anything with seq <= the last seen one is a duplicate.
 */
import { useEffect, useState } from 'react';
import { config } from '../config';

export const EVENT_TYPES = [
  'job_started', 'page_started', 'field_started', 'field_extracted', 'field_validated',
  'table_extracted', 'fallback_started', 'field_failed', 'page_completed',
  'job_completed', 'job_failed', 'resync_required',
];

export const initialLiveState = {
  connected: false,
  status: 'idle',            // idle | running | completed | failed
  pagesTotal: 0,
  pagesDone: 0,
  fieldsTotal: 0,
  fields: {},                // name -> {name,label,value,confidence,state,provisional,source,page,bbox,reason}
  tables: [],
  error: null,
  needsResync: false,
  events: 0,
};

/** Pure reducer: one event in, new state out. */
export function applyEvent(state, env) {
  const d = env.data || {};
  const next = { ...state, events: state.events + 1 };
  switch (env.type) {
    case 'job_started':
      return { ...next, status: 'running', pagesTotal: d.pages_total || 0, fieldsTotal: d.fields_total || 0 };
    case 'page_completed':
      return { ...next, pagesDone: d.pages_done ?? state.pagesDone, pagesTotal: d.pages_total ?? state.pagesTotal };
    case 'field_extracted':
    case 'field_validated': {
      if (!d.field) return next;
      const prev = state.fields[d.field] || {};
      return {
        ...next,
        fields: {
          ...state.fields,
          [d.field]: {
            ...prev,
            name: d.field, label: d.label || prev.label || d.field, value: d.value,
            confidence: d.confidence, state: d.state || (env.type === 'field_validated' ? 'validated' : 'extracted'),
            provisional: env.type === 'field_extracted', source: d.source, page: env.page, bbox: d.bbox,
            reason: d.reason || '', fallback: false,
          },
        },
      };
    }
    case 'fallback_started': {
      if (!d.field) return next;
      const prev = state.fields[d.field] || { name: d.field, label: d.field, value: '' };
      return { ...next, fields: { ...state.fields, [d.field]: { ...prev, state: 'processing', fallback: true } } };
    }
    case 'field_failed': {
      if (!d.field) return next;
      const prev = state.fields[d.field] || { name: d.field, label: d.field, value: '' };
      return { ...next, fields: { ...state.fields, [d.field]: { ...prev, state: 'failed', reason: d.reason || '', provisional: false } } };
    }
    case 'table_extracted':
      return { ...next, tables: [...state.tables, { page: env.page, index: d.table_index, headers: d.headers, rows: d.rows }] };
    case 'job_completed':
      return { ...next, status: 'completed' };
    case 'job_failed':
      return { ...next, status: 'failed', error: d.error || null };
    case 'resync_required':
      return { ...next, needsResync: true };
    default:
      return next;
  }
}

export function useJobStream(jobId, enabled) {
  const [state, setState] = useState(initialLiveState);

  useEffect(() => {
    if (!jobId || !enabled || typeof EventSource === 'undefined') return undefined;
    setState(initialLiveState);
    let last = 0;
    let closed = false;
    const es = new EventSource(`${config.API_BASE_URL}/stream/jobs/${jobId}`);

    const onEvent = (e) => {
      let env;
      try { env = JSON.parse(e.data); } catch { return; }
      if (typeof env.seq === 'number') {
        if (env.seq <= last) return;            // duplicate or replay
        last = env.seq;
      }
      setState((s) => applyEvent(s, env));
      if (['job_completed', 'job_failed', 'resync_required'].includes(env.type)) {
        closed = true;
        es.close();                              // the server ends the stream; do not auto-reconnect
      }
    };
    EVENT_TYPES.forEach((t) => es.addEventListener(t, onEvent));
    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onerror = () => { if (!closed) setState((s) => ({ ...s, connected: false })); };

    return () => { closed = true; es.close(); };
  }, [jobId, enabled]);

  return state;
}

export default useJobStream;
