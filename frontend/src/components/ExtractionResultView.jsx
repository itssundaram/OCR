import React, { useState } from 'react';
import { ArrowLeft, File, Code, RefreshCw, XCircle, Clock, CheckCircle2, ZoomOut, ZoomIn, Maximize, ShieldCheck, Copy, Edit2, Flag, Printer, Download, Plus, Minus, Menu } from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { Card, CardContent, CardHeader } from './ui/Card';
import { Badge } from './ui/Badge';
import { Loader } from './ui/Loader';
import { evidenceService } from '../services/evidence';
import { config } from '../config';
import { Document, Page, pdfjs } from 'react-pdf';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

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

const ExtractionResultView = ({ 
  activeJob, 
  onBack, 
  pipelineSteps = [], 
  setZoomedImage 
}) => {
  const [activeTab, setActiveTab] = useState('data');
  const [numPages, setNumPages] = useState(null);

  if (!activeJob) return null;

  const parsedResult = activeJob.extracted_json ? parseJSONRecursively(activeJob.extracted_json) : null;
  const fields = parsedResult?.fields || {};
  const fieldKeys = Object.keys(fields);
  const totalFields = fieldKeys.length;
  const extractedCount = fieldKeys.filter(k => fields[k]?.found).length;
  const status = activeJob.status;
  const isFinished = status === 'COMPLETED' || status === 'WARNING';
  const ocrMetadata = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : null;

  let avgConf = totalFields > 0 ? fieldKeys.reduce((acc, k) => acc + (fields[k]?.confidence || 0), 0) / totalFields : 0;
  let displayAvgConf = avgConf * 100;
  if (displayAvgConf > 97.5) displayAvgConf = 97.5;

  let avgConfColorClass = 'bg-green-50 text-green-700 border-green-200';
  let AvgConfIcon = CheckCircle2;
  if (displayAvgConf < 60) {
    avgConfColorClass = 'bg-red-50 text-red-700 border-red-200';
    AvgConfIcon = XCircle;
  } else if (displayAvgConf < 85) {
    avgConfColorClass = 'bg-yellow-50 text-yellow-700 border-yellow-200';
    AvgConfIcon = Flag;
  }

  const latencyStr = (() => {
    if (activeJob.started_at && activeJob.completed_at) {
      const started = new Date(activeJob.started_at);
      const completed = new Date(activeJob.completed_at);
      const diffMs = completed - started;
      if (!isNaN(diffMs)) return (diffMs / 1000).toFixed(2) + 's';
    } else if (activeJob.created_at && activeJob.completed_at) {
      const created = new Date(activeJob.created_at);
      const completed = new Date(activeJob.completed_at);
      const diffMs = completed - created;
      if (!isNaN(diffMs)) return (diffMs / 1000).toFixed(2) + 's';
    }
    return '-';
  })();

  if (!isFinished && status !== 'FAILED') {
    return (
      <div className="flex-1 flex flex-col items-center justify-center border-2 border-dashed border-border-color rounded-lg bg-bg-secondary opacity-50 h-full mt-4">
        <Clock size={48} className="text-secondary mb-4" />
        <h3 className="text-xl font-semibold text-primary">Processing</h3>
        <p className="text-secondary">Job is currently running or queued...</p>
      </div>
    );
  }

  if (status === 'FAILED') {
    return (
      <Card><CardContent>
        <div className="h-full flex flex-col items-center justify-center text-danger p-12">
          <XCircle size={48} className="mb-4 opacity-50" />
          <h3 className="text-xl font-bold mb-2">Extraction Failed</h3>
          <p className="text-center opacity-80">{activeJob.error_message || 'An unknown error occurred during extraction.'}</p>
        </div>
      </CardContent></Card>
    );
  }

  return (
    <div className="flex flex-col h-full w-full overflow-hidden bg-bg-primary">
      {/* Header Panel */}
      <div className="flex flex-col gap-4 bg-white border border-border-color rounded-lg p-4 mb-4 shadow-sm flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {onBack && (
              <button className="p-2 border border-gray-200 rounded hover:bg-gray-50" onClick={onBack}>
                <ArrowLeft size={18} className="text-gray-600" />
              </button>
            )}
            <h2 className="text-[1.3rem] font-bold text-primary font-heading m-0">Extraction Result</h2>
            <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.65rem] tracking-widest px-2 py-0.5 uppercase font-bold">COMPLETED</Badge>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn btn-secondary py-1.5 px-3 text-xs flex items-center gap-2 bg-white border border-gray-200 shadow-sm text-gray-700"><ShieldCheck size={14}/> Verify Schema</button>
            <button className="btn btn-primary py-1.5 px-3 text-xs flex items-center gap-2 bg-[#1e3a8a] shadow-md"><RefreshCw size={14}/> Re-run Pipeline</button>
          </div>
        </div>
        
        <div className="border-t border-gray-100 pt-3 flex items-center gap-8 text-xs font-mono">
          <div className="flex flex-col gap-1">
            <span className="text-gray-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">EXECUTION ID</span>
            <span className="text-primary font-medium">#{activeJob.job_id.substring(0,8)}-{activeJob.job_id.substring(9,13)}</span>
          </div>
          <div className="w-px h-8 bg-gray-200"></div>
          <div className="flex flex-col gap-1">
            <span className="text-gray-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">DOCUMENT FILE</span>
            <span className="text-primary font-medium truncate max-w-[200px]" title={activeJob.original_filename || activeJob.document_id}>{activeJob.original_filename || activeJob.document_id || 'Unknown Document'}</span>
          </div>
          <div className="w-px h-8 bg-gray-200"></div>
          <div className="flex flex-col gap-1">
            <span className="text-gray-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">PARSER TEMPLATE</span>
            <span className="text-primary font-medium">{activeJob.template_code || 'Unknown Template'}</span>
          </div>
          <div className="w-px h-8 bg-gray-200"></div>
          <div className="flex flex-col gap-1">
            <span className="text-gray-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">INGESTION ENGINE</span>
            <span className="text-primary font-medium text-blue-700">
              {activeJob.pipeline_mode === 'paddle' ? 'PaddleOCR + Ollama' : 
               activeJob.pipeline_mode === 'surya' ? 'Surya OCR + Ollama' : 
               activeJob.pipeline_mode === 'tesseract' ? 'Tesseract + Ollama' : 
               activeJob.pipeline_mode === 'orchestration' ? 'Orchestration Engine' : 
               (activeJob.pipeline_mode || 'Unknown Engine').toUpperCase()}
            </span>
          </div>
          <div className="w-px h-8 bg-gray-200"></div>
          <div className="flex flex-col gap-1">
            <span className="text-gray-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">LATENCY</span>
            <span className="text-green-600 font-bold">{latencyStr}</span>
          </div>
        </div>
      </div>

      {/* Main Split Layout */}
      <div className="flex flex-1 gap-6 overflow-hidden">
        {/* Left: Original Document */}
        <div className="flex-1 flex flex-col bg-white border border-border-color rounded-lg overflow-hidden shadow-sm max-w-[50%] h-full">
          <TransformWrapper initialScale={1} minScale={0.5} maxScale={4} centerOnInit={true}>
            {({ zoomIn, zoomOut, resetTransform }) => (
              <>
                <div className="flex items-center justify-between p-3 border-b border-border-color bg-white">
                  <div className="text-sm font-bold text-primary flex items-center gap-2">
                    <File size={16} className="text-[#1e3a8a]" /> Original Document
                  </div>
                  <div className="flex items-center gap-3 text-sm text-gray-500">
                    <Menu size={16} className="cursor-pointer hover:text-primary" />
                    <div className="flex items-center gap-2 px-2 bg-gray-50 rounded">
                      <span className="font-mono text-xs">1 / {numPages || '-'}</span>
                    </div>
                    <Minus size={14} className="cursor-pointer hover:text-primary" onClick={() => zoomOut()} />
                    <Plus size={14} className="cursor-pointer hover:text-primary" onClick={() => zoomIn()} />
                    <Maximize size={14} className="cursor-pointer hover:text-primary" onClick={() => resetTransform()} />
                    <RefreshCw size={14} className="cursor-pointer hover:text-primary" />
                    <Download size={14} className="cursor-pointer hover:text-primary" />
                    <Printer size={14} className="cursor-pointer hover:text-primary" />
                  </div>
                </div>
                <div className="flex-1 overflow-hidden bg-[#e5e5e5] relative flex flex-col items-center justify-center h-full w-full">
                  {activeJob.document_id ? (
                    <TransformComponent wrapperStyle={{ width: '100%', height: '100%' }} contentStyle={{ width: '100%', height: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                      <Document 
                        file={`${config.API_BASE_URL}/documents/${activeJob.document_id}/file`} 
                        onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                        loading={<div className="flex items-center justify-center p-8"><Loader text="Loading PDF..." /></div>}
                        className="shadow-md"
                      >
                        <Page pageNumber={1} renderTextLayer={false} renderAnnotationLayer={false} width={600} />
                      </Document>
                    </TransformComponent>
                  ) : (
                    <div className="p-8 text-center text-secondary italic mt-20">Document not available</div>
                  )}
                </div>
              </>
            )}
          </TransformWrapper>
        </div>

        {/* Right side: Results Tabs */}
        <div className="flex-1 flex flex-col min-w-0 h-full">
          <div className="flex gap-6 border-b border-border-color bg-white pt-2 px-4 rounded-t-lg shadow-sm border-x border-t">
            {['data', 'ocr', 'json'].map((tabKey) => (
              <button
                key={tabKey}
                className={`pb-3 text-sm font-bold relative transition-colors ${activeTab === tabKey ? 'text-[#1e3a8a]' : 'text-gray-500 hover:text-gray-700'}`}
                onClick={() => setActiveTab(tabKey)}
              >
                {tabKey === 'data' ? 'Extracted Fields' : tabKey === 'ocr' ? 'Raw OCR Text' : 'JSON Payload'}
                {activeTab === tabKey && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#1e3a8a]"></div>}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto pb-4 bg-bg-primary">
            {activeTab === 'data' && (
              <div className="flex flex-col gap-4 mt-4 px-2">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-lg font-bold text-primary font-heading">Key Value Pairs</div>
                  <div className="flex items-center gap-4">
                    <span className="text-xs font-bold text-gray-500 font-mono tracking-widest">{extractedCount} / {totalFields}</span>
                    {totalFields > 0 && <Badge className={`border text-[0.65rem] px-2 py-1 font-bold flex items-center gap-1 ${avgConfColorClass}`}><AvgConfIcon size={12}/> {displayAvgConf.toFixed(1)}% Avg Confidence</Badge>}
                  </div>
                </div>

                {!parsedResult ? (
                  <div className="p-8 text-center text-secondary italic">Could not parse extraction result.</div>
                ) : (
                  <>
                    {fields && typeof fields === 'object' && !Array.isArray(fields) && Object.keys(fields).length > 0 && (
                      <div className="flex flex-col gap-4">
                        {Object.entries(fields).map(([key, data]) => {
                          const conf = data.confidence || 0;
                          let displayConf = conf * 100;
                          if (displayConf > 97.5) displayConf = 97.5;
                          
                          let confColor = 'text-green-600';
                          let ConfIcon = CheckCircle2;
                          if (displayConf < 60) {
                            confColor = 'text-red-600';
                            ConfIcon = XCircle;
                          } else if (displayConf < 85) {
                            confColor = 'text-yellow-600';
                            ConfIcon = Flag;
                          }
                          
                          return (
                            <div key={key} className="flex flex-col rounded-lg border bg-white shadow-sm overflow-hidden animate-fade-in-up border-gray-200">
                              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-white">
                                <div className="flex items-center gap-3">
                                  <span className="text-xs font-bold text-gray-600 uppercase tracking-widest">{key.replace(/_/g, ' ')}</span>
                                  {data?.found && (
                                    <Badge className="text-[0.65rem] font-bold text-[#1e3a8a] bg-blue-50 px-2 py-0.5 rounded uppercase tracking-wider border-none">
                                      {data.match_strategy?.includes('llm') ? 'LLM EXTRACTED' : 'LLM EXTRACTED'}
                                    </Badge>
                                  )}
                                </div>
                                <div className="flex items-center gap-2">
                                  {data?.found && (
                                    <div className={`flex items-center gap-1.5 font-bold text-xs ${confColor}`}>
                                      <ConfIcon size={14} /> Confidence: {displayConf.toFixed(1)}%
                                    </div>
                                  )}
                                </div>
                              </div>
                              <div className="p-4 bg-gray-50/50">
                                <div className="text-[0.65rem] font-bold text-gray-400 uppercase tracking-wider mb-2">RESOLVED VALUE</div>
                                <div className="flex items-center justify-between bg-white border border-gray-200 rounded p-2.5">
                                  <div className={`font-bold text-base font-mono ${!data?.found ? 'text-danger italic' : 'text-primary'}`}>
                                    {data?.found ? String(data.value) : 'Not found'}
                                  </div>
                                  <div className="flex gap-3 text-gray-400">
                                    <Copy size={16} className="cursor-pointer hover:text-primary transition-colors" />
                                    <Edit2 size={16} className="cursor-pointer hover:text-primary transition-colors" />
                                  </div>
                                </div>
                              </div>
                              
                              {data?.found && activeJob.job_id && (
                                <div className="px-4 py-3 border-t border-gray-100 bg-white">
                                  <div className="text-[0.65rem] font-bold text-gray-400 uppercase tracking-wider mb-2">EXTRACTION SOURCE CROP</div>
                                  <div className="flex items-center gap-4">
                                    <div className="bg-[#e0e7ff] border border-blue-300 p-2 rounded shadow-sm flex items-center justify-center max-h-16 overflow-hidden">
                                      <img 
                                        src={evidenceService.imageUrl(activeJob.job_id, key)} 
                                        alt={`Source for ${key}`}
                                        className="h-full object-contain mx-auto mix-blend-multiply"
                                        onClick={() => setZoomedImage && setZoomedImage(evidenceService.imageUrl(activeJob.job_id, key))}
                                        onError={(e) => { e.target.style.display = 'none'; }}
                                      />
                                    </div>
                                    <div className="flex items-center gap-1.5 text-xs font-mono text-gray-500 font-semibold bg-white border border-gray-200 px-3 py-1.5 rounded-full shadow-sm">
                                      <div className="w-2 h-2 rounded-full border border-gray-400"></div>
                                      (x:390, y:512)
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
            
            {activeTab === 'ocr' && (() => {
              if (!ocrMetadata?.pages?.length) {
                return (
                  <div className="h-full p-2">
                    <div className="p-8 text-center text-secondary italic">No raw OCR text available in metadata payload.</div>
                  </div>
                );
              }
              return (
                <div className="h-full p-2 overflow-auto">
                  <div className="flex flex-col gap-6">
                    {ocrMetadata.pages.map((page, pIdx) => (
                      <div key={pIdx} className="flex flex-col gap-3">
                        <div className="flex items-center gap-3">
                          <span className="text-xs font-bold uppercase tracking-widest text-primary-accent">Page {page.page_number}</span>
                          <div className="flex-1 h-px bg-border-color" />
                        </div>
                        <div className="bg-[#1e2227] text-slate-300 p-4 rounded-lg border border-[#3b4252] shadow-inner font-mono text-sm leading-relaxed max-w-full overflow-hidden">
                          {(page.lines || []).map((line, lIdx) => (
                            <div key={lIdx} className="py-0.5 hover:bg-[#282c34] rounded px-1">
                              <span className="whitespace-pre-wrap break-all">{line.text}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {activeTab === 'json' && (
              <div className="h-full p-2">
                <div className="p-4 bg-[#1e2227] rounded-lg overflow-auto h-full shadow-inner border border-[#3b4252]">
                  <JsonView data={parsedResult} shouldExpandNode={allExpanded} style={darkStyles} />
                </div>
              </div>
            )}
            
            {activeTab === 'audit' && (
              <div className="p-8 text-center text-secondary italic">Validation Audit history is currently empty.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ExtractionResultView;
