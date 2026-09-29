import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, FileText, CheckCircle, XCircle, Clock, Download, Copy, Check, Server, AlignLeft, Layers } from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import 'react-json-view-lite/dist/index.css';
import { documentsService } from '../services/documents';
import { processingService } from '../services/processing';
import { departmentsService } from '../services/departments';
import { templatesService } from '../services/templates';
import api from '../services/api';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const StatusBadge = ({ status }) => {
  switch (status) {
    case 'COMPLETED': return <Badge variant="success">Completed</Badge>;
    case 'FAILED': return <Badge variant="danger">Failed</Badge>;
    case 'PROCESSING': return <Badge variant="warning">Processing</Badge>;
    case 'QUEUED': return <Badge variant="info">Queued</Badge>;
    default: return <Badge variant="default">{status}</Badge>;
  }
};

const PipelineStep = ({ title, description, status, icon: Icon, isLast }) => {
  const getColors = () => {
    switch (status) {
      case 'completed': return 'bg-success text-white border-success';
      case 'active': return 'bg-warning text-white border-warning animate-pulse shadow-[0_0_15px_rgba(245,158,11,0.5)]';
      case 'failed': return 'bg-danger text-white border-danger';
      default: return 'bg-bg-secondary text-secondary border-border-color';
    }
  };

  return (
    <div className="flex flex-col relative" style={{ flex: 1 }}>
      {!isLast && (
        <div className={`absolute top-5 left-1/2 w-full h-[2px] -z-10 ${status === 'completed' ? 'bg-success' : 'bg-border-color'}`} />
      )}
      <div className="flex flex-col items-center gap-3">
        <div
          className={`rounded-full border-2 flex items-center justify-center z-10 transition-all duration-300 ${getColors()}`}
          style={{ width: '40px', height: '40px', minWidth: '40px', minHeight: '40px', flexShrink: 0 }}
        >
          <Icon size={18} style={{ flexShrink: 0 }} />
        </div>
        <div className="text-center">
          <div className="font-semibold text-primary">{title}</div>
          <div className="text-xs text-secondary mt-1">{description}</div>
        </div>
      </div>
    </div>
  );
};

const DocumentDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const [doc, setDoc] = useState(null);
  const [activeJobDetails, setActiveJobDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState('data'); // data, ocr, json

  const parseJSONRecursively = (data) => {
    let parsed = data;
    while (typeof parsed === 'string') {
      try {
        const next = JSON.parse(parsed);
        if (typeof next === 'string' && next === parsed) break;
        parsed = next;
      } catch (e) {
        break;
      }
    }
    return parsed;
  };

  const handleCopy = () => {
    if (activeJobDetails?.result) {
      try {
        const parsed = parseJSONRecursively(activeJobDetails.result);
        navigator.clipboard.writeText(JSON.stringify(parsed, null, 2));
      } catch (e) {
        navigator.clipboard.writeText(String(activeJobDetails.result));
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  useEffect(() => {
    let cancelled = false;
    let pollTimeoutId = null;

    const fetchData = async () => {
      try {
        // 1. Fetch document and departments to find the department slug
        const [docRes] = await Promise.all([
          documentsService.get(id)
        ]);

        if (cancelled) return;
        const document = docRes.data;
        setDoc(document);

        // 2. Fetch jobs to get the latest job ID
        const jobsRes = await processingService.list({ document_id: id });
        if (cancelled) return;

        if (jobsRes.data.length > 0) {
          const latestJobId = jobsRes.data[0].job_id;

          const detailRes = await processingService.getJob(latestJobId);

          if (cancelled) return;
          setActiveJobDetails(detailRes.data);

          const status = detailRes.data.status;
          if (status === 'QUEUED' || status === 'PROCESSING') {
            pollTimeoutId = setTimeout(fetchData, 3000);
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to fetch document details');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();

    return () => {
      cancelled = true;
      if (pollTimeoutId) clearTimeout(pollTimeoutId);
    };
  }, [id]);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading extraction data..." /></div>;
  if (error) return <div className="text-danger p-4 glass-panel">{error}</div>;
  if (!doc) return <div className="text-secondary p-4">Document not found</div>;

  const jobStatus = activeJobDetails?.status || 'UNKNOWN';

  // Determine pipeline step statuses
  const getPipelineStatus = (step) => {
    if (jobStatus === 'FAILED') return step === 0 ? 'completed' : 'failed';
    if (jobStatus === 'COMPLETED') return 'completed';

    if (jobStatus === 'QUEUED') {
      return step === 0 ? 'active' : 'pending';
    }
    if (jobStatus === 'PROCESSING') {
      if (step === 0) return 'completed';
      if (step === 1 || step === 2) return 'active';
      return 'pending';
    }
    return 'pending';
  };

  const parsedResult = activeJobDetails?.extracted_json ? parseJSONRecursively(activeJobDetails.extracted_json) : null;
  const ocrMetadata = activeJobDetails?.ocr_metadata_json ? parseJSONRecursively(activeJobDetails.ocr_metadata_json) : null;


  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto p-4 w-full h-full">
      <div className="flex items-center gap-4 mb-2">
        <button className="btn btn-secondary" style={{ padding: '0.4rem' }} onClick={() => navigate('/documents')}>
          <ArrowLeft size={20} />
        </button>
        <h2 className="text-2xl font-bold text-primary font-heading flex items-center gap-3">
          <FileText className="text-primary-accent" />
          {doc.original_filename}
        </h2>
        <StatusBadge status={doc.status} />
      </div>



      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 'var(--spacing-lg)' }}>
        {/* Left Column: Metadata */}
        <div className="flex flex-col gap-6">
          <Card className="animate-fade-in">
            <CardHeader><CardTitle>Document Details</CardTitle></CardHeader>
            <CardContent>
              <div className="flex flex-col gap-4">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div>
                    <div className="text-xs text-tertiary mb-1 uppercase tracking-wider">Template ID</div>
                    <div className="font-medium text-primary-accent">{doc.template_id}</div>
                  </div>
                  <div>
                    <div className="text-xs text-tertiary mb-1 uppercase tracking-wider">File Type</div>
                    <div className="font-medium">{doc.file_type}</div>
                  </div>
                  <div>
                    <div className="text-xs text-tertiary mb-1 uppercase tracking-wider">Size</div>
                    <div className="font-medium">{(doc.file_size_bytes / 1024).toFixed(1)} KB</div>
                  </div>
                  <div>
                    <div className="text-xs text-tertiary mb-1 uppercase tracking-wider">Uploaded</div>
                    <div className="font-medium text-sm">{new Date(doc.created_at).toLocaleDateString()}</div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {activeJobDetails && (
            <Card className="animate-fade-in" style={{ animationDelay: '0.1s' }}>
              <CardHeader><CardTitle>Extraction Engine</CardTitle></CardHeader>
              <CardContent>
                <div className="flex flex-col gap-4">
                  {activeJobDetails.ai_model && (
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-secondary">AI Model</span>
                      <Badge variant="info">{activeJobDetails.ai_model}</Badge>
                    </div>
                  )}
                  {activeJobDetails.ocr_engine && (
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-secondary">OCR Engine</span>
                      <Badge variant="default">{activeJobDetails.ocr_engine}</Badge>
                    </div>
                  )}
                  {parsedResult?.template_version && (
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-secondary">Template Version</span>
                      <span className="font-medium">v{parsedResult.template_version}</span>
                    </div>
                  )}

                  {activeJobDetails.overall_confidence !== null && (
                    <div className="mt-2">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-sm text-secondary">Overall Confidence</span>
                        <span className="font-medium text-primary-accent">
                          {(activeJobDetails.overall_confidence * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="w-full bg-bg-tertiary rounded-pill h-2 overflow-hidden">
                        <div
                          className="h-full bg-gradient-primary"
                          style={{ width: `${activeJobDetails.overall_confidence * 100}%` }}
                        />
                      </div>
                    </div>
                  )}

                  {activeJobDetails.error_message && (
                    <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm mt-2">
                      {activeJobDetails.error_message}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right Column: Results Tabs */}
        <div className="flex flex-col gap-6">
          <Card className="animate-fade-in h-full flex flex-col shadow-lg" style={{ animationDelay: '0.2s', minHeight: '500px' }}>
            <CardHeader className="flex justify-between items-center border-b border-border-color pb-0">
              <div className="flex gap-2 p-2 bg-bg-secondary border-b border-border-color" style={{ borderTopLeftRadius: '12px', borderTopRightRadius: '12px', padding: '0.5rem' }}>
                <button
                  className={`py-2 px-4 text-sm font-medium transition-all duration-200 rounded-md ${activeTab === 'data' ? 'bg-bg-primary text-primary-accent shadow-sm' : 'text-secondary hover:text-primary hover:bg-bg-primary'}`}
                  style={{ border: 'none', outline: 'none', cursor: 'pointer', background: activeTab === 'data' ? 'var(--bg-primary)' : 'transparent' }}
                  onClick={() => setActiveTab('data')}
                >
                  Extracted Fields
                </button>
                <button
                  className={`py-2 px-4 text-sm font-medium transition-all duration-200 rounded-md ${activeTab === 'ocr' ? 'bg-bg-primary text-primary-accent shadow-sm' : 'text-secondary hover:text-primary hover:bg-bg-primary'}`}
                  style={{ border: 'none', outline: 'none', cursor: 'pointer', background: activeTab === 'ocr' ? 'var(--bg-primary)' : 'transparent' }}
                  onClick={() => setActiveTab('ocr')}
                >
                  Raw OCR Text
                </button>
                <button
                  className={`py-2 px-4 text-sm font-medium transition-all duration-200 rounded-md ${activeTab === 'json' ? 'bg-bg-primary text-primary-accent shadow-sm' : 'text-secondary hover:text-primary hover:bg-bg-primary'}`}
                  style={{ border: 'none', outline: 'none', cursor: 'pointer', background: activeTab === 'json' ? 'var(--bg-primary)' : 'transparent' }}
                  onClick={() => setActiveTab('json')}
                >
                  JSON Payload
                </button>
              </div>

              {activeJobDetails?.result && (
                <div className="flex gap-2 mb-3">
                  <button className="btn btn-secondary text-xs" onClick={handleCopy}>
                    {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              )}
            </CardHeader>
            <CardContent className="flex-1 p-0">

              {jobStatus === 'COMPLETED' ? (
                <div className="h-full w-full p-6 bg-bg-secondary overflow-auto" style={{ maxHeight: '600px' }}>

                  {activeTab === 'data' && parsedResult && (
                    <div className="flex flex-col gap-6">
                      {/* Fields Section */}
                      {parsedResult.fields && Object.keys(parsedResult.fields).length > 0 && (
                        <div>
                          <h3 className="text-lg font-semibold text-primary mb-3">Key Value Pairs</h3>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {Object.entries(parsedResult.fields).map(([key, data]) => (
                              <div key={key} className={`p-4 rounded-lg border shadow-sm ${data.found ? 'bg-bg-primary border-border-color' : 'bg-danger bg-opacity-5 border-danger border-opacity-30'}`}>
                                <div className="flex justify-between items-start mb-2">
                                  <div className="text-xs text-tertiary uppercase tracking-wider font-semibold">{key.replace(/_/g, ' ')}</div>
                                  {data.found && data.match_strategy && (
                                    <Badge variant={data.match_strategy === 'llm' ? 'info' : 'success'} className="text-[0.65rem] py-0">
                                      {data.match_strategy === 'llm' ? 'LLM extracted' : 'Direct match'}
                                    </Badge>
                                  )}
                                </div>

                                <div className={`font-medium text-lg mb-3 ${!data.found ? 'text-danger italic' : 'text-primary'}`}>
                                  {data.found ? String(data.value) : 'Not found'}
                                </div>

                                {data.found && (
                                  <div className="flex justify-between items-center bg-bg-secondary p-2 rounded-md">
                                    <div className="text-xs font-medium text-secondary">Confidence</div>
                                    <div className="flex items-center gap-2 w-1/2">
                                      <div className="w-full bg-bg-tertiary rounded-pill h-1.5 overflow-hidden flex-1">
                                        <div className={`h-full ${data.confidence > 0.8 ? 'bg-success' : data.confidence > 0.5 ? 'bg-warning' : 'bg-danger'}`} style={{ width: `${(data.confidence || 0) * 100}%` }} />
                                      </div>
                                      <div className="text-xs font-mono font-bold">{(data.confidence * 100).toFixed(0)}%</div>
                                    </div>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Tables Section */}
                      {parsedResult.tables && parsedResult.tables.length > 0 && (
                        <div>
                          <h3 className="text-lg font-semibold text-primary mb-3">Extracted Tables</h3>
                          <div className="flex flex-col gap-4">
                            {parsedResult.tables.map((table, idx) => (
                              <div key={idx} className="bg-bg-primary p-4 rounded-lg border border-border-color shadow-sm overflow-hidden">
                                <div className="text-sm font-semibold text-primary mb-3 uppercase tracking-wider flex items-center gap-2">
                                  <Layers size={16} className="text-primary-accent" />
                                  {table.table_name.replace(/_/g, ' ')}
                                </div>
                                {!table.rows || table.rows.length === 0 ? (
                                  <div className="p-4 text-center text-secondary italic bg-bg-secondary rounded-md">
                                    Table not found in document
                                  </div>
                                ) : (
                                  <div className="overflow-x-auto">
                                    <table className="w-full text-sm text-left border-collapse">
                                      <thead className="bg-bg-secondary text-secondary uppercase font-medium">
                                        <tr>
                                          {Object.keys(table.rows[0]).map((col) => (
                                            <th key={col} className="px-4 py-2 border-b border-border-color">{col}</th>
                                          ))}
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {table.rows.map((row, rIdx) => (
                                          <tr key={rIdx} className="hover:bg-bg-secondary transition-colors border-b border-border-color border-opacity-50">
                                            {Object.values(row).map((val, cIdx) => (
                                              <td key={cIdx} className="px-4 py-3">{val !== null ? String(val) : '-'}</td>
                                            ))}
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
                    if (!ocrMetadata || !ocrMetadata.pages || ocrMetadata.pages.length === 0) {
                      return (
                        <div className="p-8 text-center text-secondary italic">
                          No OCR data found in payload.
                        </div>
                      );
                    }
                    return (
                      <div className="flex flex-col gap-6">
                        {ocrMetadata.pages.map((page, pIdx) => {
                          const lines = page.lines || [];
                          const tables = page.tables || [];
                          const hasContent = lines.length > 0 || tables.length > 0;
                          return (
                            <div key={pIdx} className="flex flex-col gap-3">
                              <div className="flex items-center gap-3">
                                <span className="text-xs font-bold uppercase tracking-widest text-primary-accent">
                                  Page {page.page_number}
                                </span>
                                <span className="text-xs text-tertiary">
                                  {lines.length} line{lines.length !== 1 ? 's' : ''}
                                  {tables.length > 0 ? `, ${tables.length} table${tables.length !== 1 ? 's' : ''}` : ''}
                                </span>
                                <div className="flex-1 h-px bg-border-color" />
                              </div>
                              {!hasContent ? (
                                <div className="text-secondary italic text-sm">No text extracted for this page.</div>
                              ) : (
                                <>
                                  {lines.length > 0 && (
                                    <div className="bg-bg-primary p-4 rounded-lg border border-border-color shadow-inner font-mono text-sm leading-relaxed">
                                      {lines.map((line, lIdx) => (
                                        <div key={lIdx} className="flex items-start gap-3 py-0.5 hover:bg-bg-secondary rounded px-1 group">
                                          {line.confidence != null && (
                                            <span
                                              className="text-xs mt-0.5 shrink-0 font-medium"
                                              style={{
                                                color: line.confidence > 0.8
                                                  ? 'var(--color-success)'
                                                  : line.confidence > 0.5
                                                    ? 'var(--color-warning)'
                                                    : 'var(--color-danger)',
                                                opacity: 0,
                                              }}
                                              // show on row hover via CSS group trick — use inline style
                                              onMouseEnter={e => { e.currentTarget.style.opacity = 1; }}
                                              onMouseLeave={e => { e.currentTarget.style.opacity = 0; }}
                                            >
                                              {(line.confidence * 100).toFixed(0)}%
                                            </span>
                                          )}
                                          <span className="text-secondary whitespace-pre-wrap break-all">{line.text}</span>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                  {tables.length > 0 && (
                                    <div className="flex flex-col gap-3">
                                      {tables.map((tbl, tIdx) => {
                                        const headers = tbl.headers || [];
                                        const rows = tbl.rows || [];
                                        return (
                                          <div key={tIdx} className="bg-bg-primary rounded-lg border border-border-color overflow-hidden">
                                            <div className="px-4 py-2 bg-bg-secondary border-b border-border-color text-xs font-semibold uppercase tracking-wider text-primary-accent">
                                              OCR Table {tIdx + 1}
                                            </div>
                                            {headers.length > 0 ? (
                                              <div className="overflow-x-auto">
                                                <table className="w-full text-xs text-left border-collapse font-mono">
                                                  <thead className="bg-bg-secondary text-secondary">
                                                    <tr>
                                                      {headers.map((h, hIdx) => (
                                                        <th key={hIdx} className="px-3 py-2 border-b border-border-color">{h || '—'}</th>
                                                      ))}
                                                    </tr>
                                                  </thead>
                                                  <tbody>
                                                    {rows.map((row, rIdx) => (
                                                      <tr key={rIdx} className="border-b border-border-color border-opacity-40 hover:bg-bg-secondary">
                                                        {(Array.isArray(row) ? row : Object.values(row)).map((cell, cIdx) => (
                                                          <td key={cIdx} className="px-3 py-2 text-secondary">{cell != null ? String(cell) : '—'}</td>
                                                        ))}
                                                      </tr>
                                                    ))}
                                                  </tbody>
                                                </table>
                                              </div>
                                            ) : (
                                              <div className="p-4 font-mono text-sm whitespace-pre-wrap text-secondary">{tbl.markdown || ''}</div>
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  )}
                                </>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })()}

                  {activeTab === 'json' && (
                    <div className="font-mono text-sm">
                      <JsonView data={parsedResult} shouldExpandNode={allExpanded} style={darkStyles} />
                    </div>
                  )}

                </div>
              ) : jobStatus === 'FAILED' ? (
                <div className="h-full flex flex-col items-center justify-center text-danger p-12">
                  <XCircle size={48} className="mb-4 opacity-50" />
                  <h3 className="text-xl font-bold mb-2">Extraction Failed</h3>
                  <p className="text-center opacity-80">{activeJobDetails?.error_message || 'An unknown error occurred during extraction.'}</p>
                </div>
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-warning p-12">
                  <Clock size={48} className="mb-6 opacity-50 animate-spin" style={{ animationDuration: '3s' }} />
                  <h3 className="text-xl font-bold mb-2 text-primary">Extraction in Progress</h3>
                  <p className="text-center text-secondary">Our AI engines are currently processing your document.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default DocumentDetail;
