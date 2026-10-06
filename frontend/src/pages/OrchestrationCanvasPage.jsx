/**
 * DOCINT — Orchestration execution canvas.
 * Visualizes the multi-pipeline comparison job as a live node graph:
 * Document -> each selected pipeline -> Consensus engine, using reactflow.
 * Below the canvas, the same field-by-field comparison table as before.
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, { Background, Handle, Position, MarkerType } from 'reactflow';
import 'reactflow/dist/style.css';
import {
  ArrowLeft, GitCompare, CheckCircle2, AlertTriangle, XCircle, Clock, Users,
  FileText, Cpu, Scale,
} from 'lucide-react';
import { processingService } from '../services/processing';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const StatusBadge = ({ status }) => {
  switch (status) {
    case 'COMPLETED': return <Badge variant="success">Completed</Badge>;
    case 'WARNING': return <Badge variant="warning">Needs Review</Badge>;
    case 'FAILED': return <Badge variant="danger">Failed</Badge>;
    case 'PROCESSING': return <Badge variant="warning">Processing</Badge>;
    case 'QUEUED': return <Badge variant="info">Queued</Badge>;
    default: return <Badge variant="default">{status}</Badge>;
  }
};

const agreementVariant = (agreement) => {
  switch (agreement) {
    case 'unanimous': return 'success';
    case 'majority': return 'info';
    case 'single_pipeline': return 'default';
    case 'no_data': return 'danger';
    default: return 'warning';
  }
};

const parseMaybeJSON = (data) => {
  if (typeof data !== 'string') return data;
  try { return JSON.parse(data); } catch (e) { return data; }
};

const nodeStatusColor = (status) => {
  switch (status) {
    case 'COMPLETED': return { bg: 'rgba(16,185,129,0.1)', border: '#10b981', text: '#10b981' };
    case 'RUNNING': case 'PROCESSING': return { bg: 'rgba(245,158,11,0.1)', border: '#f59e0b', text: '#f59e0b' };
    case 'FAILED': return { bg: 'rgba(239,68,68,0.1)', border: '#ef4444', text: '#ef4444' };
    default: return { bg: 'var(--bg-secondary)', border: 'var(--border-color)', text: 'var(--text-secondary)' };
  }
};

const SourceNode = ({ data }) => (
  <div className="flow-node" style={{ borderColor: 'var(--primary-accent)', background: 'var(--bg-secondary)' }}>
    <div className="flow-node-icon" style={{ background: 'var(--gradient-primary)', color: '#fff' }}><FileText size={16} /></div>
    <div className="flow-node-title">{data.label}</div>
    <Handle type="source" position={Position.Right} style={{ background: 'var(--primary-accent)' }} />
  </div>
);

const PipelineNode = ({ data }) => {
  const colors = nodeStatusColor(data.status);
  const running = data.status === 'RUNNING' || data.status === 'PROCESSING';
  return (
    <div className={`flow-node ${running ? 'flow-node-pulse' : ''}`} style={{ borderColor: colors.border, background: colors.bg }}>
      <Handle type="target" position={Position.Left} style={{ background: colors.border }} />
      <div className="flow-node-icon" style={{ color: colors.border }}><Cpu size={16} /></div>
      <div className="flow-node-title">{data.label}</div>
      <div className="flow-node-sub" style={{ color: colors.text }}>{data.status}</div>
      <Handle type="source" position={Position.Right} style={{ background: colors.border }} />
    </div>
  );
};

const ConsensusNode = ({ data }) => (
  <div className="flow-node" style={{ borderColor: 'var(--secondary-accent)', background: 'var(--bg-secondary)' }}>
    <Handle type="target" position={Position.Left} style={{ background: 'var(--secondary-accent)' }} />
    <div className="flow-node-icon" style={{ background: 'var(--secondary-accent)', color: '#fff' }}><Scale size={16} /></div>
    <div className="flow-node-title">Consensus</div>
    <div className="flow-node-sub" style={{ color: 'var(--secondary-accent)' }}>
      {data.rate !== null ? `${data.rate}% agree` : 'Waiting...'}
    </div>
  </div>
);

const nodeTypes = { source: SourceNode, pipeline: PipelineNode, consensus: ConsensusNode };

const OrchestrationCanvasPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const [jobDetails, setJobDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let pollTimeoutId = null;
    const fetchJob = async () => {
      try {
        const res = await processingService.getJob(id);
        if (cancelled) return;
        setJobDetails(res.data);
        const status = res.data.status;
        if (status === 'QUEUED' || status === 'PROCESSING') pollTimeoutId = setTimeout(fetchJob, 3000);
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to fetch comparison job');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchJob();
    return () => { cancelled = true; if (pollTimeoutId) clearTimeout(pollTimeoutId); };
  }, [id]);

  const metadata = useMemo(() => (jobDetails ? (parseMaybeJSON(jobDetails.ocr_metadata_json) || {}) : {}), [jobDetails]);
  const comparisons = useMemo(() => {
    if (!jobDetails) return [];
    return Array.isArray(jobDetails.confidence_json) ? jobDetails.confidence_json : (parseMaybeJSON(jobDetails.confidence_json) || []);
  }, [jobDetails]);
  const pipelineNames = metadata.pipelines || [];
  const pipelineStatuses = metadata.pipeline_statuses || {};
  const consensusRatePct = metadata.consensus_rate != null ? Math.round(metadata.consensus_rate * 100) : null;
  const isFinished = jobDetails?.status === 'COMPLETED' || jobDetails?.status === 'WARNING';

  const { nodes, edges } = useMemo(() => {
    const n = [];
    const e = [];
    const names = pipelineNames.length > 0 ? pipelineNames : ['pipeline-1', 'pipeline-2'];
    const spacingY = 90;
    const startY = 40;

    n.push({ id: 'source', type: 'source', position: { x: 0, y: (names.length - 1) * spacingY / 2 + startY }, data: { label: 'Document' }, draggable: false });

    names.forEach((name, idx) => {
      const rawStatus = jobDetails ? (pipelineStatuses[name] || (isFinished ? 'COMPLETED' : jobDetails.status === 'FAILED' ? 'FAILED' : 'RUNNING')) : 'PENDING';
      n.push({
        id: `pipeline-${name}`,
        type: 'pipeline',
        position: { x: 280, y: startY + idx * spacingY },
        data: { label: name, status: rawStatus },
        draggable: false,
      });
      e.push({
        id: `e-source-${name}`,
        source: 'source',
        target: `pipeline-${name}`,
        animated: rawStatus === 'RUNNING' || rawStatus === 'PROCESSING',
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: nodeStatusColor(rawStatus).border },
      });
      e.push({
        id: `e-${name}-consensus`,
        source: `pipeline-${name}`,
        target: 'consensus',
        animated: rawStatus === 'RUNNING' || rawStatus === 'PROCESSING',
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: nodeStatusColor(rawStatus).border },
      });
    });

    n.push({ id: 'consensus', type: 'consensus', position: { x: 560, y: (names.length - 1) * spacingY / 2 + startY }, data: { rate: consensusRatePct }, draggable: false });

    return { nodes: n, edges: e };
  }, [pipelineNames, pipelineStatuses, jobDetails, isFinished, consensusRatePct]);

  const onInit = useCallback((instance) => instance.fitView({ padding: 0.3 }), []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading comparison job..." /></div>;
  if (error) return <div className="text-danger p-4 glass-panel">{error}</div>;
  if (!jobDetails) return <div className="text-secondary p-4">Job not found</div>;

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto p-4 w-full h-full">
      <div className="flex items-center gap-4 mb-2">
        <button className="btn btn-secondary" style={{ padding: '0.4rem' }} onClick={() => navigate('/orchestration')}>
          <ArrowLeft size={20} />
        </button>
        <h2 className="text-2xl font-bold text-primary font-heading flex items-center gap-3">
          <GitCompare className="text-primary-accent" />
          Orchestration Execution
        </h2>
        <StatusBadge status={jobDetails.status} />
      </div>

      <Card className="animate-fade-in-up" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ height: '320px', width: '100%' }}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onInit={onInit} fitView proOptions={{ hideAttribution: true }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}>
            <Background gap={16} color="var(--border-color)" />
          </ReactFlow>
        </div>
      </Card>

      {jobDetails.status === 'FAILED' && (
        <Card><CardContent>
          <div className="h-full flex flex-col items-center justify-center text-danger p-12">
            <XCircle size={48} className="mb-4 opacity-50" />
            <h3 className="text-xl font-bold mb-2">Comparison Failed</h3>
            <p className="text-center opacity-80">{jobDetails.error_message || 'An unknown error occurred while running the pipelines.'}</p>
          </div>
        </CardContent></Card>
      )}

      {isFinished && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <Card className="animate-fade-in-up"><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1">Pipelines Compared</div>
              <div className="text-2xl font-bold text-primary flex items-center gap-2"><Users size={20} className="text-primary-accent" />{pipelineNames.length}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up" style={{ animationDelay: '0.05s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1">Total Fields</div>
              <div className="text-2xl font-bold text-primary">{metadata.total_fields ?? comparisons.length}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up" style={{ animationDelay: '0.1s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1">Consensus Rate</div>
              <div className="text-2xl font-bold text-primary-accent">{consensusRatePct !== null ? `${consensusRatePct}%` : '—'}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up" style={{ animationDelay: '0.15s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1">Flagged for Review</div>
              <div className={`text-2xl font-bold flex items-center gap-2 ${(metadata.flagged_fields || []).length > 0 ? 'text-warning' : 'text-primary'}`}>
                {(metadata.flagged_fields || []).length > 0 && <AlertTriangle size={18} />}
                {(metadata.flagged_fields || []).length}
              </div>
            </CardContent></Card>
          </div>

          <Card className="animate-fade-in-up" style={{ animationDelay: '0.2s' }}>
            <CardHeader><CardTitle>Field-Level Comparison</CardTitle></CardHeader>
            <CardContent className="p-0">
              {comparisons.length === 0 ? (
                <div className="p-8 text-center text-secondary italic">No comparable fields were produced by the selected pipelines.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left border-collapse">
                    <thead className="text-secondary uppercase font-medium" style={{ backgroundColor: 'rgba(0,0,0,0.05)' }}>
                      <tr>
                        <th className="px-4 py-3 border-b border-border-color">Field</th>
                        {pipelineNames.map((name) => <th key={name} className="px-4 py-3 border-b border-border-color">{name}</th>)}
                        <th className="px-4 py-3 border-b border-border-color">Consensus Value</th>
                        <th className="px-4 py-3 border-b border-border-color">Agreement</th>
                        <th className="px-4 py-3 border-b border-border-color">Review</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisons.map((c, idx) => {
                        const perPipeline = c.per_pipeline || {};
                        return (
                          <tr key={c.field || idx} className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors" style={c.needs_review ? { backgroundColor: 'rgba(245, 158, 11, 0.06)' } : undefined}>
                            <td className="px-4 py-3 font-semibold text-primary whitespace-nowrap">{(c.field || '').replace(/_/g, ' ')}</td>
                            {pipelineNames.map((name) => {
                              const v = perPipeline[name];
                              if (!v) return <td key={name} className="px-4 py-3 text-tertiary italic">—</td>;
                              return (
                                <td key={name} className="px-4 py-3">
                                  <div className="text-primary">{v.value !== null && v.value !== undefined ? String(v.value) : <span className="italic text-tertiary">not found</span>}</div>
                                  {v.confidence != null && <div className="text-xs text-tertiary mt-0.5">{(v.confidence * 100).toFixed(0)}% conf.</div>}
                                </td>
                              );
                            })}
                            <td className="px-4 py-3 font-medium text-primary-accent">
                              {c.consensus_value !== null && c.consensus_value !== undefined ? String(c.consensus_value) : <span className="italic text-tertiary">not found</span>}
                            </td>
                            <td className="px-4 py-3">
                              <Badge variant={agreementVariant(c.agreement)} className="text-xs whitespace-nowrap">{(c.agreement || c.resolution_rule || '—').replace(/_/g, ' ')}</Badge>
                            </td>
                            <td className="px-4 py-3">
                              {c.needs_review ? (
                                <span className="flex items-center gap-1 text-warning text-xs font-medium"><AlertTriangle size={14} /> Flagged</span>
                              ) : (
                                <span className="flex items-center gap-1 text-success text-xs font-medium"><CheckCircle2 size={14} /> OK</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {!isFinished && jobDetails.status !== 'FAILED' && (
        <Card><CardContent>
          <div className="h-full flex flex-col items-center justify-center text-warning p-8">
            <Clock size={32} className="mb-3 opacity-50 animate-spin" style={{ animationDuration: '3s' }} />
            <p className="text-center text-secondary">Each selected pipeline is processing the document — the canvas above updates automatically.</p>
          </div>
        </CardContent></Card>
      )}
    </div>
  );
};

export default OrchestrationCanvasPage;
