import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, File, AlertCircle, RefreshCw, List, ShieldCheck, FileText, CheckCircle2, Zap, Save, CheckSquare, Square, Eye, Trash2, Code } from 'lucide-react';
import { templatesService } from '../services/templates';
import { departmentsService } from '../services/departments';
import { extractService } from '../services/extract';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent } from '../components/ui/Card';
import { Loader } from '../components/ui/Loader';
import { Badge } from '../components/ui/Badge';

const PIPELINE_META = {
  'qwen2.5vl': { name: 'Qwen 2.5-VL', badges: ['LLM Vision', 'GPU Primary'], status: 'High Confidence', statusColor: 'text-success', desc: 'End-to-end multimodal key-value extraction directly from raster coordinates.' },
  'surya': { name: 'Surya Engine', badges: ['GPU Accel', 'High Precision'], status: 'Layout Sovereign', statusColor: 'text-success', desc: 'Surya Layout Detection + TrOCR Fine-Tuned + TATR Tables.' },
  'paddle': { name: 'PaddleOCR', badges: ['PP-Structure', 'Tabular Master'], status: 'High Speed', statusColor: 'text-success', desc: 'PPStructure Layout → Paddle Recognition → Structured LLM Fallback.' },
  'tesseract': { name: 'Tesseract OCR', badges: ['CPU Cluster', 'Standard Fallback'], status: 'Deterministic Baseline', statusColor: 'text-secondary', desc: 'PPStructure OCR Pipeline → Tesseract v5 → Img2Table Extraction.' }
};

const OrchestrationPage = () => {
  const navigate = useNavigate();

  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [pipelines, setPipelines] = useState([]);
  const [loadingInitial, setLoadingInitial] = useState(true);

  const [selectedDept, setSelectedDept] = useState('');
  const [selectedTemplateCode, setSelectedTemplateCode] = useState('');
  const [selectedPipelines, setSelectedPipelines] = useState([]);
  const [file, setFile] = useState(null);

  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const fileInputRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const [deptRes, pipelinesRes] = await Promise.all([
          departmentsService.list(),
          pipelinesService.list(),
        ]);
        const activeDepts = deptRes.data.filter((d) => d.is_active);
        setDepartments(activeDepts);
        if (activeDepts.length > 0) setSelectedDept(activeDepts[0].slug);

        const availablePipelines = pipelinesRes.data.pipelines || [];
        setPipelines(availablePipelines);
        setSelectedPipelines(availablePipelines.map((p) => p.name));
      } catch (err) {
        setError('Failed to load form data. ' + err.message);
      } finally {
        setLoadingInitial(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedDept) { setTemplates([]); setSelectedTemplateCode(''); return; }
    templatesService.listTemplates(selectedDept).then((res) => {
      const activeTemplates = res.data.filter((t) => t.is_active);
      setTemplates(activeTemplates);
      setSelectedTemplateCode(activeTemplates.length > 0 ? activeTemplates[0].code : '');
    }).catch((err) => console.error(err));
  }, [selectedDept]);

  const togglePipeline = (name) => {
    setSelectedPipelines((prev) => (prev.includes(name) ? prev.filter((p) => p !== name) : [...prev, name]));
  };

  const selectAllPipelines = () => setSelectedPipelines(pipelines.map(p => p.name));
  const deselectAllPipelines = () => setSelectedPipelines([]);

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
    if (!file || !selectedTemplateCode || selectedPipelines.length < 2) {
      setError('Select a department, template, file, and at least two pipelines to compare.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const res = await extractService.uploadForComparison(selectedDept, selectedTemplateCode, selectedPipelines, file);
      navigate(`/orchestration/canvas/${res.data.job_id}`);
    } catch (err) {
      setError(err.message || 'Upload failed');
      setUploading(false);
    }
  };

  if (loadingInitial) {
    return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;
  }

  const activeTemplate = templates.find(t => t.code === selectedTemplateCode);
  let activeKeys = [];
  if (activeTemplate && activeTemplate.schema) {
    try {
      const schemaObj = typeof activeTemplate.schema === 'string' ? JSON.parse(activeTemplate.schema) : activeTemplate.schema;
      activeKeys = schemaObj.fields || [];
    } catch (e) {
      console.error('Failed to parse schema', e);
    }
  }

  return (
    <div className="flex flex-col gap-4 max-w-[1600px] mx-auto p-4 w-full min-h-full animate-fade-in-up">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs font-bold text-secondary tracking-widest uppercase">
          <span>DocInt Systems</span> <span className="text-border-color">›</span> <span>Execution Engine</span> <span className="text-border-color">›</span> <span className="text-primary">Multi-Pipeline Orchestration Setup</span>
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-secondary bg-white text-sm" onClick={() => { setFile(null); setSelectedPipelines([]); setSelectedDept(''); setSelectedTemplateCode(''); }}><RefreshCw size={14}/> Clear Configuration</button>
        </div>
      </div>

      <Card className="mb-2 shadow-sm border border-border-color flex-shrink-0">
        <CardContent className="p-6 flex items-center justify-between">
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-bold text-primary font-heading flex items-center gap-3">
              Orchestration — Multi-Pipeline Compare
              <Badge className="bg-indigo-100 text-primary-accent border-none text-[0.65rem] rounded uppercase tracking-wider py-1 font-bold">Interactive Canvas</Badge>
            </h1>
            <p className="text-secondary max-w-2xl text-sm">Run selected OCR and extraction pipelines simultaneously on the same document to reconcile outputs field-by-field on the live workflow canvas.</p>
          </div>
        </CardContent>
      </Card>

      <form onSubmit={handleSubmit} className="flex gap-6 flex-1 min-h-[600px]">
        {/* LEFT COLUMN */}
        <div className="w-[60%] flex flex-col gap-6">
          <Card className="border border-border-color shadow-sm">
            <CardContent className="p-6 flex flex-col gap-6">
              <div className="flex items-center justify-between border-b border-border-color pb-3">
                <h3 className="text-lg font-bold text-primary flex items-center gap-2">
                  <CheckSquare size={18} className="text-primary-accent" /> Target Context & Schema Selection
                </h3>
                <div className="text-[0.65rem] font-bold text-tertiary uppercase tracking-wider">
                  SCHEMA: {activeTemplate ? activeTemplate.name : 'NONE'}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-8">
                <div className="flex flex-col gap-2">
                  <div className="flex justify-between">
                    <label className="text-[0.65rem] font-bold text-secondary uppercase tracking-wider">Department Code</label>
                    <span className="text-[0.65rem] text-tertiary">Required</span>
                  </div>
                  <select className="input-field py-2 text-sm bg-white" value={selectedDept} onChange={(e) => setSelectedDept(e.target.value)} disabled={uploading}>
                    {departments.map((d) => <option key={d.slug} value={d.slug}>{d.slug.toUpperCase()} — {d.name}</option>)}
                  </select>
                </div>
                <div className="flex flex-col gap-2">
                  <div className="flex justify-between">
                    <label className="text-[0.65rem] font-bold text-secondary uppercase tracking-wider">Extraction Template Rule</label>
                    <span className="text-[0.65rem] text-primary-accent cursor-pointer hover:underline font-bold">View JSON Spec</span>
                  </div>
                  <select className="input-field py-2 text-sm bg-white" value={selectedTemplateCode} onChange={(e) => setSelectedTemplateCode(e.target.value)} disabled={!selectedDept || templates.length === 0 || uploading}>
                    {templates.length === 0 && <option value="" disabled>No active templates</option>}
                    {templates.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                  </select>
                </div>
              </div>

              {activeKeys.length > 0 && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-md p-4 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <span className="text-sm font-bold text-warning border-b border-warning">Strict Verification Rule ({activeKeys.length} target keys)</span>
                    <span className="text-xs text-secondary">Fields: {activeKeys.join(', ')}</span>
                  </div>
                  <div className="text-xs font-bold text-primary-accent flex items-center gap-1 cursor-pointer hover:underline">
                    <Code size={14} /> Rule Preview
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border border-border-color shadow-sm flex-1 flex flex-col">
            <CardContent className="p-6 flex flex-col h-full">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-lg font-bold text-primary mb-1">Select Pipelines to Execute</h3>
                  <p className="text-xs text-secondary">Recommended: 3+ engines active for field-by-field consensus arbitration.</p>
                </div>
                <div className="flex items-center gap-3 text-xs font-bold text-primary-accent">
                  <span className="cursor-pointer hover:underline" onClick={selectAllPipelines}>Select All</span>
                  <span className="text-border-color">|</span>
                  <span className="cursor-pointer text-tertiary hover:underline" onClick={deselectAllPipelines}>Deselect All</span>
                </div>
              </div>

              <div className="flex flex-col gap-3 overflow-y-auto">
                {pipelines.map((p) => {
                  const meta = PIPELINE_META[p.name] || { name: p.name, badges: ['Custom Pipeline'], status: 'Ready', statusColor: 'text-primary', desc: 'Custom configured intelligence extraction pipeline.' };
                  const isSelected = selectedPipelines.includes(p.name);
                  
                  return (
                    <div 
                      key={p.name} 
                      className={`flex items-start gap-4 p-4 rounded-md border-2 transition-colors cursor-pointer ${isSelected ? 'border-primary-accent bg-white shadow-sm' : 'border-border-color bg-slate-50 opacity-70'}`}
                      onClick={() => togglePipeline(p.name)}
                    >
                      <div className="mt-1">
                        {isSelected ? <CheckSquare size={18} className="text-primary-accent" /> : <Square size={18} className="text-tertiary" />}
                      </div>
                      <div className="flex-1 flex flex-col">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-bold text-primary">{meta.name}</span>
                          {meta.badges.map(b => (
                            <span key={b} className={`text-[0.6rem] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${b.includes('GPU') || b.includes('LLM') ? 'bg-indigo-100 text-primary-accent' : 'bg-slate-200 text-secondary'}`}>
                              {b}
                            </span>
                          ))}
                        </div>
                        <span className="text-xs text-secondary">{meta.desc}</span>
                      </div>
                      <div className="flex flex-col items-end text-right">
                        <span className={`text-[0.65rem] font-bold uppercase tracking-wider ${meta.statusColor}`}>{meta.status}</span>
                      </div>
                    </div>
                  );
                })}

              </div>
            </CardContent>
          </Card>
        </div>

        {/* RIGHT COLUMN */}
        <div className="w-[40%] flex flex-col gap-6">
          <Card className="border border-border-color shadow-sm">
            <CardContent className="p-6 flex flex-col gap-6">
              <div className="flex items-center justify-between border-b border-border-color pb-3">
                <h3 className="text-lg font-bold text-primary flex items-center gap-2">
                  <UploadCloud size={18} className="text-primary-accent" /> Document Ingestion
                </h3>
                <Badge variant="success" className="bg-green-50 text-success border border-green-200 text-[0.65rem] py-0.5 uppercase tracking-widest font-bold">Vault-Encrypted</Badge>
              </div>

              {!file ? (
                <div
                  className={`border-2 border-dashed rounded-lg p-8 flex flex-col items-center justify-center text-center transition-colors cursor-pointer ${dragActive ? 'border-primary-accent bg-indigo-50' : 'border-border-color bg-slate-50 hover:bg-slate-100'}`}
                  onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input ref={fileInputRef} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={handleFileChange} disabled={uploading} />
                  <div className="bg-primary-accent text-white p-3 rounded-md mb-4 shadow-sm"><UploadCloud size={24} /></div>
                  <h4 className="font-bold text-primary mb-2">Drag & drop technical document here</h4>
                  <p className="text-xs text-secondary mb-4">or click to browse from local workstation or GovCloud S3 store.</p>
                  <div className="flex items-center gap-2">
                    <span className="bg-slate-200 text-secondary text-[0.65rem] font-bold px-2 py-1 rounded uppercase tracking-wider">PDF</span>
                    <span className="bg-slate-200 text-secondary text-[0.65rem] font-bold px-2 py-1 rounded uppercase tracking-wider">PNG</span>
                    <span className="bg-slate-200 text-secondary text-[0.65rem] font-bold px-2 py-1 rounded uppercase tracking-wider">JPG</span>
                    <span className="text-xs text-tertiary ml-2 font-medium">Max: 25MB</span>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-4">
                  <div className="text-[0.65rem] font-bold text-tertiary uppercase tracking-wider">Loaded Document For Comparison</div>
                  <div className="border border-border-color rounded-md p-3 flex items-start gap-4 bg-white shadow-sm">
                    <div className="p-2 border border-danger border-opacity-30 rounded text-danger bg-red-50">
                      <FileText size={24} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-bold text-sm text-primary truncate">{file.name}</span>
                        <ShieldCheck size={16} className="text-success flex-shrink-0" />
                      </div>
                      <div className="flex items-center gap-4 text-xs text-secondary">
                        <span className="font-semibold text-primary">{(file.size / 1024).toFixed(0)} KB</span>
                        <span className="w-1 h-1 rounded-full bg-border-color" />
                        <span className="font-semibold text-primary">3 Pages</span>
                        <span className="w-1 h-1 rounded-full bg-border-color" />
                        <span className="truncate">SHA-256: 7f8a..3c21</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-tertiary">
                      <Eye size={16} className="cursor-pointer hover:text-primary" />
                      <Trash2 size={16} className="cursor-pointer hover:text-danger" onClick={() => setFile(null)} />
                    </div>
                  </div>

                  <div className="flex flex-col gap-2 mt-2">
                    <div className="flex items-center justify-between text-[0.65rem] font-bold text-primary-accent uppercase tracking-wider mb-1">
                      <span>Target Reconciliation Fields:</span>
                      <span className="text-primary">18 Recognized Keys</span>
                    </div>
                    <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                      <div className="bg-primary-accent h-full w-[100%] rounded-full"></div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[0.6rem] font-bold text-tertiary uppercase tracking-widest pt-2 border-t border-border-color mt-2">
                    <div>OCR DENSITY: <span className="text-secondary">300 DPI</span></div>
                    <div>COLORSPACE: <span className="text-secondary">MONO_REC</span></div>
                    <div className="col-span-2">SECURITY: <span className="text-secondary">UNCLASSIFIED</span></div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border border-border-color shadow-sm mt-auto">
            <CardContent className="p-6 flex flex-col gap-4">
              <div className="flex items-center justify-between border-b border-border-color pb-3">
                <span className="text-[0.65rem] font-bold text-tertiary uppercase tracking-wider">Execution Pipeline Staging</span>
                <Badge variant="success" className="bg-green-50 text-success border border-green-200 text-[0.65rem] py-0.5 uppercase tracking-widest font-bold">Ready</Badge>
              </div>

              {error && <div className="p-3 bg-danger bg-opacity-10 text-danger text-xs font-bold rounded">{error}</div>}

              <button 
                type="submit" 
                className="btn btn-primary w-full py-4 text-base shadow-md flex justify-center font-bold tracking-wide items-center gap-2" 
                disabled={!file || !selectedTemplateCode || selectedPipelines.length < 2 || uploading}
              >
                {uploading ? <Loader size={18} /> : <Zap size={18} />}
                {uploading ? 'Dispatching Pipelines...' : `Run & Reconcile ${selectedPipelines.length} Selected Pipelines`}
              </button>
            </CardContent>
          </Card>
        </div>
      </form>
    </div>
  );
};

export default OrchestrationPage;
