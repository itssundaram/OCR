/**
 * DOCINT — OCR page (single pipeline), consolidated.
 * One page: pick department/template/pipeline, upload, then watch the same
 * job's result render in place — no navigation to a separate document page.
 * Reads ?department=&template= query params so a Generate URL link can
 * pre-fill the form.
 */
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  UploadCloud, File, AlertCircle, Play, Cpu, ArrowLeft, XCircle, Clock,
  Eye, Layers,
} from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import 'react-json-view-lite/dist/index.css';
import { templatesService } from '../services/templates';
import { departmentsService } from '../services/departments';
import { extractService } from '../services/extract';
import { pipelinesService } from '../services/pipelines';
import { processingService } from '../services/processing';
import { evidenceService } from '../services/evidence';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const parseJSONRecursively = (data) => {
  let parsed = data;
  while (typeof parsed === 'string') {
    try {
      const next = JSON.parse(parsed);
      if (typeof next === 'string' && next === parsed) break;
      parsed = next;
    } catch (e) { break; }
  }
  return parsed;
};

const fieldColors = (data) => {
  if (!data.found) return { bg: 'rgba(239, 68, 68, 0.05)', border: 'rgba(239, 68, 68, 0.3)' };
  if (data.confidence === undefined) return { bg: 'var(--bg-primary)', border: 'var(--border-color)' };
  if (data.confidence > 0.95) return { bg: 'rgba(16, 185, 129, 0.1)', border: 'rgba(16, 185, 129, 0.5)' };
  if (data.confidence >= 0.90) return { bg: 'rgba(59, 130, 246, 0.1)', border: 'rgba(59, 130, 246, 0.5)' };
  return { bg: 'rgba(239, 68, 68, 0.1)', border: 'rgba(239, 68, 68, 0.5)' };
};

const OcrPage = () => {
  const [searchParams] = useSearchParams();

  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [pipelines, setPipelines] = useState([]);
  const [loadingInitial, setLoadingInitial] = useState(true);

  const [selectedDept, setSelectedDept] = useState('');
  const [selectedTemplateCode, setSelectedTemplateCode] = useState('');
  const [selectedPipeline, setSelectedPipeline] = useState('');
  const [file, setFile] = useState(null);

  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const [activeJob, setActiveJob] = useState(null);
  const [activeTab, setActiveTab] = useState('data');

  const fileInputRef = useRef(null);
  const prefillApplied = useRef(false);

  useEffect(() => {
    (async () => {
      try {
        const [deptRes, pipelinesRes] = await Promise.all([
          departmentsService.list(),
          pipelinesService.list(),
        ]);
        const activeDepts = deptRes.data.filter((d) => d.is_active);
        setDepartments(activeDepts);

        const deptFromQuery = searchParams.get('department');
        const initialDept = (deptFromQuery && activeDepts.some((d) => d.slug === deptFromQuery))
          ? deptFromQuery
          : (activeDepts[0]?.slug || '');
        setSelectedDept(initialDept);

        const availablePipelines = pipelinesRes.data.pipelines || [];
        setPipelines(availablePipelines);
        if (availablePipelines.length > 0) setSelectedPipeline(availablePipelines[0].name);
      } catch (err) {
        setError('Failed to load form data. ' + err.message);
      } finally {
        setLoadingInitial(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedDept) { setTemplates([]); setSelectedTemplateCode(''); return; }
    templatesService.listTemplates(selectedDept).then((res) => {
      const activeTemplates = res.data.filter((t) => t.is_active);
      setTemplates(activeTemplates);
      const templateFromQuery = searchParams.get('template');
      if (!prefillApplied.current && templateFromQuery && activeTemplates.some((t) => t.code === templateFromQuery)) {
        setSelectedTemplateCode(templateFromQuery);
      } else {
        setSelectedTemplateCode(activeTemplates.length > 0 ? activeTemplates[0].code : '');
      }
      prefillApplied.current = true;
    }).catch((err) => console.error(err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDept]);

  // Poll the active job until it reaches a terminal state.
  useEffect(() => {
    if (!activeJob?.job_id) return undefined;
    if (activeJob.status !== 'QUEUED' && activeJob.status !== 'PROCESSING') return undefined;
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const res = await processingService.getJob(activeJob.job_id);
        if (!cancelled) setActiveJob(res.data);
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to refresh job status');
      }
    }, 3000);
    return () => { cancelled = true; clearTimeout(t); };
  }, [activeJob]);

  const handleDrag = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  };
  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation(); setDragActive(false);
    if (e.dataTransfer.files?.[0]) setFile(e.dataTransfer.files[0]);
  };
  const handleFileChange = (e) => { if (e.target.files?.[0]) setFile(e.target.files[0]); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || !selectedTemplateCode || !selectedPipeline) {
      setError('Select a department, template, pipeline, and a file.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const res = await extractService.uploadWithPipeline(selectedDept, selectedTemplateCode, selectedPipeline, file);
      const job = await processingService.getJob(res.data.job_id);
      setActiveJob(job.data);
    } catch (err) {
      setError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const reset = () => { setActiveJob(null); setFile(null); setError(null); };

  if (loadingInitial) {
    return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;
  }

  if (activeJob) {
    const status = activeJob.status;
    const isFinished = status === 'COMPLETED' || status === 'WARNING';
    const parsedResult = activeJob.extracted_json ? parseJSONRecursively(activeJob.extracted_json) : null;
    const ocrMetadata = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : null;

    return (
      <div className="flex flex-col gap-6 max-w-5xl mx-auto p-4 w-full h-full">
        <div className="flex items-center gap-4 mb-2">
          <button className="btn btn-secondary" style={{ padding: '0.4rem' }} onClick={reset}>
            <ArrowLeft size={20} />
          </button>
          <h2 className="text-2xl font-bold text-primary font-heading">Extraction Result</h2>
          <Badge variant={status === 'COMPLETED' ? 'success' : status === 'WARNING' ? 'warning' : status === 'FAILED' ? 'danger' : 'info'}>
            {status}
          </Badge>
        </div>

        {isFinished && (
          <Card className="animate-fade-in-up">
            <CardHeader className="flex justify-between items-center border-b border-border-color pb-0">
              <div className="flex gap-2" style={{ padding: '0.5rem' }}>
                {['data', 'ocr', 'json'].map((tabKey) => (
                  <button
                    key={tabKey}
                    className="py-2 px-4 text-sm font-medium rounded-md transition-all duration-200"
                    style={{ border: 'none', cursor: 'pointer', background: activeTab === tabKey ? 'var(--bg-primary)' : 'transparent', color: activeTab === tabKey ? 'var(--primary-accent)' : 'var(--text-secondary)' }}
                    onClick={() => setActiveTab(tabKey)}
                  >
                    {tabKey === 'data' ? 'Extracted Fields' : tabKey === 'ocr' ? 'Raw OCR Text' : 'JSON Payload'}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent className="p-6 bg-bg-secondary" style={{ maxHeight: '70vh', overflow: 'auto' }}>
              {activeTab === 'data' && parsedResult && (
                <div className="flex flex-col gap-6">
                  {parsedResult.fields && Object.keys(parsedResult.fields).length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold text-primary mb-3">Key Value Pairs</h3>
                      <div className="flex flex-col gap-4">
                        {Object.entries(parsedResult.fields).map(([key, data]) => {
                          const colors = fieldColors(data);
                          return (
                            <div key={key} className="p-4 rounded-lg border shadow-sm animate-fade-in-up" style={{ backgroundColor: colors.bg, borderColor: colors.border }}>
                              <div className="flex justify-between items-start mb-2">
                                <div className="text-xs text-tertiary uppercase tracking-wider font-semibold">{key.replace(/_/g, ' ')}</div>
                                <div className="flex items-center gap-2">
                                  {data.found && data.match_strategy && (
                                    <Badge variant={data.match_strategy === 'llm' ? 'info' : 'success'} className="text-[0.65rem] py-0">
                                      {data.match_strategy === 'llm' ? 'LLM extracted' : 'Direct match'}
                                    </Badge>
                                  )}
                                  {data.found && activeJob.job_id && (
                                    <a href={evidenceService.imageUrl(activeJob.job_id, key)} target="_blank" rel="noopener noreferrer" className="text-tertiary hover:text-primary-accent transition-colors" title="View source page">
                                      <Eye size={14} />
                                    </a>
                                  )}
                                </div>
                              </div>
                              <div className={`font-medium text-lg ${!data.found ? 'text-danger italic' : 'text-primary'}`}>
                                {data.found ? String(data.value) : 'Not found'}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {parsedResult.tables && parsedResult.tables.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold text-primary mb-3">Extracted Tables</h3>
                      <div className="flex flex-col gap-4">
                        {parsedResult.tables.map((table, idx) => (
                          <div key={idx} className="p-4 rounded-lg border shadow-sm overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
                            <div className="text-sm font-semibold text-primary mb-3 uppercase tracking-wider flex items-center gap-2">
                              <Layers size={16} className="text-primary-accent" />
                              {table.table_name.replace(/_/g, ' ')}
                            </div>
                            {!table.rows || table.rows.length === 0 ? (
                              <div className="p-4 text-center text-secondary italic bg-bg-secondary rounded-md">Table not found in document</div>
                            ) : (
                              <div className="overflow-x-auto">
                                <table className="w-full text-sm text-left border-collapse">
                                  <thead className="text-secondary uppercase font-medium" style={{ backgroundColor: 'rgba(0,0,0,0.05)' }}>
                                    <tr>{Object.keys(table.rows[0]).map((col) => <th key={col} className="px-4 py-2 border-b border-border-color">{col}</th>)}</tr>
                                  </thead>
                                  <tbody>
                                    {table.rows.map((row, rIdx) => (
                                      <tr key={rIdx} className="hover:bg-bg-secondary transition-colors border-b border-border-color border-opacity-50">
                                        {Object.values(row).map((val, cIdx) => <td key={cIdx} className="px-4 py-3">{val !== null ? String(val) : '-'}</td>)}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'ocr' && (() => {
                if (!ocrMetadata?.pages?.length) {
                  return <div className="p-8 text-center text-secondary italic">No OCR data found in payload.</div>;
                }
                return (
                  <div className="flex flex-col gap-6">
                    {ocrMetadata.pages.map((page, pIdx) => (
                      <div key={pIdx} className="flex flex-col gap-3">
                        <div className="flex items-center gap-3">
                          <span className="text-xs font-bold uppercase tracking-widest text-primary-accent">Page {page.page_number}</span>
                          <div className="flex-1 h-px bg-border-color" />
                        </div>
                        <div className="bg-bg-primary p-4 rounded-lg border border-border-color shadow-inner font-mono text-sm leading-relaxed">
                          {(page.lines || []).map((line, lIdx) => (
                            <div key={lIdx} className="py-0.5 hover:bg-bg-secondary rounded px-1">
                              <span className="text-secondary whitespace-pre-wrap break-all">{line.text}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}

              {activeTab === 'json' && (
                <div className="font-mono text-sm">
                  <JsonView data={parsedResult} shouldExpandNode={allExpanded} style={darkStyles} />
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {status === 'FAILED' && (
          <Card><CardContent>
            <div className="h-full flex flex-col items-center justify-center text-danger p-12">
              <XCircle size={48} className="mb-4 opacity-50" />
              <h3 className="text-xl font-bold mb-2">Extraction Failed</h3>
              <p className="text-center opacity-80">{activeJob.error_message || 'An unknown error occurred during extraction.'}</p>
            </div>
          </CardContent></Card>
        )}

        {(status === 'QUEUED' || status === 'PROCESSING') && (
          <Card><CardContent>
            <div className="h-full flex flex-col items-center justify-center text-warning p-12">
              <Clock size={48} className="mb-6 opacity-50 animate-spin" style={{ animationDuration: '3s' }} />
              <h3 className="text-xl font-bold mb-2 text-primary">Extraction in Progress</h3>
              <p className="text-center text-secondary">This updates automatically.</p>
            </div>
          </CardContent></Card>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto h-full p-4">
      <div className="flex flex-col text-center mt-4 mb-6">
        <h2 className="text-3xl font-bold text-primary font-heading mb-2">OCR — Single Pipeline</h2>
        <p className="text-secondary max-w-lg mx-auto">Pick one pipeline to process this document. Only that engine runs — no comparison.</p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-6">
        <Card className="animate-fade-in-up border border-primary-accent border-opacity-20 shadow-lg">
          <CardContent className="p-6">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-lg)' }}>
              <div className="input-group mb-0">
                <label className="input-label">Department</label>
                <select className="input-field" value={selectedDept} onChange={(e) => setSelectedDept(e.target.value)} disabled={uploading}>
                  <option value="" disabled>Select Department...</option>
                  {departments.map((d) => <option key={d.slug} value={d.slug}>{d.name}</option>)}
                </select>
              </div>
              <div className="input-group mb-0">
                <label className="input-label">Template</label>
                <select className="input-field" value={selectedTemplateCode} onChange={(e) => setSelectedTemplateCode(e.target.value)} disabled={!selectedDept || templates.length === 0 || uploading}>
                  {templates.length === 0 && <option value="" disabled>No active templates</option>}
                  {templates.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                </select>
              </div>
            </div>

            <div className="input-group mt-4 mb-0">
              <label className="input-label">Pipeline</label>
              <select className="input-field" value={selectedPipeline} onChange={(e) => setSelectedPipeline(e.target.value)} disabled={pipelines.length === 0 || uploading}>
                {pipelines.length === 0 && <option value="" disabled>No pipelines registered</option>}
                {pipelines.map((p) => <option key={p.name} value={p.name}>{p.name}{p.requires_gpu ? ' (GPU)' : ''}</option>)}
              </select>
            </div>

            {selectedPipeline && pipelines.length > 0 && (
              <div className="mt-4 flex items-center gap-3 p-3 bg-bg-secondary rounded-lg border border-border-color">
                <div className="p-2 bg-primary-accent bg-opacity-10 text-primary-accent rounded-lg"><Cpu size={18} /></div>
                <div className="text-xs text-secondary">
                  {(() => {
                    const p = pipelines.find((x) => x.name === selectedPipeline);
                    if (!p) return null;
                    const caps = [];
                    if (p.supports_layout_detection) caps.push('layout');
                    if (p.supports_tables) caps.push('tables');
                    if (p.supports_handwriting) caps.push('handwriting');
                    if (p.supports_vision) caps.push('vision');
                    return `${p.model_name || p.name} — supports: ${caps.join(', ') || 'basic text'}`;
                  })()}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {error && (
          <div className="p-4 bg-danger bg-opacity-10 border border-danger rounded-lg text-danger flex items-center gap-3 animate-fade-in-up">
            <AlertCircle size={20} /><span>{error}</span>
          </div>
        )}

        <div
          className={`flex-1 rounded-xl border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center cursor-pointer min-h-[260px] animate-fade-in-up
            ${dragActive ? 'border-primary-accent bg-primary-accent bg-opacity-5' : 'border-border-color hover:border-secondary-accent hover:bg-white hover:bg-opacity-5'}
            ${file ? 'bg-primary-accent bg-opacity-5 border-primary-accent' : ''}`}
          onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input ref={fileInputRef} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={handleFileChange} disabled={uploading} />
          {file ? (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-primary-accent rounded-full text-white shadow-glow"><File size={40} /></div>
              <div>
                <div className="text-xl font-bold text-primary">{file.name}</div>
                <div className="text-secondary mt-1">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
              </div>
              <button className="btn btn-secondary mt-2" onClick={(e) => { e.stopPropagation(); setFile(null); }} disabled={uploading}>Choose different file</button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-bg-secondary rounded-full text-secondary"><UploadCloud size={40} /></div>
              <div>
                <div className="text-xl font-bold text-primary">Drag & drop your file here</div>
                <div className="text-secondary mt-1">or click to browse from your computer</div>
              </div>
              <div className="text-xs text-tertiary mt-2">Supports PDF, PNG, JPG</div>
            </div>
          )}
        </div>

        <div className="flex justify-center mt-4">
          <button type="submit" className="btn btn-primary w-full max-w-xs flex justify-center py-3 text-lg shadow-glow" disabled={!file || !selectedTemplateCode || !selectedPipeline || uploading}>
            {uploading ? <span className="flex items-center gap-2"><Loader size={18} /> Processing...</span> : <span className="flex items-center gap-2"><Play size={18} /> Run {selectedPipeline || 'Pipeline'}</span>}
          </button>
        </div>
      </form>
    </div>
  );
};

export default OcrPage;
