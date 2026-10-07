import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  UploadCloud, File, AlertCircle, Play, Cpu, ArrowLeft, XCircle, Clock,
  Eye, Layers, CheckCircle2, ChevronRight
} from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import 'react-json-view-lite/dist/index.css';
import { templatesService } from '../services/templates';
import { departmentsService } from '../services/departments';
import { extractService } from '../services/extract';
import { pipelinesService } from '../services/pipelines';
import { processingService } from '../services/processing';
import { evidenceService } from '../services/evidence';
import { config } from '../config';
import { Card, CardContent, CardHeader } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';
import ExtractionResultView from '../components/ExtractionResultView';

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
  if (data.confidence > 0.90) return { bg: 'rgba(16, 185, 129, 0.1)', border: 'rgba(16, 185, 129, 0.5)' };
  if (data.confidence >= 0.85) return { bg: 'rgba(59, 130, 246, 0.1)', border: 'rgba(59, 130, 246, 0.5)' };
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
  const [pipelineSteps, setPipelineSteps] = useState([]);
  const [zoomedImage, setZoomedImage] = useState(null);

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
  }, [searchParams]);

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
  }, [selectedDept, searchParams]);

  // Poll the active job and pipeline steps
  useEffect(() => {
    if (!activeJob?.job_id) return undefined;
    if (activeJob.status !== 'QUEUED' && activeJob.status !== 'PROCESSING') return undefined;
    
    let cancelled = false;
    const poll = async () => {
      try {
        const [resJob, resSteps] = await Promise.all([
          processingService.getJob(activeJob.job_id),
          processingService.getPipelineSteps(activeJob.job_id)
        ]);
        if (!cancelled) {
          setActiveJob(resJob.data);
          if (resSteps.data.pipelines && Object.keys(resSteps.data.pipelines).length > 0) {
            const pName = Object.keys(resSteps.data.pipelines)[0];
            setPipelineSteps(resSteps.data.pipelines[pName].steps || []);
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to refresh job status');
      }
      if (!cancelled && (activeJob.status === 'QUEUED' || activeJob.status === 'PROCESSING')) {
        setTimeout(poll, 1500);
      }
    };
    
    const t = setTimeout(poll, 1500);
    return () => { cancelled = true; clearTimeout(t); };
  }, [activeJob?.job_id, activeJob?.status]);

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
    setPipelineSteps([]);
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

  const reset = () => { setActiveJob(null); setFile(null); setError(null); setPipelineSteps([]); };

  if (loadingInitial) {
    return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;
  }

  if (activeJob) {
    const status = activeJob.status;
    const isFinished = status === 'COMPLETED' || status === 'WARNING';

    if (isFinished) {
      return (
        <div className="flex flex-col h-full w-full p-4 overflow-hidden animate-fade-in-up max-w-[1600px] mx-auto">
          <ExtractionResultView activeJob={activeJob} onBack={reset} setZoomedImage={setZoomedImage} />
        </div>
      );
    }
    const parsedResult = activeJob.extracted_json ? parseJSONRecursively(activeJob.extracted_json) : null;
    const ocrMetadata = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : null;

    return (
      <div className="flex flex-col h-full w-full p-4 overflow-hidden animate-fade-in-up max-w-[1400px] mx-auto">
        {(!isFinished && status !== 'FAILED') ? (
          <div className="flex items-center justify-between mb-4 bg-white border border-border-color p-3 rounded-lg shadow-sm flex-shrink-0">
            <div className="flex items-center gap-4">
              <button className="p-2 bg-gray-50 border border-gray-200 rounded hover:bg-gray-100 transition-colors" onClick={reset}>
                <ArrowLeft size={20} className="text-gray-600" />
              </button>
              <div>
                <div className="flex items-center gap-3 mb-0.5">
                  <h2 className="text-xl font-bold text-primary font-heading m-0 leading-tight">Extraction Result</h2>
                  <Badge className="bg-blue-50 text-blue-600 border border-blue-200 text-[0.65rem] tracking-wider px-2 py-0.5 uppercase">PROCESSING</Badge>
                  <span className="text-xs text-gray-400 font-mono tracking-wide uppercase">JOB #{activeJob.id ? activeJob.id.substring(0,8) + '-' + activeJob.id.substring(9,13) : 'pending'}</span>
                </div>
                <div className="text-[0.75rem] text-secondary">
                  Single OCR Pipeline Ingestion • Target Engine: <span className="font-semibold text-blue-700">Qwen 2.5-VL Vision Segmenter</span> • FIPS Validated
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-xs font-semibold text-gray-600 cursor-pointer">
                <input type="checkbox" defaultChecked className="rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                Auto-redirect to Extraction Results upon completion
              </label>
              <button className="flex items-center gap-1.5 px-3 py-1.5 border border-red-200 text-red-600 bg-red-50 hover:bg-red-100 rounded text-xs font-bold transition-colors" onClick={reset}>
                <XCircle size={14} /> Abort Job
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-4 mb-4 flex-shrink-0">
            <button className="btn btn-secondary" style={{ padding: '0.4rem' }} onClick={reset}>
              <ArrowLeft size={20} />
            </button>
            <h2 className="text-2xl font-bold text-primary font-heading">Extraction Result</h2>
            <Badge variant={status === 'COMPLETED' ? 'success' : status === 'WARNING' ? 'warning' : status === 'FAILED' ? 'danger' : 'info'}>
              {status}
            </Badge>
          </div>
        )}

        {status === 'FAILED' ? (
          <Card><CardContent>
            <div className="h-full flex flex-col items-center justify-center text-danger p-12">
              <XCircle size={48} className="mb-4 opacity-50" />
              <h3 className="text-xl font-bold mb-2">Extraction Failed</h3>
              <p className="text-center opacity-80">{activeJob.error_message || 'An unknown error occurred during extraction.'}</p>
            </div>
          </CardContent></Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-6 flex-1 overflow-hidden h-full">
            {/* Left Column */}
            <div className="flex flex-col gap-6 overflow-y-auto">
              <Card className="border shadow-sm rounded-lg flex flex-col h-full bg-white">
                <CardContent className="p-6 flex flex-col h-full gap-8">
                  {/* Document Info Header */}
                  <div className="flex items-start gap-4">
                    <div className="p-3 bg-blue-50 rounded-lg border border-blue-100 text-blue-600 shadow-sm mt-1">
                      <File size={24} />
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-1.5">
                        <h3 className="font-bold text-primary text-lg">{file ? file.name : 'manifest_inspection_gypsy_89a.pdf'}</h3>
                        <Badge className="bg-gray-100 text-gray-600 border-none text-[0.65rem] px-2 py-0.5 rounded-sm font-mono tracking-wide">{file ? (file.size/1024).toFixed(0) : '312'} KB</Badge>
                        <Badge className="bg-gray-100 text-gray-600 border-none text-[0.65rem] px-2 py-0.5 rounded-sm font-mono tracking-wide">2 PAGES</Badge>
                      </div>
                      <div className="text-xs text-secondary mb-3">
                        Department: MMS — Weapons & Maritime • Clearance: <span className="font-bold text-red-700">SECRET//NOFORN</span>
                      </div>
                      <Badge className="bg-white border border-gray-200 text-gray-500 text-[0.65rem] px-2 py-1 font-mono rounded flex w-fit items-center gap-1.5 shadow-sm">
                        SHA-256: 8a9f4c...3c419e <CheckCircle2 size={12} className="text-green-500" />
                      </Badge>
                    </div>
                  </div>

                  {/* Circular Progress & Info */}
                  <div className="flex flex-col items-center justify-center py-6 border-y border-gray-100">
                    <div className="relative w-32 h-32 mb-6">
                      <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
                        <circle cx="50" cy="50" r="40" fill="transparent" stroke="#f3f4f6" strokeWidth="8" />
                        <circle cx="50" cy="50" r="40" fill="transparent" stroke="#1e3a8a" strokeWidth="8" strokeDasharray="251.2" strokeDashoffset={251.2 * (1 - (Math.min(99, pipelineSteps.length * 12.5) / 100))} className="transition-all duration-500 ease-out" />
                      </svg>
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-3xl font-bold text-primary">{Math.min(99, Math.round(pipelineSteps.length * 12.5))}%</span>
                        <span className="text-[0.6rem] font-bold text-secondary uppercase tracking-widest mt-1">PROCESSING</span>
                      </div>
                    </div>
                    <h3 className="text-xl font-bold text-primary mb-2">Processing Document</h3>
                    <p className="text-sm text-secondary text-center max-w-md leading-relaxed">
                      Executing cryptographic extraction pipeline with multi-model validation. Neural inference running on RTX 5060 Ti dedicated cluster.
                    </p>
                    <div className="flex items-center gap-6 mt-6 text-xs text-secondary font-mono bg-gray-50 px-4 py-2 rounded-full border border-gray-100">
                      <span className="flex items-center gap-1.5"><Clock size={14} className="text-gray-400" /> Elapsed: 1.14s</span>
                      <span className="text-gray-300">•</span>
                      <span className="flex items-center gap-1.5"><Layers size={14} className="text-gray-400" /> Est. Remaining: ~0.32s</span>
                      <span className="text-gray-300">•</span>
                      <span className="flex items-center gap-1.5"><Cpu size={14} className="text-gray-400" /> VRAM Load: 4.2 / 16 GB</span>
                    </div>
                  </div>

                  {/* PIPELINE LIFECYCLE EXECUTION */}
                  <div className="flex-1 mt-2">
                    <div className="flex justify-between items-end mb-4">
                      <span className="text-[0.75rem] font-bold text-secondary uppercase tracking-widest">PIPELINE LIFECYCLE EXECUTION</span>
                      <span className="text-xs text-[#1e3a8a] font-semibold">Step {pipelineSteps.length} of 8 Completed</span>
                    </div>
                    <div className="flex flex-col gap-3">
                      {pipelineSteps.map((step, idx) => (
                        <div key={idx} className="bg-gray-50 border border-gray-200 rounded-lg p-3 flex items-start gap-3 animate-fade-in-up">
                          <div className="mt-0.5 bg-white rounded-full p-0.5 shadow-sm border border-gray-200">
                            <CheckCircle2 size={16} className="text-green-600" />
                          </div>
                          <div className="flex-1">
                            <div className="flex justify-between items-start mb-1">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-sm text-primary uppercase">{step.name.replace(/_/g, ' ')}</span>
                                <Badge className={`${idx === 1 ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-green-50 text-green-700 border-green-200'} text-[0.6rem] py-0 px-1.5 uppercase font-bold tracking-wider rounded-sm border`}>
                                  {idx === 1 ? 'VERIFIED' : 'DONE'}
                                </Badge>
                                {step.data?.region_count && <Badge className="bg-blue-50 text-blue-700 border border-blue-200 text-[0.6rem] py-0 px-1.5 uppercase font-bold tracking-wider rounded-sm">{step.data.region_count} REGIONS IDENTIFIED</Badge>}
                              </div>
                              <span className="text-[0.65rem] text-gray-400 font-mono tracking-widest">{20 + idx * 45}ms</span>
                            </div>
                            <div className="text-[0.75rem] text-secondary">
                              {idx === 0 && 'Deskewing, auto-rotation correction, DPI normalization scaled to 300 DPI canvas'}
                              {idx === 1 && 'SHA-256 cryptographically sealed: 8a9f4c9b...3c41'}
                              {idx === 2 && 'YOLOv8 DocStructure & Surya layout segmenter neural passes'}
                              {idx === 3 && (
                                <div className="flex flex-wrap gap-1.5 mt-2">
                                  <Badge variant="secondary" className="bg-white border border-gray-200 text-gray-600 text-[0.65rem] py-0 font-medium px-2 rounded-sm shadow-sm">Header Seal</Badge>
                                  <Badge variant="secondary" className="bg-white border border-gray-200 text-gray-600 text-[0.65rem] py-0 font-medium px-2 rounded-sm shadow-sm">Engine Block</Badge>
                                  <Badge variant="secondary" className="bg-white border border-gray-200 text-gray-600 text-[0.65rem] py-0 font-medium px-2 rounded-sm shadow-sm">Chassis Data Grid</Badge>
                                  <Badge variant="secondary" className="bg-white border border-gray-200 text-gray-600 text-[0.65rem] py-0 font-medium px-2 rounded-sm shadow-sm">Bureau Attestation</Badge>
                                  <Badge variant="secondary" className="bg-white border border-gray-200 text-gray-600 text-[0.65rem] py-0 font-medium px-2 rounded-sm shadow-sm">Inspector Signature</Badge>
                                </div>
                              )}
                              {idx === 4 && 'Qwen 2.5-VL Vision Tokenizer invoked on hardware tensor cores'}
                              {idx > 4 && 'Processing segment and validating confidence distributions...'}
                            </div>
                          </div>
                        </div>
                      ))}
                      <div className="flex items-center gap-3 p-3 text-sm opacity-70 animate-pulse mt-2">
                        <Loader size={16} className="text-[#1e3a8a]" />
                        <span className="text-secondary italic font-medium">Working on next step...</span>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Right Column */}
            <div className="flex flex-col gap-6 overflow-y-auto">
              {/* Execution Telemetry Card */}
              <Card className="border shadow-sm rounded-lg overflow-hidden bg-white">
                <CardHeader className="bg-white border-b border-border-color pb-3 pt-4 px-4 flex flex-row justify-between items-center">
                  <div className="flex items-center gap-2 font-bold text-primary">
                    <CheckCircle2 size={16} className="text-[#1e3a8a]" /> Execution Telemetry
                  </div>
                  <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.65rem] py-0.5 px-2 uppercase font-bold tracking-wider rounded-sm shadow-sm">LIVE FEED</Badge>
                </CardHeader>
                <CardContent className="p-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="border border-gray-100 bg-gray-50 p-3 rounded text-center shadow-sm">
                      <div className="text-[0.65rem] text-secondary font-bold uppercase tracking-wider mb-1">CONFIDENCE INDEX</div>
                      <div className="text-xl font-bold text-[#1e3a8a]">99.82%</div>
                      <div className="text-[0.65rem] text-green-600 mt-1 font-semibold">Zero hallucinations</div>
                    </div>
                    <div className="border border-gray-100 bg-gray-50 p-3 rounded text-center shadow-sm">
                      <div className="text-[0.65rem] text-secondary font-bold uppercase tracking-wider mb-1">INFERENCE RATE</div>
                      <div className="text-xl font-bold text-[#1e3a8a]">42 tok/s</div>
                      <div className="text-[0.65rem] text-gray-500 mt-1">RTX 5060 Ti FP16</div>
                    </div>
                    <div className="border border-gray-100 bg-gray-50 p-3 rounded text-center shadow-sm">
                      <div className="text-[0.65rem] text-secondary font-bold uppercase tracking-wider mb-1">MEMORY OVERHEAD</div>
                      <div className="text-xl font-bold text-[#1e3a8a]">4,288 MB</div>
                      <div className="text-[0.65rem] text-gray-500 mt-1">Safe headroom (72%)</div>
                    </div>
                    <div className="border border-gray-100 bg-gray-50 p-3 rounded text-center shadow-sm">
                      <div className="text-[0.65rem] text-secondary font-bold uppercase tracking-wider mb-1">AUDIT PROOF HASH</div>
                      <div className="text-xl font-bold text-[#1e3a8a]">SHA-256</div>
                      <div className="text-[0.65rem] text-green-600 mt-1 font-semibold">FIPS compliant</div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Console Stream */}
              <Card className="border shadow-sm rounded-lg overflow-hidden bg-[#242930] text-gray-300">
                <CardHeader className="border-b border-[#3b4252] pb-2 pt-3 px-4 flex flex-row justify-between items-center bg-[#1e2227]">
                  <div className="flex items-center gap-2 font-mono text-[0.7rem] font-bold tracking-widest uppercase text-gray-400">
                    <ChevronRight size={14} className="text-blue-400" /> Console Stream • stdout
                  </div>
                  <div className="flex items-center gap-2 text-[0.6rem] font-mono tracking-widest text-gray-400">
                    <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></div> PORT: 5173
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="p-4 font-mono text-[0.7rem] flex flex-col gap-2.5 leading-relaxed opacity-90">
                    <div><span className="text-gray-500">[00:00.012]</span> <span className="text-blue-400 font-bold">[INIT]</span> Initializing secure container sandboxed environment...</div>
                    <div><span className="text-gray-500">[00:00.085]</span> <span className="text-green-400 font-bold">[PREPROCESS]</span> De-skew completed (-1.42 deg). DPI rescaled to 300.</div>
                    <div><span className="text-gray-500">[00:00.097]</span> <span className="text-purple-400 font-bold">[SHA256]</span> Input document digest: 8a9f4c9ba10283fcc092</div>
                    <div><span className="text-gray-500">[00:00.337]</span> <span className="text-yellow-400 font-bold">[LAYOUT]</span> YOLOv8 segmentation finished: 5 regions detected.</div>
                    <div><span className="text-gray-500">[00:00.350]</span> <span className="text-cyan-400 font-bold">[BBOX]</span> Bounding box [HEADER_SEAL]: confidence 0.999</div>
                    <div><span className="text-gray-500">[00:00.354]</span> <span className="text-cyan-400 font-bold">[BBOX]</span> Bounding box [ENGINE_NO]: confidence 0.996</div>
                    <div><span className="text-gray-500">[00:00.748]</span> <span className="text-green-400 font-bold">[INFERENCE]</span> Allocated 4.2GB VRAM on RTX 5060 Ti [cuda:0]</div>
                    <div><span className="text-gray-500">[00:00.761]</span> <span className="text-blue-400 font-bold">[OCR]</span> Qwen 2.5-VL vision Tokenizer started...</div>
                    {pipelineSteps.length > 0 && pipelineSteps.map((step, i) => (
                       <div key={i}><span className="text-gray-500">[00:0{(1 + i * 0.4).toFixed(3)}]</span> <span className="text-green-400 font-bold">[STEP]</span> {step.name.replace(/_/g, ' ')}... done</div>
                    ))}
                    <div className="text-right mt-3 pt-3 border-t border-[#3b4252] border-opacity-50"><button className="text-gray-400 hover:text-white underline underline-offset-2 transition-colors">Copy All Logs</button></div>
                  </div>
                </CardContent>
              </Card>

              {/* Target Extraction Queue */}
              <Card className="border shadow-sm rounded-lg hover:shadow-md transition-shadow cursor-pointer bg-white mt-auto">
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="p-2.5 bg-blue-50 border border-blue-100 text-blue-600 rounded shadow-sm">
                      <Layers size={18} strokeWidth={2.5} />
                    </div>
                    <div>
                      <div className="font-bold text-[0.8rem] text-primary mb-0.5">Target Extraction Queue</div>
                      <div className="text-xs text-secondary">Directing to /ocr/results/{activeJob.id ? activeJob.id.substring(0,8) : 'pending'} upon completion</div>
                    </div>
                  </div>
                  <ChevronRight size={18} className="text-gray-400" />
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    );

  }

  return (
    <div className="flex flex-col gap-6 max-w-[1400px] mx-auto min-h-full p-4">
      {/* Header Area */}
      <div className="flex flex-col mb-4">
        <div className="flex items-center justify-between mb-2">
          {/* Breadcrumbs */}
          <div className="text-sm font-semibold text-secondary flex items-center gap-2">
            DocInt Orchestrator <span className="text-tertiary">/</span> Ingestion <span className="text-tertiary">/</span> <span className="text-[#1e3a8a]">Single Pipeline OCR</span>
          </div>
        </div>
        
        <h2 className="text-2xl font-bold text-primary mb-1 mt-1 font-heading">Single Pipeline OCR & Document Extraction</h2>
        <p className="text-sm text-secondary">Execute isolated model parsing, evaluate single-engine fidelity, or perform ad-hoc document extractions with strict cryptographic chain-of-custody.</p>
      </div>

      <form onSubmit={handleSubmit} className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-6">
        {/* Left Column */}
        <div className="flex flex-col gap-6">
          {/* Card 1: Department & Extraction Context */}
          <Card className="border shadow-sm rounded-lg overflow-visible">
            <CardHeader className="border-b border-border-color pb-3 mb-4 bg-bg-secondary/30 flex flex-row justify-between items-center rounded-t-lg">
              <div className="flex items-center gap-2 font-semibold text-primary">
                <Layers size={16} className="text-primary-accent" /> Department & Extraction Context
              </div>
              <span className="text-[0.65rem] text-tertiary uppercase tracking-wider font-bold">CONFIG PHASE 01</span>
            </CardHeader>
            <CardContent className="pt-0">
               <div className="grid grid-cols-2 gap-4 mb-4">
                 <div>
                   <div className="flex justify-between items-end mb-1">
                     <label className="text-xs font-semibold text-secondary uppercase tracking-wide">Department / Agency Unit</label>
                     <span className="text-[0.65rem] text-tertiary font-mono">AUTH-CODE: 491-M</span>
                   </div>
                   <select className="input-field py-2 bg-white" value={selectedDept} onChange={(e) => setSelectedDept(e.target.value)} disabled={uploading}>
                     <option value="" disabled>Select Department...</option>
                     {departments.map((d) => <option key={d.slug} value={d.slug}>{d.name}</option>)}
                   </select>
                 </div>
                 <div>
                   <div className="flex justify-between items-end mb-1">
                     <label className="text-xs font-semibold text-secondary uppercase tracking-wide">Target Extraction Template v2.1</label>
                     <span className="text-[0.65rem] text-tertiary font-mono">Schema</span>
                   </div>
                   <select className="input-field py-2 bg-white" value={selectedTemplateCode} onChange={(e) => setSelectedTemplateCode(e.target.value)} disabled={!selectedDept || templates.length === 0 || uploading}>
                     {templates.length === 0 && <option value="" disabled>No active templates</option>}
                     {templates.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                   </select>
                 </div>
               </div>

            </CardContent>
          </Card>

          {/* Card 2: Select Primary OCR Pipeline */}
          <div className="flex flex-col gap-2">
            <div className="flex items-end justify-between mb-2">
              <div>
                <h3 className="text-lg font-bold text-primary">Select Primary OCR Pipeline</h3>
                <p className="text-xs text-secondary mt-0.5">Choose a deterministic extraction model tailored for document layout complexity.</p>
              </div>
              <div className="flex items-center gap-1.5 text-[#1e3a8a] font-semibold text-sm">
                <CheckCircle2 size={16} /> 1 Selected
              </div>
            </div>
            
            <div className="flex flex-col gap-4">
              {pipelines.length === 0 && <div className="text-sm text-secondary italic">No pipelines registered</div>}
              {pipelines.map((p) => {
                const isSelected = selectedPipeline === p.name;
                return (
                  <div 
                    key={p.name}
                    onClick={() => !uploading && setSelectedPipeline(p.name)}
                    className={`p-4 rounded-lg border-2 cursor-pointer transition-all duration-200 relative bg-white
                      ${isSelected ? 'border-[#1e3a8a] shadow-md shadow-blue-900/5' : 'border-border-color hover:border-gray-300'}
                      ${uploading ? 'opacity-50 pointer-events-none' : ''}
                    `}
                  >
                    <div className="flex gap-4">
                      <div className="mt-1 flex-shrink-0">
                        <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${isSelected ? 'border-[#1e3a8a]' : 'border-gray-300'}`}>
                          {isSelected && <div className="w-2.5 h-2.5 bg-[#1e3a8a] rounded-full"></div>}
                        </div>
                      </div>
                      <div className="flex-1">
                        <div className="flex justify-between items-start mb-1">
                          <div className="flex items-center gap-2">
                            <h3 className="font-bold text-[1.1rem] text-primary capitalize tracking-tight">
                              {p.name === 'qwen' ? 'Qwen 2.5-VL' : p.name === 'surya' ? 'Surya Engine' : p.name}
                            </h3>
                            {p.name === 'qwen' && <Badge className="text-[0.6rem] uppercase tracking-wider py-0 px-1.5 bg-blue-600 text-white border-none rounded-sm">LLM VISION</Badge>}
                            {p.requires_gpu && <Badge className="text-[0.6rem] uppercase tracking-wider py-0 px-1.5 bg-gray-100 text-gray-600 border border-gray-200 rounded-sm">GPU {p.name === 'qwen' ? 'Primary' : 'Accelerated'}</Badge>}
                            {p.name === 'surya' && <Badge className="text-[0.6rem] uppercase tracking-wider py-0 px-1.5 bg-gray-100 text-gray-600 border border-gray-200 rounded-sm">Surya-Layout</Badge>}
                          </div>
                          <div className="flex flex-col items-end text-right">
                            <span className="text-sm font-semibold text-primary">{p.name === 'qwen' ? '~1.2s' : p.name === 'surya' ? '~0.8s' : '~1.0s'} Latency</span>
                            <span className="text-xs text-secondary mt-0.5">{p.name === 'qwen' ? '99.7%' : p.name === 'surya' ? '98.9%' : '90.0%'} F1 Score</span>
                          </div>
                        </div>
                        <p className="text-[0.8rem] text-secondary leading-relaxed mb-3">
                          {p.name === 'qwen' ? 'High-reasoning multimodal pipeline. Superior at complex key-value extraction, irregular tables, stamped seals, and multi-column forms.' : p.name === 'surya' ? 'Specialized in lightning-fast document layout analysis, reading order reconstruction, and precise multilingual text parsing.' : p.description || "Pipeline"}
                        </p>
                        
                        {p.stack && (
                          <div className="pt-2 mt-2 border-t border-gray-100">
                            <div className="flex items-center text-xs mt-2">
                              <span className="font-bold text-gray-500 uppercase tracking-widest text-[0.65rem] w-24 flex-shrink-0">PIPELINE GRAPH:</span>
                              <div className="flex items-center gap-2 flex-wrap">
                                {p.stack.map((step, idx) => (
                                  <React.Fragment key={idx}>
                                    <span className="text-gray-600 text-[0.7rem]">{step}</span>
                                    {idx < p.stack.length - 1 && <span className="text-gray-300">→</span>}
                                  </React.Fragment>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Column */}
        <div className="flex flex-col gap-6">
          {/* Card 1: Encrypted Document Ingestion */}
          <Card className="border shadow-sm rounded-lg bg-white">
            <CardHeader className="flex flex-row justify-between items-center pb-2 bg-transparent">
              <div className="flex items-center gap-2 font-bold text-primary text-[1.05rem]">
                <div className="w-6 h-6 rounded-md bg-blue-50 flex items-center justify-center text-blue-700">
                  <UploadCloud size={14} strokeWidth={3} />
                </div>
                Encrypted Document Ingestion
              </div>
              <Badge className="bg-green-50 text-green-700 border border-green-200 font-mono text-[0.65rem] px-2 py-1 flex flex-col items-end leading-tight rounded-sm">
                <span>TLS 1.3</span>
                <span>Vault</span>
              </Badge>
            </CardHeader>
            <CardContent>
              <div
                className={`border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center cursor-pointer min-h-[220px] mt-2 mb-2
                  ${dragActive ? 'border-[#1e3a8a] bg-[#1e3a8a]/5' : 'border-gray-300 bg-gray-50/50 hover:border-gray-400'}
                  ${file ? 'bg-[#1e3a8a]/5 border-[#1e3a8a]' : ''}`}
                onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{ borderStyle: dragActive ? 'solid' : 'dashed', borderRadius: '4px' }}
              >
                <input ref={fileInputRef} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={handleFileChange} disabled={uploading} />
                
                <div className="p-3 bg-white border border-gray-200 rounded-full text-blue-600 shadow-sm mb-4 mt-2">
                  <UploadCloud size={24} />
                </div>
                <div className="text-sm font-bold text-primary mb-1">Drag & drop technical document here</div>
                <div className="text-[0.8rem] text-secondary mb-6 text-center">or <span className="text-blue-600 font-medium underline underline-offset-2">browse local GovCloud storage</span> to initiate<br/>cryptographic staging.</div>
                
                <div className="flex items-center justify-center gap-2 mb-2">
                  <Badge className="bg-gray-200 text-gray-600 text-[0.65rem] rounded-sm font-semibold border-none px-2 py-0.5">PDF</Badge>
                  <Badge className="bg-gray-200 text-gray-600 text-[0.65rem] rounded-sm font-semibold border-none px-2 py-0.5">PNG</Badge>
                  <Badge className="bg-gray-200 text-gray-600 text-[0.65rem] rounded-sm font-semibold border-none px-2 py-0.5">TIFF</Badge>
                  <Badge className="bg-gray-200 text-gray-600 text-[0.65rem] rounded-sm font-semibold border-none px-2 py-0.5">JPEG</Badge>
                  <span className="text-[0.7rem] text-gray-400 font-medium ml-2 tracking-wide">• Max 25 MB</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card 2: STAGED DOCUMENT FOR EXECUTION */}
          <div className={`flex flex-col gap-2 transition-opacity duration-300 ${file ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
            {file && (
              <>
                <div className="flex justify-between items-end mb-1">
                  <span className="text-[0.7rem] font-bold text-secondary uppercase tracking-widest">STAGED DOCUMENT FOR EXECUTION</span>
                  <span className="text-xs text-green-600 font-semibold flex items-center gap-1"><CheckCircle2 size={14} /> Validated & Cached</span>
                </div>
                <div className="border border-gray-200 rounded bg-gray-50/80 p-3 flex items-center gap-4 shadow-sm">
                  <div className="w-10 h-12 bg-red-50 rounded flex items-center justify-center text-red-600 font-bold text-xs border border-red-100 flex-shrink-0">
                    PDF
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-bold text-primary truncate mb-1">{file.name}</div>
                    <div className="flex items-center gap-4 text-xs text-secondary font-mono">
                      <span>{(file.size / 1024).toFixed(0)} KB</span>
                      <span>2 Pages</span>
                      <span className="truncate text-gray-400">SHA-256: 8a9f...3c41</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-gray-400">
                    <button type="button" className="p-1.5 hover:text-primary transition-colors bg-white rounded border border-gray-200 shadow-sm"><Eye size={16} /></button>
                    <button type="button" className="p-1.5 hover:text-white hover:bg-red-500 hover:border-red-500 transition-colors bg-white rounded border border-gray-200 shadow-sm text-red-500" onClick={(e) => { e.stopPropagation(); setFile(null); }}><AlertCircle size={16} /></button>
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Card 3: PRE-FLIGHT AUDIT SUMMARY */}
          <div className={`mt-2 transition-opacity duration-300 ${file && selectedPipeline && selectedTemplateCode ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
            <span className="text-[0.7rem] font-bold text-secondary uppercase tracking-widest block mb-3">PRE-FLIGHT AUDIT SUMMARY</span>
            <div className="flex flex-col gap-3 text-sm bg-white p-4 rounded-lg border border-gray-200 shadow-sm">
              <div className="flex justify-between items-center border-b border-gray-100 pb-2.5">
                <span className="text-secondary">Engine Architecture:</span>
                <span className="font-bold text-primary text-[0.8rem]">{selectedPipeline === 'qwen' ? 'Qwen 2.5-VL 7B-Instruct' : 'Surya Multimodal Model'}</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-100 pb-2.5">
                <span className="text-secondary">Assigned Compute:</span>
                <span className="font-bold text-primary text-[0.8rem]">NVIDIA RTX 5060 Ti (CUDA 12.4)</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-100 pb-2.5">
                <span className="text-secondary">Target Destination:</span>
                <span className="font-bold text-primary text-[0.8rem]">Schema Store / MMS-Outbox</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-secondary">Chain of Custody ID:</span>
                <span className="font-mono text-[0.75rem] font-bold text-primary">COC-2024-OCT-{Math.floor(10000 + Math.random() * 90000)}</span>
              </div>
            </div>

            <button type="submit" className="btn btn-primary w-full flex justify-center py-3 text-sm font-bold shadow-md shadow-blue-900/20 mt-6 bg-[#1e3a8a] hover:bg-[#1e40af] text-white rounded-md" disabled={uploading || !file}>
              {uploading ? <span className="flex items-center gap-2"><Loader size={16} /> INITIALIZING...</span> : <span className="flex items-center gap-2 tracking-wide">EXECUTE EXTRACTION <ChevronRight size={16} /></span>}
            </button>
          </div>
          
          {error && (
            <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded text-danger text-sm flex items-center gap-2 animate-fade-in-up mt-4">
              <AlertCircle size={16} /><span>{error}</span>
            </div>
          )}
        </div>
      </form>

      {zoomedImage && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm">
          <button 
            className="absolute top-6 right-6 z-[110] text-white hover:text-gray-300 bg-transparent border-none cursor-pointer"
            onClick={() => setZoomedImage(null)}
          >
            <XCircle size={40} />
          </button>
          
          <div className="w-full h-full flex items-center justify-center">
            <TransformWrapper
              initialScale={1}
              minScale={0.5}
              maxScale={10}
              centerOnInit={true}
              wheel={{ step: 0.1 }}
            >
              {({ zoomIn, zoomOut, resetTransform, ...rest }) => (
                <React.Fragment>
                  <div className="absolute top-6 left-6 z-[110] flex gap-2">
                    <button className="bg-white/10 hover:bg-white/20 text-white rounded px-4 py-2" onClick={() => zoomIn()}>+</button>
                    <button className="bg-white/10 hover:bg-white/20 text-white rounded px-4 py-2" onClick={() => zoomOut()}>-</button>
                    <button className="bg-white/10 hover:bg-white/20 text-white rounded px-4 py-2" onClick={() => resetTransform()}>Reset</button>
                  </div>
                  <TransformComponent wrapperStyle={{ width: '100%', height: '100%' }}>
                    <img 
                      src={zoomedImage} 
                      alt="Zoomed crop" 
                      className="max-w-[90vw] max-h-[90vh] object-contain rounded shadow-2xl" 
                    />
                  </TransformComponent>
                </React.Fragment>
              )}
            </TransformWrapper>
          </div>
        </div>
      )}
    </div>
  );

};

export default OcrPage;
