import React, { useState, useEffect } from 'react';
import { Copy, Check, Terminal, Info, Zap, Settings, Key, BookOpen, ShieldCheck, Activity, Database, Code, RefreshCw, Building2 } from 'lucide-react';
import { departmentsService } from '../services/departments';
import { templatesService } from '../services/templates';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const GenerateUrlPage = () => {
  const [departments, setDepartments] = useState([]);
  const [templatesByDept, setTemplatesByDept] = useState({});
  const [selectedDeptSlug, setSelectedDeptSlug] = useState('');
  const [selectedTemplateCode, setSelectedTemplateCode] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [copiedId, setCopiedId] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const deptRes = await departmentsService.list();
        const activeDepts = deptRes.data.filter((d) => d.is_active);
        setDepartments(activeDepts);
        
        const templatesMap = {};
        for (const dept of activeDepts) {
          try {
            const tmplRes = await templatesService.listTemplates(dept.slug);
            templatesMap[dept.slug] = (tmplRes.data || []).filter(t => t.is_active === 1 || t.is_active === true || t.is_active === '1' || String(t.is_active).toLowerCase() === 'true');
          } catch (e) {
            templatesMap[dept.slug] = [];
          }
        }
        setTemplatesByDept(templatesMap);
        
        if (activeDepts.length > 0) {
          setSelectedDeptSlug(activeDepts[0].slug);
          const firstDeptTmpls = templatesMap[activeDepts[0].slug];
          if (firstDeptTmpls && firstDeptTmpls.length > 0) {
             setSelectedTemplateCode(firstDeptTmpls[0].code);
          }
        }
      } catch (err) {
        setError(err.message || 'Failed to load data');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleDeptChange = (e) => {
    const slug = e.target.value;
    setSelectedDeptSlug(slug);
    const tmpls = templatesByDept[slug] || [];
    if (tmpls.length > 0) {
       setSelectedTemplateCode(tmpls[0].code);
    } else {
       setSelectedTemplateCode('');
    }
  };

  const copyUrl = (id, text) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading Endpoints..." /></div>;

  const baseUrl = window.location.origin.replace(':5173', ':8001');
  const basePrefix = `/api/v1/${selectedDeptSlug || '{dept_slug}'}/${selectedTemplateCode || '{template_code}'}`;
  const fullBaseUrl = `${baseUrl}${basePrefix}`;

  const copyAllEndpoints = () => {
    const textToCopy = `=== ${selectedDeptSlug?.toUpperCase() || 'UNK'} Department — Template: ${selectedTemplateCode || 'none'} ===\n\n` +
      `[1] Default Arbitration Endpoint\n` +
      `URL: POST ${fullBaseUrl}\n` +
      `cURL: curl -X POST "${fullBaseUrl}" \\\n  -F "file=@manifest_inspection.pdf"\n\n` +
      `[2] Isolated Engine Target\n` +
      `URL: POST ${fullBaseUrl}/pipeline/{pipeline_name}\n` +
      `cURL: curl -X POST "${fullBaseUrl}/pipeline/surya" \\\n  -F "file=@vehicle_reg.png"\n\n` +
      `[3] Comparative Engine Output\n` +
      `URL: POST ${fullBaseUrl}/compare?pipelines=surya&pipelines=qwen\n` +
      `cURL: curl -X POST "${fullBaseUrl}/compare?pipelines=surya&pipelines=qwen" \\\n  -F "file=@audit_doc.pdf"`;
    
    copyUrl('all_endpoints', textToCopy);
  };

  
  const mockJsonSchema = {
    "status": "success",
    "pipeline_arbitration": "consensus_v2",
    "extracted_fields": {
      "ba_number": {
        "value": "BA-70492-X",
        "confidence": 0.994
      },
      "engine_no": {
        "value": "4D56-U99142",
        "confidence": 0.988
      },
      "chasis_no": {
        "value": "JM3JR88W13Z001928",
        "confidence": 1.000
      }
    },
    "consensus_score": 1.0,
    "engines_queried": ["surya", "qwen-vl"],
    "latency_ms": 812
  };

  return (
    <div className="flex flex-col gap-6 w-full min-h-full p-4 bg-bg-primary font-sans animate-fade-in-up pb-12">
      
      {/* Header Panel */}
      <div className="flex flex-col gap-4 bg-white border border-border-color rounded-lg p-5 shadow-sm flex-shrink-0">
        <div className="flex items-start justify-between">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-3">
              <div className="flex flex-col">
                <div className="flex items-center gap-2 text-[0.6rem] font-bold text-gray-500 tracking-widest uppercase mb-1">
                   <span>DOCINT ORCHESTRATOR</span> <span className="text-gray-300">&gt;</span>
                   <span>DEVELOPER & INTEGRATION</span> <span className="text-gray-300">&gt;</span>
                   <span className="text-[#1e3a8a]">GENERATE URL / API</span>
                </div>
                <h1 className="text-[1.7rem] font-bold text-primary font-heading tracking-tight leading-tight m-0">Automated API & Ingestion Endpoint<br/>Generator</h1>
              </div>
            </div>
            <p className="text-sm text-secondary mt-2">Generate pre-configured REST endpoints and webhook URLs for automated<br/>departmental document ingestion pipelines.</p>
          </div>
          <div className="flex flex-col gap-2 w-full max-w-[220px]">
             <button onClick={copyAllEndpoints} className="btn btn-primary bg-[#1e3a8a] text-sm flex items-center justify-center gap-2 shadow-md hover:bg-[#1e40af] w-full px-4 py-2.5 rounded text-white font-bold transition-colors">
                {copiedId === 'all_endpoints' ? <Check size={14}/> : <Copy size={14}/>} 
                {copiedId === 'all_endpoints' ? 'Copied to Clipboard!' : 'Copy All Endpoints'}
             </button>
          </div>
        </div>
        
        <div className="flex flex-col lg:flex-row lg:items-end justify-between border-t border-gray-100 pt-5 mt-2 gap-6">
          <div className="flex items-end gap-6 w-full lg:w-auto">
             <div className="flex flex-col gap-1.5 flex-1 min-w-[240px]">
                <label className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest flex items-center gap-1.5"><Building2 size={12}/> DEPARTMENT</label>
                <select 
                  className="w-full text-sm border border-gray-300 rounded px-3 py-2 bg-white text-primary font-medium focus:outline-none focus:ring-1 focus:ring-[#1e3a8a] shadow-sm"
                  value={selectedDeptSlug}
                  onChange={handleDeptChange}
                >
                  {departments.map(d => <option key={d.slug} value={d.slug}>{d.slug.toUpperCase()} - {d.name}</option>)}
                </select>
             </div>
             <div className="flex flex-col gap-1.5 flex-1 min-w-[280px]">
                <label className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest flex items-center gap-1.5"><Code size={12}/> EXTRACTION TEMPLATE</label>
                <select 
                  className="w-full text-sm border border-gray-300 rounded px-3 py-2 bg-white text-primary font-medium focus:outline-none focus:ring-1 focus:ring-[#1e3a8a] shadow-sm"
                  value={selectedTemplateCode}
                  onChange={(e) => setSelectedTemplateCode(e.target.value)}
                >
                  {(templatesByDept[selectedDeptSlug] || []).map(t => <option key={t.code} value={t.code}>{t.code} (v{t.version} Official)</option>)}
                </select>
             </div>
             <div className="flex flex-col gap-1.5 flex-1 min-w-[220px]">
                <label className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest flex items-center gap-1.5">ENGINE STATUS</label>
                <div className="flex items-center gap-2 bg-green-50 border border-green-200 text-green-700 px-3 py-2 rounded text-xs font-bold shadow-sm">
                   <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                   4 Consensus Engines Active
                </div>
             </div>
          </div>

        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
         {/* Left Column - Endpoints */}
         <div className="flex flex-col gap-5 flex-[2]">
            
            <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm flex items-center justify-between">
               <div className="flex items-center gap-3">
                  <div className="w-3 h-3 bg-[#1e3a8a] rounded-sm"></div>
                  <h2 className="text-xl font-bold text-primary font-heading tracking-tight m-0">{selectedDeptSlug ? selectedDeptSlug.toUpperCase() : 'UNK'} Department — Template: <span className="text-[#1e3a8a]">{selectedTemplateCode || 'None Selected'}</span></h2>
               </div>
               <div className="flex flex-col items-end">
                  <span className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest">Prefix:</span>
                  <span className="text-sm font-mono text-gray-600 bg-gray-50 px-2 py-0.5 rounded border border-gray-200">{basePrefix}</span>
               </div>
            </div>

            {/* Endpoint 1 */}
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm flex flex-col">
              <div className="flex items-center justify-between p-3 border-b border-gray-100 bg-white">
                 <div className="flex items-center gap-3">
                   <span className="px-2 py-1 rounded text-xs font-bold text-white bg-blue-600 shadow-sm">POST</span>
                   <span className="font-mono text-[0.85rem] text-gray-800 font-bold">{fullBaseUrl}</span>
                 </div>
                 <div className="flex gap-2">
                    <button onClick={() => copyUrl('ep1_url', fullBaseUrl)} className="flex items-center gap-1.5 px-2 py-1 text-xs font-bold text-gray-600 hover:text-primary bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded transition-colors"><Copy size={12}/> {copiedId === 'ep1_url' ? 'Copied' : 'Copy URL'}</button>
                    <button onClick={() => copyUrl('ep1_curl', `curl -X POST "${fullBaseUrl}" \\n  -F "file=@manifest_inspection.pdf"`)} className="flex items-center gap-1.5 px-2 py-1 text-xs font-bold text-gray-600 hover:text-primary bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded transition-colors"><Terminal size={12}/> {copiedId === 'ep1_curl' ? 'Copied' : 'cURL Snippet'}</button>
                 </div>
              </div>
              <div className="p-5 flex flex-col gap-4">
                 <p className="text-sm text-gray-600 leading-relaxed">Upload and parse a document using standard default agency pipeline configuration. Automatically selects the validated ensemble arbitration logic.</p>
                 <div className="flex gap-3">
                    <Badge className="bg-gray-50 text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-2 py-0.5 rounded shadow-sm">file: multipart/form-data</Badge>
                    <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.65rem] font-bold px-2 py-0.5 rounded shadow-sm"><Check size={10} className="inline mr-1"/> Synch Mode (Blocking)</Badge>
                    <Badge className="bg-gray-50 text-gray-500 border border-gray-200 text-[0.65rem] px-2 py-0.5 rounded shadow-sm">Latency: ~840ms</Badge>
                 </div>
                 <div className="bg-[#0f172a] text-slate-300 p-4 rounded-lg border border-[#1e293b] shadow-inner font-mono text-xs overflow-auto relative mt-1">
                   <pre className="whitespace-pre-wrap leading-relaxed"><span className="text-gray-500"># Sample Terminal Ingestion Execution</span><br/><span className="text-blue-400">curl</span> -X POST <span className="text-green-300">"{fullBaseUrl}"</span> \<br/>  -F <span className="text-green-300">"file=@manifest_inspection.pdf"</span></pre>
                 </div>
              </div>
            </div>

            {/* Endpoint 2 */}
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm flex flex-col">
              <div className="flex items-center justify-between p-3 border-b border-gray-100 bg-white">
                 <div className="flex items-center gap-3">
                   <span className="px-2 py-1 rounded text-xs font-bold text-white bg-blue-600 shadow-sm">POST</span>
                   <span className="font-mono text-[0.85rem] text-gray-800 font-bold">{fullBaseUrl}/pipeline/<span className="text-blue-600">&#123;pipeline_name&#125;</span></span>
                 </div>
                 <div className="flex gap-2">
                    <button className="flex items-center gap-1.5 px-2 py-1 text-xs font-bold text-gray-600 hover:text-primary bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded transition-colors"><Copy size={12}/> Copy URL</button>
                 </div>
              </div>
              <div className="p-5 flex flex-col gap-4">
                 <p className="text-sm text-gray-600 leading-relaxed">Direct upload targeting an isolated OCR model engine without orchestrator arbitration. Ideal for comparative benchmark tests or engine degradation analysis.</p>
                 <div className="flex items-center gap-3 text-xs bg-blue-50/30 border border-blue-100 px-3 py-2 rounded">
                    <span className="font-bold text-gray-500 uppercase tracking-widest text-[0.65rem]">PATH PARAM:</span>
                    <Badge className="bg-blue-100 text-blue-700 border-none font-mono text-xs px-1.5 py-0">pipeline_name</Badge>
                    <span className="text-gray-400">&rarr;</span>
                    <span className="text-gray-500 text-[0.65rem] uppercase tracking-widest">Allowed values:</span>
                    <div className="flex gap-1.5">
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0">qwen</Badge>
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0">surya</Badge>
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0">paddle</Badge>
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0">tesseract</Badge>
                    </div>
                 </div>
                 <div className="bg-[#0f172a] text-slate-300 p-4 rounded-lg border border-[#1e293b] shadow-inner font-mono text-xs overflow-auto relative mt-1">
                   <pre className="whitespace-pre-wrap leading-relaxed"><span className="text-gray-500"># Targeting Isolated Surya Engine</span><br/><span className="text-blue-400">curl</span> -X POST <span className="text-green-300">"{fullBaseUrl}/pipeline/surya"</span> \<br/>  -F <span className="text-green-300">"file=@vehicle_reg.png"</span></pre>
                 </div>
              </div>
            </div>

            {/* Endpoint 3 */}
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm flex flex-col">
              <div className="flex items-center justify-between p-3 border-b border-gray-100 bg-white">
                 <div className="flex items-center gap-3">
                   <span className="px-2 py-1 rounded text-xs font-bold text-white bg-blue-600 shadow-sm">POST</span>
                   <span className="font-mono text-[0.85rem] text-gray-800 font-bold">{fullBaseUrl}/compare?<br/><span className="text-gray-400 font-normal">pipelines=surya&amp;pipelines=qwen</span></span>
                 </div>
                 <div className="flex gap-2 self-start">
                    <button className="flex items-center gap-1.5 px-2 py-1 text-xs font-bold text-gray-600 hover:text-primary bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded transition-colors"><Copy size={12}/> Copy URL</button>
                 </div>
              </div>
              <div className="p-5 flex flex-col gap-4">
                 <p className="text-sm text-gray-600 leading-relaxed">Execute parallel multi-engine extraction and reconcile outputs via consensus arbiter engine. Returns individual scores alongside aggregated high-confidence data points.</p>
                 <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-3 text-xs bg-gray-50 border border-gray-200 px-3 py-2 rounded">
                       <span className="font-bold text-gray-500 uppercase tracking-widest text-[0.65rem]">QUERY PARAMS:</span>
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0 shadow-sm">pipelines: [surya, qwen, paddle]</Badge>
                       <Badge className="bg-white text-gray-600 border border-gray-200 font-mono text-[0.65rem] px-1.5 py-0 shadow-sm">min_confidence: 0.95</Badge>
                    </div>
                    <div className="flex">
                       <Badge className="bg-indigo-50 text-indigo-700 border border-indigo-200 text-[0.65rem] font-bold px-2 py-0.5 rounded shadow-sm"><Zap size={10} className="inline mr-1"/> Ensemble Synthesis</Badge>
                    </div>
                 </div>
                 <div className="bg-[#0f172a] text-slate-300 p-4 rounded-lg border border-[#1e293b] shadow-inner font-mono text-xs overflow-auto relative mt-1">
                   <pre className="whitespace-pre-wrap leading-relaxed"><span className="text-gray-500"># Multi-Pipeline Parallel Comparison</span><br/><span className="text-blue-400">curl</span> -X POST <span className="text-green-300">"{fullBaseUrl}/compare?<br/>pipelines=surya&amp;pipelines=qwen&amp;min_confidence=0.95"</span> \<br/>  -F <span className="text-green-300">"file=@chasis_plate.jpg"</span></pre>
                 </div>
              </div>
            </div>

            {/* Endpoint 4 */}
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm flex flex-col">
              <div className="flex items-center justify-between p-3 border-b border-gray-100 bg-white">
                 <div className="flex items-center gap-3">
                   <span className="px-2 py-1 rounded text-xs font-bold text-white bg-green-600 shadow-sm">GET</span>
                   <span className="font-mono text-[0.85rem] text-gray-800 font-bold">{fullBaseUrl}/status/<span className="text-green-600">&#123;job_id&#125;</span></span>
                 </div>
              </div>
              <div className="p-5 flex flex-col gap-4">
                 <p className="text-sm text-gray-600 leading-relaxed">Poll asynchronous document extraction status and retrieve validated JSON key-value response with cryptographic digest confirmation.</p>
                 <div className="flex gap-3">
                    <Badge className="bg-green-50 text-green-700 border border-green-200 font-bold text-[0.65rem] px-2 py-0.5 rounded shadow-sm"><Check size={10} className="inline mr-1"/> Returns 200 OK with Pydantic Validated JSON</Badge>
                    <Badge className="bg-gray-50 text-gray-500 border border-gray-200 font-mono text-[0.65rem] px-2 py-0.5 rounded shadow-sm">job_id: UUIDv4</Badge>
                 </div>
                 <div className="bg-[#0f172a] text-slate-300 p-4 rounded-lg border border-[#1e293b] shadow-inner font-mono text-xs overflow-auto relative mt-1">
                   <pre className="whitespace-pre-wrap leading-relaxed"><span className="text-gray-500"># Polling Document Extraction Results</span><br/><span className="text-blue-400">curl</span> -X GET <span className="text-green-300">"{fullBaseUrl}/status/8f7c-4029-91a"</span></pre>
                 </div>
              </div>
            </div>

         </div>

         {/* Right Column - Specs */}
         <div className="flex flex-col gap-5 flex-1 min-w-[340px]">
            {/* Headers Spec */}
            <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
               <div className="p-4 border-b border-gray-100 flex items-center justify-between bg-[#f8fafc]">
                  <h3 className="font-bold text-primary flex items-center gap-2 font-heading text-[0.95rem]"><Settings size={16} className="text-[#1e3a8a]"/> Request Headers Spec</h3>
                  <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.6rem] font-bold tracking-wider px-1.5 py-0 uppercase">Mandatory</Badge>
               </div>
               <div className="p-4 flex flex-col gap-4 divide-y divide-gray-100">
                  <div className="flex flex-col gap-1 pb-1">
                     <div className="flex items-center justify-between">
                        <span className="font-mono text-sm text-gray-800 font-bold">Content-Type</span>
                        <span className="font-mono text-xs text-gray-400">multipart/form-data</span>
                     </div>
                     <p className="text-[0.75rem] text-gray-500 mt-1">Required for binary PDF & TIFF uploads.</p>
                  </div>
               </div>
            </div>

            {/* Pydantic Schema Output */}
            <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
               <div className="p-4 border-b border-gray-100 flex items-center justify-between bg-white">
                  <h3 className="font-bold text-primary flex items-center gap-2 font-heading text-[0.95rem]"><Code size={16} className="text-[#1e3a8a]"/> Pydantic Extraction Schema</h3>
                  <button className="flex items-center gap-1.5 px-2 py-1 text-xs font-bold text-[#1e3a8a] hover:bg-blue-50 border border-blue-100 rounded transition-colors shadow-sm"><Copy size={12}/> Copy</button>
               </div>
               <div className="bg-[#0f172a] text-slate-300 p-4 font-mono text-[0.7rem] overflow-auto h-[380px]">
                  <pre>{JSON.stringify(mockJsonSchema, null, 2)}</pre>
               </div>
            </div>

         </div>
      </div>
    </div>
  );
};

export default GenerateUrlPage;
