/**
 * DOCINT — Logs page.
 * System-wide recent activity feed with filters and an expandable
 * per-event drill-down (metadata, duration, pipeline run, page), plus the
 * worker/GPU/pipeline health strip.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Activity, RefreshCw, Cpu, Database, AlertTriangle, CheckCircle2, ChevronRight,
} from 'lucide-react';
import { eventsService } from '../services/events';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const statusVariant = (status) => {
  switch (status) {
    case 'COMPLETED': return 'success';
    case 'WARNING': return 'warning';
    case 'FAILED': return 'danger';
    case 'RUNNING': return 'info';
    default: return 'default';
  }
};

const LogsPage = () => {
  const [events, setEvents] = useState([]);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [filters, setFilters] = useState({ event_type: '', status: '' });

  const fetchAll = useCallback(async () => {
    try {
      const params = { limit: 150 };
      if (filters.event_type) params.event_type = filters.event_type;
      if (filters.status) params.status = filters.status;
      const [eventsRes, healthRes] = await Promise.all([
        eventsService.recent(params),
        pipelinesService.health(),
      ]);
      setEvents(eventsRes.data || []);
      setHealth(healthRes.data);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load logs');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchAll();
    if (!autoRefresh) return undefined;
    const interval = setInterval(fetchAll, 5000);
    return () => clearInterval(interval);
  }, [fetchAll, autoRefresh]);

  const eventTypes = Array.from(new Set(events.map((e) => e.event_type))).sort();
  const statuses = Array.from(new Set(events.map((e) => e.status).filter(Boolean))).sort();

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto p-4 w-full h-full">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-2xl font-bold text-primary font-heading flex items-center gap-3">
          <Activity className="text-primary-accent" />
          System Logs
        </h2>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-secondary cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> Auto-refresh
          </label>
          <button className="btn btn-secondary flex items-center gap-2" onClick={fetchAll}>
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="animate-fade-in-up"><CardContent>
          <div className="flex items-center gap-3">
            <Database size={20} className={health?.redis_reachable ? 'text-success' : 'text-danger'} />
            <div>
              <div className="text-xs text-tertiary uppercase tracking-wider">Job Queue</div>
              <div className="font-semibold text-primary">{health ? (health.redis_reachable ? `Connected (${health.queue_name})` : 'Unreachable') : '—'}</div>
            </div>
          </div>
        </CardContent></Card>
        <Card className="animate-fade-in-up" style={{ animationDelay: '0.05s' }}><CardContent>
          <div className="flex items-center gap-3">
            <Cpu size={20} className={health?.gpu?.cuda_available ? 'text-success' : 'text-secondary'} />
            <div>
              <div className="text-xs text-tertiary uppercase tracking-wider">GPU</div>
              <div className="font-semibold text-primary">
                {health ? (health.gpu.cuda_available ? health.gpu.devices.map((d) => d.name).join(', ') : 'Not detected (CPU-only / not yet configured)') : '—'}
              </div>
            </div>
          </div>
        </CardContent></Card>
        <Card className="animate-fade-in-up" style={{ animationDelay: '0.1s' }}><CardContent>
          <div className="text-xs text-tertiary uppercase tracking-wider mb-1">Registered Pipelines</div>
          <div className="flex flex-wrap gap-2">
            {(health?.registered_pipelines || []).map((p) => <Badge key={p} variant="info">{p}</Badge>)}
            {health && health.registered_pipelines.length === 0 && <span className="text-sm text-secondary italic">None registered</span>}
          </div>
        </CardContent></Card>
      </div>

      <Card>
        <CardContent>
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              <label className="text-sm text-secondary">Event type</label>
              <select className="input-field" style={{ maxWidth: '220px' }} value={filters.event_type} onChange={(e) => setFilters({ ...filters, event_type: e.target.value })}>
                <option value="">All</option>
                {eventTypes.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-sm text-secondary">Status</label>
              <select className="input-field" style={{ maxWidth: '180px' }} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                <option value="">All</option>
                {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm flex items-center gap-2">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      <Card className="flex-1">
        <CardHeader><CardTitle>Recent Activity</CardTitle></CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-12 flex justify-center"><Loader text="Loading events..." /></div>
          ) : events.length === 0 ? (
            <div className="p-8 text-center text-secondary italic">No processing events recorded yet.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left border-collapse">
                <thead className="text-secondary uppercase font-medium" style={{ backgroundColor: 'rgba(0,0,0,0.05)' }}>
                  <tr>
                    <th className="px-4 py-3 border-b border-border-color" />
                    <th className="px-4 py-3 border-b border-border-color">Time</th>
                    <th className="px-4 py-3 border-b border-border-color">Job</th>
                    <th className="px-4 py-3 border-b border-border-color">Event</th>
                    <th className="px-4 py-3 border-b border-border-color">Status</th>
                    <th className="px-4 py-3 border-b border-border-color">Message</th>
                    <th className="px-4 py-3 border-b border-border-color">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => {
                    const expanded = expandedId === e.id;
                    return (
                      <React.Fragment key={e.id}>
                        <tr
                          className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors cursor-pointer"
                          onClick={() => setExpandedId(expanded ? null : e.id)}
                        >
                          <td className="px-4 py-3">
                            <ChevronRight size={14} className="text-tertiary transition-transform" style={{ transform: expanded ? 'rotate(90deg)' : 'none' }} />
                          </td>
                          <td className="px-4 py-3 text-xs text-tertiary whitespace-nowrap">{e.created_at ? new Date(e.created_at).toLocaleString() : '—'}</td>
                          <td className="px-4 py-3 font-mono text-xs text-primary-accent">{(e.job_id || '').slice(0, 8)}</td>
                          <td className="px-4 py-3 font-medium text-primary">{e.event_type}</td>
                          <td className="px-4 py-3">
                            {e.status ? (
                              <Badge variant={statusVariant(e.status)} className="text-xs flex items-center gap-1 w-fit">
                                {e.status === 'COMPLETED' && <CheckCircle2 size={12} />} {e.status}
                              </Badge>
                            ) : <span className="text-tertiary">—</span>}
                          </td>
                          <td className="px-4 py-3 text-secondary">{e.message || '—'}</td>
                          <td className="px-4 py-3 text-xs text-tertiary">{e.duration_ms != null ? `${e.duration_ms}ms` : '—'}</td>
                        </tr>
                        {expanded && (
                          <tr className="border-b border-border-color border-opacity-50" style={{ backgroundColor: 'rgba(79,70,229,0.03)' }}>
                            <td colSpan={7} className="px-8 py-4">
                              <div className="flex flex-col gap-2 text-xs">
                                <div className="flex gap-6 flex-wrap text-secondary">
                                  <span><strong className="text-primary">Document:</strong> {e.document_id || '—'}</span>
                                  <span><strong className="text-primary">Pipeline run:</strong> {e.pipeline_run_id || '—'}</span>
                                  <span><strong className="text-primary">Page:</strong> {e.page_id || '—'}</span>
                                </div>
                                {e.metadata && Object.keys(e.metadata).length > 0 ? (
                                  <pre className="font-mono text-xs bg-bg-primary border border-border-color rounded-md p-3 overflow-x-auto">
                                    {JSON.stringify(e.metadata, null, 2)}
                                  </pre>
                                ) : (
                                  <span className="text-tertiary italic">No additional metadata recorded for this event.</span>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default LogsPage;
