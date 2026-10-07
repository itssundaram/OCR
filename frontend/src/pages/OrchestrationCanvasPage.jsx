import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, { Background, Handle, Position, MarkerType } from 'reactflow';
import 'reactflow/dist/style.css';
import {
  ArrowLeft, GitCompare, CheckCircle2, AlertTriangle, XCircle, Clock, Users,
  FileText, Cpu, Scale, Loader as LoaderIcon, Image as ImageIcon, Type, LayoutTemplate, BrainCircuit, Activity
} from 'lucide-react';
import { processingService } from '../services/processing';
import { evidenceService } from '../services/evidence';
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
    case 'COMPLETED': case 'PARTIAL': return { bg: 'rgba(16,185,129,0.1)', border: '#10b981', text: '#10b981' };
    case 'RUNNING': case 'PROCESSING': return { bg: 'rgba(245,158,11,0.1)', border: '#f59e0b', text: '#f59e0b' };
    case 'FAILED': return { bg: 'rgba(239,68,68,0.1)', border: '#ef4444', text: '#ef4444' };
    case 'LOW_CONFIDENCE': return { bg: 'rgba(239,68,68,0.1)', border: '#ef4444', text: '#ef4444' };
    case 'MEDIUM_CONFIDENCE': return { bg: 'rgba(59,130,246,0.1)', border: '#3b82f6', text: '#3b82f6' };
    case 'HIGH_CONFIDENCE': return { bg: 'rgba(16,185,129,0.1)', border: '#10b981', text: '#10b981' };
    default: return { bg: 'var(--bg-secondary)', border: 'var(--border-color)', text: 'var(--text-secondary)' };
  }
};

const SourceNode = ({ data }) => (
  <div className="flow-node" style={{ borderColor: 'var(--primary-accent)', background: 'var(--bg-secondary)', minWidth: 150 }}>
    <div className="flow-node-icon" style={{ background: 'var(--gradient-primary)', color: '#fff' }}><FileText size={16} /></div>
    <div className="flow-node-title text-center text-primary font-bold w-full">{data.label}</div>
    <Handle type="source" position={Position.Right} style={{ background: 'var(--primary-accent)' }} />
  </div>
);

const PipelineNode = ({ data }) => {
  const colors = nodeStatusColor(data.status);
  const running = data.status === 'RUNNING' || data.status === 'PROCESSING';
  return (
    <div className={`flow-node ${running ? 'flow-node-pulse shadow-glow' : 'shadow-lg'}`} style={{ borderColor: colors.border, background: colors.bg, minWidth: 180, padding: '14px', borderRadius: '12px' }}>
      <Handle type="target" position={Position.Left} style={{ background: colors.border }} />
      <div className="flex flex-col items-center gap-2 w-full justify-center">
        <div className="flex items-center gap-2">
          <div className={`flow-node-icon relative ${running ? 'animate-bounce' : ''}`} style={{ color: colors.border, background: 'transparent' }}><Cpu size={20} /></div>
          <div className="flow-node-title text-primary font-black capitalize text-lg">{data.label}</div>
        </div>
        
        {data.overallConf !== undefined && data.overallConf !== null && (
          <div className="flex items-center gap-2 text-[0.65rem] font-bold tracking-wider mt-1 bg-white bg-opacity-50 px-2 py-0.5 rounded-full border" style={{ borderColor: colors.border }}>
            <span className="text-secondary uppercase">Confidence</span>
            <span style={{ color: colors.text }}>{(data.overallConf * 100).toFixed(1)}%</span>
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: colors.border }} />
    </div>
  );
};

const getStepIcon = (label) => {
  const l = label.toLowerCase();
  if (l.includes('preprocess')) return <ImageIcon size={14} />;
  if (l.includes('layout')) return <LayoutTemplate size={14} />;
  if (l.includes('ocr')) return <Type size={14} />;
  if (l.includes('llm') || l.includes('fallback')) return <BrainCircuit size={14} />;
  return <Activity size={14} />;
};

const StepNode = ({ data }) => {
  const colors = nodeStatusColor(data.status);
  const running = data.isLastRunning;

  let durationStr = null;
  if (data.stepData && data.stepData.started_at && data.stepData.completed_at) {
    const s = new Date(data.stepData.started_at);
    const c = new Date(data.stepData.completed_at);
    const diff = (c - s) / 1000;
    if (diff >= 0) durationStr = `${diff.toFixed(2)}s`;
  } else if (running) {
    durationStr = 'running...';
  }

  return (
    <div className={`flow-node ${running ? 'flow-node-pulse shadow-glow border-dashed' : 'shadow-sm'}`} style={{ borderColor: colors.border, background: colors.bg, minWidth: 180, padding: '10px 14px', borderRadius: '12px' }}>
      <Handle type="target" position={Position.Left} style={{ background: colors.border }} />
      <div className="flex flex-col gap-2 w-full">
        <div className="flex items-center gap-2 justify-between w-full">
          <div className="flex items-center gap-2">
            <div className="text-primary opacity-70">
              {getStepIcon(data.label)}
            </div>
            <div className="flow-node-title text-primary font-bold text-[0.75rem] capitalize truncate max-w-[100px]" title={data.label.replace(/_/g, ' ')}>
              {data.label.replace(/_/g, ' ')}
            </div>
          </div>
          {running ? (
            <LoaderIcon size={14} className="animate-spin text-tertiary flex-shrink-0" />
          ) : (
            <CheckCircle2 size={14} className="text-success flex-shrink-0" />
          )}
        </div>
        
        {durationStr && (
          <div className="flex justify-between items-center text-[0.65rem] font-mono mt-1 pt-1 border-t border-black border-opacity-5">
            <span className="text-tertiary uppercase tracking-wider font-bold text-[0.55rem]">Runtime</span>
            <span className="font-bold" style={{ color: colors.text }}>{durationStr}</span>
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: colors.border }} />
    </div>
  );
};

const ConsensusNode = ({ data }) => (
  <div className="flow-node shadow-lg" style={{ borderColor: 'var(--secondary-accent)', background: 'var(--bg-secondary)', minWidth: 150 }}>
    <Handle type="target" position={Position.Left} style={{ background: 'var(--secondary-accent)' }} />
    <div className="flex flex-col items-center justify-center w-full gap-2">
      <div className="p-2 rounded-full" style={{ background: 'var(--secondary-accent)', color: '#fff' }}><Scale size={24} /></div>
      <div className="flow-node-title text-primary font-bold mt-1">Consensus</div>
      <div className="flow-node-sub font-semibold" style={{ color: 'var(--secondary-accent)' }}>
        {data.rate !== null ? `${data.rate}% agree` : 'Waiting...'}
      </div>
    </div>
  </div>
);

const nodeTypes = { source: SourceNode, pipeline: PipelineNode, step: StepNode, consensus: ConsensusNode };

const OrchestrationCanvasPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const [jobDetails, setJobDetails] = useState(null);
  const [pipelineSteps, setPipelineSteps] = useState({});
  const [activeResultPipeline, setActiveResultPipeline] = useState(null);
  const [activeResultTab, setActiveResultTab] = useState('data');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let pollTimeoutId = null;
    const fetchJob = async () => {
      try {
        const [resJob, resSteps] = await Promise.all([
          processingService.getJob(id),
          processingService.getPipelineSteps(id)
        ]);
        if (cancelled) return;
        setJobDetails(resJob.data);
        if (resSteps.data.pipelines) {
          setPipelineSteps(resSteps.data.pipelines);
        }
        const status = resJob.data.status;
        if (status === 'QUEUED' || status === 'PROCESSING') {
          pollTimeoutId = setTimeout(fetchJob, 1500);
        }
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
  const pipelineNames = metadata.pipelines || Object.keys(pipelineSteps || {});
  
  useEffect(() => {
    if (pipelineNames.length > 0 && !activeResultPipeline) {
      setActiveResultPipeline(pipelineNames[0]);
    }
  }, [pipelineNames, activeResultPipeline]);

  const pipelineStatuses = metadata.pipeline_statuses || {};
  const consensusRatePct = metadata.consensus_rate != null ? Math.round(metadata.consensus_rate * 100) : null;
  const isFinished = jobDetails?.status === 'COMPLETED' || jobDetails?.status === 'WARNING';

  const { nodes, edges } = useMemo(() => {
    const n = [];
    const e = [];
    // If no names are found yet (e.g., initial load before steps are fetched), default to empty array
    const names = pipelineNames.length > 0 ? pipelineNames : [];
    const spacingY = 130;
    const startY = 40;

    n.push({ id: 'source', type: 'source', position: { x: 0, y: (names.length - 1) * spacingY / 2 + startY + 20 }, data: { label: 'Document' }, draggable: false });

    names.forEach((name, idx) => {
      const rawStatus = jobDetails ? (pipelineStatuses[name] || (isFinished ? 'COMPLETED' : jobDetails.status === 'FAILED' ? 'FAILED' : 'RUNNING')) : 'PENDING';
      const rawSteps = pipelineSteps[name]?.steps || [];
      const pipelineData = pipelineSteps[name] || {};
      const overallConf = pipelineData.overall_confidence;

      let pipelineColorStatus = rawStatus;
      if ((rawStatus === 'COMPLETED' || rawStatus === 'PARTIAL') && overallConf !== undefined && overallConf !== null) {
          if (overallConf < 0.85) pipelineColorStatus = 'LOW_CONFIDENCE';
          else if (overallConf < 0.90) pipelineColorStatus = 'MEDIUM_CONFIDENCE';
          else pipelineColorStatus = 'HIGH_CONFIDENCE';
      }

      const consolidatedSteps = [];
      rawSteps.forEach(step => {
        const baseName = step.name.replace(/_STARTED|_DONE/g, '');
        if (!consolidatedSteps.find(s => s.name === baseName)) {
          consolidatedSteps.push({ ...step, name: baseName });
        }
      });

      const baseY = startY + idx * spacingY;

      n.push({
        id: `pipeline-${name}`,
        type: 'pipeline',
        position: { x: 250, y: baseY },
        data: { label: name, status: pipelineColorStatus, overallConf: overallConf },
        draggable: false,
      });

      e.push({
        id: `e-source-${name}`,
        source: 'source',
        target: `pipeline-${name}`,
        type: 'smoothstep',
        animated: rawStatus === 'RUNNING' || rawStatus === 'PROCESSING',
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: nodeStatusColor(pipelineColorStatus).border, strokeWidth: 2 },
      });

      let lastNodeId = `pipeline-${name}`;

      consolidatedSteps.forEach((step, stepIdx) => {
        let stepStatus = step.status;
        if (stepStatus !== 'FAILED' && (rawStatus === 'COMPLETED' || rawStatus === 'PARTIAL')) {
          stepStatus = pipelineColorStatus;
        }

        const stepId = `pipeline-${name}-step-${stepIdx}`;
        n.push({
          id: stepId,
          type: 'step',
          position: { x: 250 + (stepIdx + 1) * 220, y: baseY + 4 },
          data: { label: step.name, status: stepStatus, isLastRunning: (rawStatus === 'RUNNING' || rawStatus === 'PROCESSING') && stepIdx === consolidatedSteps.length - 1, stepData: step },
          draggable: false,
        });

        e.push({
          id: `e-${lastNodeId}-${stepId}`,
          source: lastNodeId,
          target: stepId,
          type: 'smoothstep',
          animated: false,
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: nodeStatusColor(stepStatus).border, strokeWidth: 2 },
        });

        lastNodeId = stepId;
      });

      e.push({
        id: `e-${lastNodeId}-consensus`,
        source: lastNodeId,
        target: 'consensus',
        type: 'smoothstep',
        animated: rawStatus === 'RUNNING' || rawStatus === 'PROCESSING',
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: nodeStatusColor(pipelineColorStatus).border, strokeWidth: 2 },
      });
    });

    const maxSteps = names.reduce((max, name) => {
      const rawSteps = pipelineSteps[name]?.steps || [];
      const consolidatedSteps = [];
      rawSteps.forEach(step => {
        const baseName = step.name.replace(/_STARTED|_DONE/g, '');
        if (!consolidatedSteps.find(s => s.name === baseName)) {
          consolidatedSteps.push({ ...step, name: baseName });
        }
      });
      return Math.max(max, consolidatedSteps.length);
    }, 0);
    const consensusX = 250 + (maxSteps + 1) * 220 + 50;

    n.push({ id: 'consensus', type: 'consensus', position: { x: consensusX, y: (names.length - 1) * spacingY / 2 + startY + 10 }, data: { rate: consensusRatePct }, draggable: false });

    return { nodes: n, edges: e };
  }, [metadata, jobDetails, isFinished, consensusRatePct, pipelineSteps]);

  const onInit = useCallback((instance) => instance.fitView({ padding: 0.2 }), []);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading comparison job..." /></div>;
  if (error) return <div className="text-danger p-4 glass-panel">{error}</div>;
  if (!jobDetails) return <div className="text-secondary p-4">Job not found</div>;

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto p-4 w-full min-h-full">
      <div className="flex items-center gap-4 mb-2 flex-shrink-0">
        <button className="btn btn-secondary" style={{ padding: '0.4rem' }} onClick={() => navigate('/orchestration')}>
          <ArrowLeft size={20} />
        </button>
        <h2 className="text-3xl font-bold text-primary font-heading flex items-center gap-3">
          <GitCompare className="text-primary-accent" />
          Orchestration Execution
        </h2>
        <StatusBadge status={jobDetails.status} />
      </div>

      <Card className="animate-fade-in-up border-primary-accent border-opacity-20 shadow-lg flex-shrink-0" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ height: '360px', width: '100%' }}>
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
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 flex-shrink-0">
            <Card className="animate-fade-in-up border shadow-sm"><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1 font-semibold">Pipelines Compared</div>
              <div className="text-2xl font-bold text-primary flex items-center gap-2"><Users size={20} className="text-primary-accent" />{pipelineNames.length}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up border shadow-sm" style={{ animationDelay: '0.05s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1 font-semibold">Total Fields</div>
              <div className="text-2xl font-bold text-primary">{metadata.total_fields ?? comparisons.length}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up border shadow-sm" style={{ animationDelay: '0.1s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1 font-semibold">Consensus Rate</div>
              <div className="text-2xl font-bold text-primary-accent">{consensusRatePct !== null ? `${consensusRatePct}%` : '—'}</div>
            </CardContent></Card>
            <Card className="animate-fade-in-up border shadow-sm" style={{ animationDelay: '0.15s' }}><CardContent>
              <div className="text-xs text-tertiary uppercase tracking-wider mb-1 font-semibold">Flagged for Review</div>
              <div className={`text-2xl font-bold flex items-center gap-2 ${(metadata.flagged_fields || []).length > 0 ? 'text-warning' : 'text-primary'}`}>
                {(metadata.flagged_fields || []).length > 0 && <AlertTriangle size={18} />}
                {(metadata.flagged_fields || []).length}
              </div>
            </CardContent></Card>
          </div>

          <Card className="animate-fade-in-up shadow-lg border border-border-color" style={{ animationDelay: '0.2s' }}>
            <CardHeader className="bg-bg-secondary border-b border-border-color"><CardTitle>Field-Level Comparison</CardTitle></CardHeader>
            <CardContent className="p-0">
              {comparisons.length === 0 ? (
                <div className="p-8 text-center text-secondary italic">No comparable fields were produced by the selected pipelines.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left border-collapse table-auto">
                    <thead className="text-tertiary uppercase tracking-wider font-semibold text-xs" style={{ backgroundColor: 'rgba(0,0,0,0.02)' }}>
                      <tr>
                        <th className="px-5 py-4 border-b border-border-color min-w-[150px]">Field</th>
                        {pipelineNames.map((name) => <th key={name} className="px-5 py-4 border-b border-border-color capitalize min-w-[200px]">{name}</th>)}
                        <th className="px-5 py-4 border-b border-border-color min-w-[150px]">Consensus Value</th>
                        <th className="px-5 py-4 border-b border-border-color">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisons.map((c, idx) => {
                        const perPipeline = c.per_pipeline || {};
                        const fieldName = (c.field || '').replace(/_/g, ' ');
                        return (
                          <tr key={c.field || idx} className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors" style={c.needs_review ? { backgroundColor: 'rgba(245, 158, 11, 0.03)' } : undefined}>
                            <td className="px-5 py-4 align-top">
                              <div className="font-bold text-primary mb-2 capitalize">{fieldName}</div>
                              <div className="bg-white rounded p-1 inline-block border border-border-color">
                                <img 
                                  src={evidenceService.imageUrl(jobDetails.job_id, c.field)} 
                                  alt={`Source crop`}
                                  className="max-h-16 object-contain"
                                  onError={(e) => { e.target.style.display = 'none'; }}
                                />
                              </div>
                            </td>
                            {pipelineNames.map((name) => {
                              const v = perPipeline[name];
                              if (!v) return <td key={name} className="px-5 py-4 align-top text-tertiary italic">—</td>;
                              
                              let colorClass = 'text-primary';
                              let bgClass = 'bg-transparent';
                              let borderClass = 'border-transparent';
                              
                              if (v.confidence > 0.90) {
                                colorClass = 'text-green-600';
                                bgClass = 'bg-green-500 bg-opacity-10';
                                borderClass = 'border-green-500 border-opacity-30';
                              } else if (v.confidence >= 0.85) {
                                colorClass = 'text-blue-600';
                                bgClass = 'bg-blue-500 bg-opacity-10';
                                borderClass = 'border-blue-500 border-opacity-30';
                              } else if (v.confidence !== undefined && v.confidence !== null) {
                                colorClass = 'text-red-600';
                                bgClass = 'bg-red-500 bg-opacity-10';
                                borderClass = 'border-red-500 border-opacity-30';
                              }

                              return (
                                <td key={name} className="px-5 py-4 align-top">
                                  <div className={`p-2 rounded border ${bgClass} ${borderClass}`}>
                                    <div className="font-semibold text-[0.95rem] mb-1 text-primary">
                                      {v.value !== null && v.value !== undefined && v.value !== '' ? String(v.value) : <span className="italic opacity-50">empty</span>}
                                    </div>
                                    {v.confidence != null && <div className={`text-xs font-bold ${colorClass}`}>{(v.confidence * 100).toFixed(1)}% conf.</div>}
                                  </div>
                                </td>
                              );
                            })}
                            <td className="px-5 py-4 align-top">
                              <div className="font-bold text-lg text-primary-accent p-2">
                                {c.consensus_value !== null && c.consensus_value !== undefined && c.consensus_value !== '' ? String(c.consensus_value) : <span className="italic opacity-50">empty</span>}
                              </div>
                            </td>
                            <td className="px-5 py-4 align-top">
                              <div className="flex flex-col gap-2">
                                <Badge variant={agreementVariant(c.resolution_rule)} className="text-[0.65rem] whitespace-nowrap self-start">{(c.resolution_rule || '—').replace(/_/g, ' ')}</Badge>
                                {c.needs_review ? (
                                  <span className="flex items-center gap-1 text-warning text-xs font-bold uppercase tracking-wider"><AlertTriangle size={14} /> Flagged</span>
                                ) : (
                                  <span className="flex items-center gap-1 text-success text-xs font-bold uppercase tracking-wider"><CheckCircle2 size={14} /> Agreed</span>
                                )}
                              </div>
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

          <Card className="animate-fade-in-up shadow-lg border border-border-color" style={{ animationDelay: '0.3s' }}>
            <CardHeader className="bg-bg-secondary border-b border-border-color py-3 px-4 flex flex-row items-center justify-between">
              <CardTitle>Pipeline Raw Outputs</CardTitle>
              <div className="flex gap-2">
                {pipelineNames.map(name => (
                  <button
                    key={name}
                    onClick={() => setActiveResultPipeline(name)}
                    className={`px-3 py-1 text-sm font-semibold rounded-md border capitalize transition-colors ${activeResultPipeline === name ? 'bg-primary-accent text-white border-primary-accent' : 'bg-transparent text-secondary border-border-color hover:bg-bg-secondary'}`}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </CardHeader>
            {activeResultPipeline && metadata.raw_results?.[activeResultPipeline] ? (
              <CardContent className="p-0 flex flex-col h-[600px]">
                <div className="flex border-b border-border-color px-2 pt-2 bg-bg-secondary gap-2 flex-shrink-0">
                  {['data', 'tables', 'ocr', 'json'].map(tab => (
                    <button
                      key={tab}
                      onClick={() => setActiveResultTab(tab)}
                      className={`px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${activeResultTab === tab ? 'border-primary-accent text-primary-accent' : 'border-transparent text-secondary hover:text-primary'}`}
                    >
                      {tab === 'data' ? 'Extracted Fields' : tab === 'tables' ? 'Extracted Tables' : tab === 'ocr' ? 'Raw OCR Text' : 'JSON Payload'}
                    </button>
                  ))}
                </div>
                <div className="flex-1 overflow-auto p-4 bg-bg-primary">
                  {activeResultTab === 'data' && (
                    <div className="flex flex-col gap-4">
                      {Object.keys(metadata.raw_results[activeResultPipeline].fields || {}).length === 0 ? (
                        <div className="text-secondary italic p-8 text-center">No fields extracted.</div>
                      ) : (
                        Object.entries(metadata.raw_results[activeResultPipeline].fields || {}).map(([key, data]) => (
                          <div key={key} className="p-4 rounded-lg border shadow-sm flex justify-between items-center bg-white hover:bg-bg-secondary transition-colors">
                            <div>
                              <div className="text-xs text-tertiary uppercase tracking-wider font-semibold mb-1">{key.replace(/_/g, ' ')}</div>
                              <div className="font-bold text-lg text-primary">{data.value !== null && data.value !== '' ? String(data.value) : <span className="italic opacity-50">empty</span>}</div>
                            </div>
                            <div className={`text-xs font-bold px-3 py-1 rounded-full ${data.confidence > 0.90 ? 'bg-green-100 text-green-700' : data.confidence >= 0.85 ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700'}`}>
                              {(data.confidence * 100).toFixed(1)}% conf
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {activeResultTab === 'tables' && (
                    <div className="flex flex-col gap-6">
                      {(metadata.raw_results[activeResultPipeline].tables || []).length === 0 ? (
                        <div className="text-secondary italic p-8 text-center">No tables extracted.</div>
                      ) : (
                        (metadata.raw_results[activeResultPipeline].tables || []).map((t, idx) => (
                          <div key={idx} className="border border-border-color rounded-lg overflow-hidden shadow-sm">
                            <div className="bg-bg-secondary p-3 border-b border-border-color font-bold text-primary flex justify-between items-center">
                              <span>Table {t.table_index + 1}</span>
                              <span className="text-xs font-semibold px-2 py-1 bg-white rounded text-secondary border border-border-color">Method: {t.extraction_method || 'unknown'}</span>
                            </div>
                            <div className="overflow-x-auto p-4 bg-white">
                              <table className="w-full text-sm border-collapse">
                                {t.headers && t.headers.length > 0 && (
                                  <thead><tr className="border-b-2 border-border-color">
                                    {t.headers.map((h, i) => <th key={i} className="p-2 text-left font-bold text-primary whitespace-nowrap bg-bg-secondary">{h}</th>)}
                                  </tr></thead>
                                )}
                                <tbody>
                                  {t.rows.map((row, rIdx) => (
                                    <tr key={rIdx} className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors">
                                      {row.map((cell, cIdx) => <td key={cIdx} className="p-3 align-top">{cell}</td>)}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {activeResultTab === 'ocr' && (
                    <div className="h-full relative">
                      <pre className="whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-secondary bg-[#1e1e1e] text-[#d4d4d4] p-6 rounded-lg h-full overflow-y-auto border border-border-color shadow-inner">
                        {(metadata.raw_results[activeResultPipeline].raw_text_per_page || []).join('\n\n---\n\n') || "No raw text available."}
                      </pre>
                    </div>
                  )}
                  {activeResultTab === 'json' && (
                    <div className="h-full relative">
                      <pre className="whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-secondary bg-[#1e1e1e] text-[#d4d4d4] p-6 rounded-lg h-full overflow-y-auto border border-border-color shadow-inner">
                        {JSON.stringify(metadata.raw_results[activeResultPipeline], null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              </CardContent>
            ) : (
              <CardContent className="p-8 text-center text-secondary italic">
                Raw output data is not available for this pipeline run.
              </CardContent>
            )}
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
