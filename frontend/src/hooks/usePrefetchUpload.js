import { useCallback, useEffect, useRef } from 'react';
import { extractService } from '../services/extract';

/**
 * Prefetch: the chosen document is uploaded and processed in the background (as a hidden job) while the person
 * is still looking at the form, so "Execute extraction" only has to commit it.
 *
 *   start(ctx)   begin (or keep) the prefetch for { file, dept, template, pipeline }. A different file, department,
 *                template or pipeline discards the previous prefetch at once and starts a new one.
 *   stop()       discard whatever is prefetching (file removed, form changed to something incomplete)
 *   commit(ctx)  Execute: commit the matching prefetch, or fall back to an ordinary upload when there is none
 *                (it failed, was reaped, or the form changed after it started). Always resolves to job info.
 *
 * Race rules: every prefetch is an entry object; only `current` may be committed; an entry that was replaced is
 * flagged `cancelled`, so a late upload response discards the job it created instead of using it. A request whose
 * bytes are not fully sent yet is aborted; one that is fully sent is allowed to finish and is then discarded
 * (aborting then could leave a job on the server nobody knows about; the server reaps those after a while anyway).
 */
export const prefetchKey = ({ file, dept, template, pipeline } = {}) =>
  file && dept && template && pipeline
    ? [dept, template, pipeline, file.name, file.size, file.lastModified].join('|')
    : '';

const log = () => {};   // flip to console.debug while diagnosing

export default function usePrefetchUpload({ enabled = true, pipelines = null, onError } = {}) {
  const currentRef = useRef(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const allowed = useCallback(
    (pipeline) => enabled && (!pipelines || pipelines.has(pipeline)),
    [enabled, pipelines]
  );

  const discardEntry = useCallback((entry) => {
    if (!entry || entry.cancelled) return;
    entry.cancelled = true;
    if (!entry.sent && !entry.jobId) entry.controller.abort();          // bytes still going out: just stop them
    if (entry.jobId && !entry.ordinary) {
      extractService.discardPrefetch(entry.jobId).catch(() => { /* the server reaps it */ });
    }
    log('discard', entry.key, entry.jobId || '(upload in flight)');
  }, []);

  const stop = useCallback(() => {
    const entry = currentRef.current;
    currentRef.current = null;
    discardEntry(entry);
  }, [discardEntry]);

  const start = useCallback((ctx) => {
    const key = prefetchKey(ctx);
    if (!key || !allowed(ctx.pipeline)) { stop(); return; }
    const existing = currentRef.current;
    if (existing && existing.key === key && !existing.failed) return;     // already prefetching exactly this
    stop();                                                                // previous document: discard right now

    const entry = {
      key, cancelled: false, failed: false, sent: false, jobId: null, ordinary: false,
      controller: new AbortController(), data: null, promise: null,
    };
    currentRef.current = entry;
    entry.promise = extractService
      .uploadWithPipeline(ctx.dept, ctx.template, ctx.pipeline, ctx.file, false, {
        prefetch: true,
        signal: entry.controller.signal,
        onUploadProgress: (ev) => { if (ev.total && ev.loaded >= ev.total) entry.sent = true; },
      })
      .then((res) => {
        const data = res.data;
        if (entry.cancelled) {                                             // replaced while uploading
          if (data?.prefetch) extractService.discardPrefetch(data.job_id).catch(() => {});
          return null;
        }
        entry.jobId = data.job_id;
        entry.data = data;
        entry.ordinary = data.prefetch === false;                          // server has prefetch switched off: a normal job
        log('ready', key, data.job_id);
        return data;
      })
      .catch((err) => {
        if (entry.cancelled) return null;
        entry.failed = true;
        log('failed', err?.message);
        if (onErrorRef.current) onErrorRef.current(err);
        return null;
      });
  }, [allowed, stop]);

  const commit = useCallback(async (ctx) => {
    const key = prefetchKey(ctx);
    const entry = currentRef.current;
    if (entry && entry.key === key && !entry.cancelled && !entry.failed) {
      const data = await entry.promise;                                    // still uploading: wait for it (no worse than a plain upload)
      if (data && entry.jobId && currentRef.current === entry && !entry.cancelled) {
        try {
          const res = await extractService.commitPrefetch(entry.jobId);
          currentRef.current = null;
          return { ...data, ...res.data, prefetched: true };
        } catch (err) {
          log('commit failed, uploading again', err?.code || err?.message);   // PREFETCH_GONE etc.
        }
      }
    }
    stop();
    const res = await extractService.uploadWithPipeline(ctx.dept, ctx.template, ctx.pipeline, ctx.file, false);
    return { ...res.data, prefetched: false };
  }, [stop]);

  useEffect(() => {
    const onUnload = () => {
      const entry = currentRef.current;
      if (entry?.jobId && !entry.ordinary) extractService.discardPrefetchBeacon(entry.jobId);
    };
    window.addEventListener('pagehide', onUnload);
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('pagehide', onUnload);
      window.removeEventListener('beforeunload', onUnload);
      const entry = currentRef.current;
      currentRef.current = null;
      discardEntry(entry);
    };
  }, [discardEntry]);

  return { start, stop, commit };
}
