import React, { useState, useRef, useEffect } from 'react';
import {
  ArrowLeft, FileText, CheckCircle2, XCircle, Clock, Zap, Cpu,
  Copy, Check, RefreshCw, ZoomIn, ZoomOut, Maximize, Crosshair,
  ShieldCheck, Eye, Layers, Sparkles, ChevronRight, AlertCircle, Database
} from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { Document, Page, pdfjs } from 'react-pdf';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import { Badge } from './ui/Badge';
import { evidenceService } from '../services/evidence';
import { config } from '../config';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.js';

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

const FastExtractionResultView = ({
  activeJob,
  onBack,
  pipelineSteps = [],
  setZoomedImage,
  onRerun,
}) => {
  const [activeTab, setActiveTab] = useState('results');
  const [selectedField, setSelectedField] = useState(null);
  const [copiedKey, setCopiedKey] = useState(null);
  const [numPages, setNumPages] = useState(null);
  const [pageDimensions, setPageDimensions] = useState({ width: 620, height: 877, originalWidth: 1654, originalHeight: 2338 });
  const fieldRefs = useRef({});

  if (!activeJob) return null;

  const parsedResult = activeJob.extracted_json ? parseJSONRecursively(activeJob.extracted_json) : null;
  const ocrMetadata = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : {};
  const fields = parsedResult?.fields || {};
  const fieldKeys = Object.keys(fields);
  const totalFields = fieldKeys.length;
  const extractedCount = fieldKeys.filter((k) => fields[k]?.found).length;

  const isWarmHit = ocrMetadata?.mode === 'WARM_ANCHOR_HIT' || activeJob.ocr_metadata_json?.includes?.('WARM_ANCHOR_HIT');
  const learnedAnchors = ocrMetadata?.learned_anchors || parsedResult?.learned_anchors;

  // Genuine execution duration
  const latencyStr = (() => {
    if (activeJob.started_at && activeJob.completed_at) {
      const started = new Date(activeJob.started_at);
      const completed = new Date(activeJob.completed_at);
      const diffMs = completed - started;
      if (!isNaN(diffMs)) {
        return diffMs < 1000 ? `${diffMs}ms` : `${(diffMs / 1000).toFixed(2)}s`;
      }
    }
    return '-';
  })();

  const avgConfidence = totalFields > 0
    ? (fieldKeys.reduce((acc, k) => acc + (fields[k]?.confidence || 0), 0) / totalFields) * 100
    : 0;

  const handleCopy = (key, value) => {
    navigator.clipboard.writeText(String(value));
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleSelectField = (key) => {
    setSelectedField(key);
    if (fieldRefs.current[key]) {
      fieldRefs.current[key].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  const onPdfPageLoadSuccess = (page) => {
    setPageDimensions((prev) => ({
      ...prev,
      originalWidth: page.originalWidth,
      originalHeight: page.originalHeight,
    }));
  };

  // Coordinate scaling factors
  const docRefWidth = learnedAnchors?.page_width || pageDimensions.originalWidth || 1654;
  const docRefHeight = learnedAnchors?.page_height || pageDimensions.originalHeight || 2338;
  const scaleX = pageDimensions.width / docRefWidth;
  const scaleY = (pageDimensions.width * (docRefHeight / docRefWidth)) / docRefHeight;

  return (
    <div className="flex flex-col h-full w-full overflow-hidden bg-slate-50 text-slate-800">
      {/* Top Banner: Real Pipeline Status & True Latency */}
      <div className="flex flex-col gap-3 bg-white border border-slate-200 rounded-xl p-4 mb-4 shadow-sm flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {onBack && (
              <button
                className="p-2 border border-slate-200 rounded-lg hover:bg-slate-100 transition-colors text-slate-600"
                onClick={onBack}
                title="Go back"
              >
                <ArrowLeft size={18} />
              </button>
            )}
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-xl font-bold text-slate-900 tracking-tight m-0 font-heading">
                  Learned Geometric Extraction
                </h1>
                {isWarmHit ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300 shadow-sm animate-pulse">
                    <Zap size={14} className="text-emerald-600" />
                    WARM ANCHOR HIT ({latencyStr})
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-indigo-100 text-indigo-800 border border-indigo-300 shadow-sm">
                    <Sparkles size={14} className="text-indigo-600" />
                    COLD LEARNED CACHE ({latencyStr})
                  </span>
                )}
                <span className="text-xs font-mono text-slate-400 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
                  JOB #{activeJob.job_id ? activeJob.job_id.substring(0, 8) : 'unknown'}
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                Sub-second geometric anchor matching with autonomous template coordinate memory.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex flex-col items-end mr-2">
              <span className="text-[0.65rem] uppercase tracking-wider font-bold text-slate-400">Total Latency</span>
              <span className="text-base font-extrabold font-mono text-emerald-600">{latencyStr}</span>
            </div>
            {onRerun && (
              <button
                className="btn btn-secondary py-1.5 px-3 text-xs flex items-center gap-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 shadow-sm"
                onClick={onRerun}
              >
                <RefreshCw size={14} /> Re-extract
              </button>
            )}
          </div>
        </div>

        {/* Live Metrics Strip */}
        <div className="border-t border-slate-100 pt-3 grid grid-cols-4 gap-4 text-xs font-mono">
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Template Code</span>
            <span className="text-slate-800 font-semibold truncate">{activeJob.template_code || 'AUTODETECT'}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Extraction Strategy</span>
            <span className="text-blue-700 font-semibold">{isWarmHit ? 'Geometric Anchor Crops' : 'Adaptive VLM Geometry'}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Fields Extracted</span>
            <span className="text-slate-800 font-semibold">{extractedCount} / {totalFields} fields</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Overall Accuracy</span>
            <span className={`font-bold ${avgConfidence >= 80 ? 'text-emerald-600' : 'text-amber-600'}`}>
              {avgConfidence.toFixed(1)}%
            </span>
          </div>
        </div>
      </div>

      {/* Main Two-Column Interactive View */}
      <div className="flex flex-1 gap-5 overflow-hidden">
        {/* Left Column: Interactive Document Canvas with Bounding Box Overlays */}
        <div className="flex-1 flex flex-col bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm max-w-[48%] h-full">
          <div className="flex items-center justify-between p-3 border-b border-slate-200 bg-slate-50 flex-shrink-0">
            <div className="text-xs font-bold text-slate-700 flex items-center gap-2">
              <Crosshair size={16} className="text-blue-600" />
              Document Spatial Geometry & Anchors
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="px-2 py-0.5 bg-white border border-slate-200 rounded font-mono text-[0.7rem]">
                Page 1 of {numPages || 1}
              </span>
            </div>
          </div>

          <div className="flex-1 overflow-hidden bg-slate-100 relative flex flex-col items-center justify-center">
            {activeJob.document_id ? (
              <TransformWrapper initialScale={1} minScale={0.6} maxScale={3.5} centerOnInit={true}>
                {({ zoomIn, zoomOut, resetTransform }) => (
                  <div className="relative w-full h-full flex flex-col items-center justify-center">
                    {/* Floating Controls */}
                    <div className="absolute top-3 right-3 z-20 flex items-center gap-1 bg-white/90 backdrop-blur border border-slate-200 p-1 rounded-lg shadow-sm">
                      <button className="p-1 hover:bg-slate-100 rounded text-slate-600" onClick={() => zoomIn()} title="Zoom In">
                        <ZoomIn size={15} />
                      </button>
                      <button className="p-1 hover:bg-slate-100 rounded text-slate-600" onClick={() => zoomOut()} title="Zoom Out">
                        <ZoomOut size={15} />
                      </button>
                      <button className="p-1 hover:bg-slate-100 rounded text-slate-600" onClick={() => resetTransform()} title="Reset">
                        <Maximize size={15} />
                      </button>
                    </div>

                    <TransformComponent wrapperStyle={{ width: '100%', height: '100%' }} contentStyle={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                      <div className="relative inline-block shadow-md my-4 bg-white" style={{ width: pageDimensions.width }}>
                        <Document
                          file={`${config.API_BASE_URL}/documents/${activeJob.document_id}/file`}
                          onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                          loading={<div className="p-10 text-center text-xs text-slate-500">Loading document canvas...</div>}
                        >
                          <Page
                            pageNumber={1}
                            width={pageDimensions.width}
                            renderTextLayer={false}
                            renderAnnotationLayer={false}
                            onLoadSuccess={onPdfPageLoadSuccess}
                          />
                        </Document>

                        {/* Interactive Bounding Box Overlays */}
                        {Object.entries(fields).map(([key, data]) => {
                          if (!data?.bbox || data.bbox.x1 === undefined) return null;
                          const { x1, y1, x2, y2 } = data.bbox;
                          const boxLeft = x1 * scaleX;
                          const boxTop = y1 * scaleY;
                          const boxWidth = (x2 - x1) * scaleX;
                          const boxHeight = (y2 - y1) * scaleY;
                          const isSelected = selectedField === key;

                          return (
                            <div
                              key={key}
                              className={`absolute cursor-pointer transition-all duration-200 border-2 rounded ${
                                isSelected
                                  ? 'border-blue-600 bg-blue-500/25 ring-2 ring-blue-400 z-10 shadow-lg'
                                  : 'border-emerald-500/80 bg-emerald-500/10 hover:border-blue-500 hover:bg-blue-500/20'
                              }`}
                              style={{
                                left: `${boxLeft}px`,
                                top: `${boxTop}px`,
                                width: `${Math.max(20, boxWidth)}px`,
                                height: `${Math.max(14, boxHeight)}px`,
                              }}
                              onClick={() => handleSelectField(key)}
                              title={`${key}: ${data.value}`}
                            >
                              <span
                                className={`absolute -top-4 left-0 text-[9px] font-mono font-bold px-1 rounded shadow-sm whitespace-nowrap uppercase ${
                                  isSelected ? 'bg-blue-600 text-white' : 'bg-emerald-700 text-white'
                                }`}
                              >
                                {key.replace(/_/g, ' ')}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </TransformComponent>
                  </div>
                )}
              </TransformWrapper>
            ) : (
              <div className="text-slate-400 text-sm italic">Document preview unavailable</div>
            )}
          </div>
        </div>

        {/* Right Column: High-Priority Extracted Results & Upright Source Crops */}
        <div className="flex-1 flex flex-col min-w-0 h-full bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
          {/* Tabs Navigation */}
          <div className="flex items-center justify-between border-b border-slate-200 px-4 pt-2 bg-slate-50 flex-shrink-0">
            <div className="flex gap-4">
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors ${
                  activeTab === 'results' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('results')}
              >
                Extracted Fields ({extractedCount})
              </button>
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors ${
                  activeTab === 'geometry' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('geometry')}
              >
                Learned Geometry Memory
              </button>
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors ${
                  activeTab === 'json' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('json')}
              >
                Raw JSON
              </button>
            </div>
            <span className="text-[11px] font-mono text-slate-400 pb-2">
              Showing {extractedCount} verified results
            </span>
          </div>

          {/* Results Tab Content */}
          {activeTab === 'results' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-slate-50/50">
              {fieldKeys.map((key) => {
                const data = fields[key] || {};
                const confPercent = Math.round((data.confidence || 0) * 100);
                const isSelected = selectedField === key;
                const hasBbox = data.bbox && data.bbox.x1 !== undefined;

                return (
                  <div
                    key={key}
                    ref={(el) => (fieldRefs.current[key] = el)}
                    className={`rounded-xl border transition-all duration-200 bg-white overflow-hidden shadow-sm ${
                      isSelected
                        ? 'border-blue-500 ring-2 ring-blue-100 shadow-md'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                    onClick={() => setSelectedField(key)}
                  >
                    {/* Field Header */}
                    <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50 border-b border-slate-100">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold uppercase tracking-wider text-slate-700">
                          {key.replace(/_/g, ' ')}
                        </span>
                        {data.found ? (
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200 font-mono">
                            {data.match_strategy || 'LEARNED_ANCHOR'}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-red-50 text-red-700 border border-red-200">
                            NOT FOUND
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-2">
                        <span
                          className={`text-xs font-bold font-mono px-2 py-0.5 rounded ${
                            confPercent >= 80
                              ? 'bg-emerald-50 text-emerald-700'
                              : confPercent >= 50
                              ? 'bg-amber-50 text-amber-700'
                              : 'bg-red-50 text-red-700'
                          }`}
                        >
                          {confPercent}% Conf
                        </span>
                      </div>
                    </div>

                    {/* Field Body: Resolved Value (Priority #1) */}
                    <div className="p-3.5 flex flex-col gap-2">
                      <div className="flex items-center justify-between bg-slate-50 border border-slate-200 rounded-lg p-2.5">
                        <div className="font-mono text-sm font-bold text-slate-900 break-all select-all">
                          {data.found ? String(data.value) : <span className="text-red-500 italic">Not extracted</span>}
                        </div>
                        <button
                          className="p-1.5 hover:bg-white rounded text-slate-400 hover:text-slate-700 transition-colors"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopy(key, data.value);
                          }}
                          title="Copy to clipboard"
                        >
                          {copiedKey === key ? <Check size={16} className="text-emerald-600" /> : <Copy size={16} />}
                        </button>
                      </div>

                      {/* Extraction Source Crop & Spatial Coordinates */}
                      {data.found && (
                        <div className="mt-1 pt-2 border-t border-slate-100 flex items-center justify-between gap-4">
                          <div className="flex items-center gap-3">
                            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
                              Source Crop:
                            </span>
                            <div
                              className="bg-slate-100 border border-slate-200 rounded p-1 max-h-12 max-w-[240px] overflow-hidden flex items-center justify-center cursor-pointer hover:border-blue-400 hover:shadow-sm transition-all"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (setZoomedImage && activeJob.job_id) {
                                  setZoomedImage(evidenceService.imageUrl(activeJob.job_id, key));
                                }
                              }}
                              title="Click to zoom source crop"
                            >
                              <img
                                src={evidenceService.imageUrl(activeJob.job_id, key)}
                                alt={`Crop for ${key}`}
                                className="h-9 object-contain mix-blend-multiply"
                                onError={(e) => {
                                  e.target.style.display = 'none';
                                }}
                              />
                            </div>
                          </div>

                          {hasBbox && (
                            <div className="flex items-center gap-1 text-[11px] font-mono text-slate-500 bg-slate-50 px-2.5 py-1 rounded border border-slate-200">
                              <Crosshair size={12} className="text-slate-400" />
                              <span>({Math.round(data.bbox.x1)}, {Math.round(data.bbox.y1)}) - ({Math.round(data.bbox.x2)}, {Math.round(data.bbox.y2)})</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Geometry Memory Tab */}
          {activeTab === 'geometry' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-xs text-blue-900">
                <div className="flex items-center gap-2 font-bold mb-1">
                  <Database size={16} className="text-blue-600" />
                  Autonomous Anchor Geometry Store
                </div>
                <p>
                  These bounding boxes and anchor points are cached in the template schema. Next time a document matching this layout is ingested, it bypasses full VLM inference and extracts fields in under 300ms.
                </p>
              </div>

              {learnedAnchors ? (
                <div className="space-y-3 font-mono text-xs">
                  <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                    <div className="text-[10px] uppercase font-bold text-slate-400 mb-1">Reference Canvas</div>
                    <div>Width: {learnedAnchors.page_width}px | Height: {learnedAnchors.page_height}px</div>
                  </div>

                  {learnedAnchors.anchor_text && (
                    <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                      <div className="text-[10px] uppercase font-bold text-slate-400 mb-1">Alignment Anchor Header</div>
                      <div className="text-emerald-700 font-bold">"{learnedAnchors.anchor_text}"</div>
                    </div>
                  )}

                  <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                    <div className="text-[10px] uppercase font-bold text-slate-400 mb-2">Mapped Field Anchors</div>
                    <div className="space-y-1.5">
                      {Object.entries(learnedAnchors.fields || {}).map(([fKey, fVal]) => (
                        <div key={fKey} className="flex items-center justify-between text-[11px] border-b border-slate-100 pb-1">
                          <span className="font-bold text-slate-700">{fKey}</span>
                          <span className="text-slate-500">
                            [{fVal.bbox?.x1}, {fVal.bbox?.y1}, {fVal.bbox?.x2}, {fVal.bbox?.y2}]
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-center p-8 text-slate-400 italic">No learned anchors recorded for this template yet.</div>
              )}
            </div>
          )}

          {/* Raw JSON Tab */}
          {activeTab === 'json' && (
            <div className="flex-1 overflow-auto p-4 bg-[#1e2227]">
              <JsonView data={parsedResult} shouldExpandNode={allExpanded} style={darkStyles} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default FastExtractionResultView;
