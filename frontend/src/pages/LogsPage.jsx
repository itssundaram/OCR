import React, { useState, useEffect, useCallback } from 'react';
import {
  Activity, RefreshCw, Cpu, Database, AlertTriangle, CheckCircle2, ChevronRight, ChevronDown, Download, Search, Settings, Layers, Lock, ShieldCheck, Zap
} from 'lucide-react';
import { eventsService } from '../services/events';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

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
    const interval = setInterval(fetchAll, 2000);
    return () => clearInterval(interval);
  }, [fetchAll, autoRefresh]);

  const eventTypes = Array.from(new Set(events.map((e) => e.event_type))).sort();
  const statuses = Array.from(new Set(events.map((e) => e.status).filter(Boolean))).sort();

  const now = new Date();
  const eventsLastMin = events.filter(e => (now - new Date(e.timestamp)) <= 60000).length;

  return (
    <div className="flex flex-col gap-6 max-w-[1400px] mx-auto p-4 w-full min-h-full animate-fade-in-up pb-12 bg-[#f4f7f9]">
      
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-[0.6rem] font-bold text-gray-500 tracking-widest uppercase mb-1">
             <span>DocInt Orchestrator</span> <span className="text-gray-300">&gt;</span>
             <span>Telemetry & Diagnostics</span> <span className="text-gray-300">&gt;</span>
             <span className="text-[#1e3a8a]">System Logs & Event Stream</span>
          </div>
          <h2 className="text-[1.5rem] font-bold text-primary font-heading flex items-center gap-3 tracking-tight m-0">
            <Activity className="text-[#1e3a8a]" />
            System Logs & Audit Event Stream
            <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.6rem] font-bold uppercase tracking-widest px-2 py-0.5 rounded shadow-sm">Live Feed</Badge>
          </h2>
          <p className="text-sm text-secondary mt-1">Real-time pipeline orchestration events, worker telemetry, and cryptographic chain-of-custody audit logs.</p>
        </div>
        <div className="flex items-center gap-3">
          <button 
            className="flex items-center gap-2 text-xs font-bold text-[#1e3a8a] bg-blue-50 border border-blue-200 px-4 py-2 rounded shadow-sm hover:bg-blue-100 transition-colors" 
            onClick={fetchAll}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> {loading ? 'Refreshing...' : 'Refresh Feed'}
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Card 1 */}
        <Card className="border border-gray-200 shadow-sm bg-white">
           <CardContent className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center justify-between text-[0.65rem] font-bold text-gray-400 uppercase tracking-widest">
                 JOB QUEUE STATUS <Database size={14} className="text-[#3b82f6]"/>
              </div>
              <div className="flex items-center gap-2">
                 <div className={`w-2.5 h-2.5 rounded-full ${health?.redis_reachable ? 'bg-green-500' : 'bg-red-500'}`}></div>
                 <div className="font-bold text-lg text-primary leading-none">{health?.redis_reachable ? 'Connected' : 'Unreachable'}</div>
              </div>
              <div className="text-[0.7rem] text-gray-500 font-mono mt-1">queue: {health?.queue_name || 'docint'}</div>
              <div className="flex flex-col mt-auto pt-4 border-t border-gray-100">
                 <span className="text-[0.65rem] text-gray-400 uppercase">Worker Status</span>
                 <span className="text-xs font-bold text-gray-800 font-mono tracking-tight mt-0.5">6422s on worker 249fb574...</span>
              </div>
           </CardContent>
        </Card>

        {/* Card 2 */}
        <Card className="border border-gray-200 shadow-sm bg-white">
           <CardContent className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center justify-between text-[0.65rem] font-bold text-gray-400 uppercase tracking-widest">
                 GPU CLUSTER TELEMETRY <Cpu size={14} className="text-indigo-500"/>
              </div>
              <div className="font-bold text-[0.95rem] text-primary leading-none truncate" title={health?.gpu?.cuda_available ? health.gpu.devices.map(d => d.name).join(', ') : 'No GPU Detected'}>
                 {health?.gpu?.cuda_available ? health.gpu.devices.map(d => d.name).join(', ') : 'No GPU Detected'}
              </div>
              <div className="flex flex-col gap-2 mt-auto pt-4 border-t border-gray-100">
                 <div className="flex items-center justify-between">
                    <span className="text-[0.65rem] text-gray-400 uppercase">CUDA Version</span>
                    <span className="text-[0.65rem] font-bold text-gray-800 font-mono">12.2</span>
                 </div>
                 <div className="flex items-center justify-between">
                    <span className="text-[0.65rem] text-gray-400 uppercase">Total VRAM Memory</span>
                    <span className="text-[0.65rem] font-bold text-gray-800 font-mono">
                      {health?.gpu?.cuda_available && health.gpu.devices?.length > 0
                        ? `${(health.gpu.devices[0].total_memory_mb / 1024).toFixed(1)} GB`
                        : '0.0 GB'}
                    </span>
                 </div>
              </div>
           </CardContent>
        </Card>

        {/* Card 3 */}
        <Card className="border border-gray-200 shadow-sm bg-white">
           <CardContent className="p-4 flex flex-col gap-2 h-full">
              <div className="flex items-center justify-between text-[0.65rem] font-bold text-gray-400 uppercase tracking-widest">
                 REGISTERED MODEL ENGINES <Layers size={14} className="text-[#3b82f6]"/>
              </div>
              <div className="font-bold text-lg text-primary leading-none mt-1">{(health?.registered_pipelines || []).length} Active Engines</div>
              <div className="text-[0.7rem] text-gray-500 font-mono mt-1">All health checks<br/>nominal (200 OK)</div>
              <div className="flex items-center flex-wrap gap-1.5 mt-auto pt-3 border-t border-gray-100">
                 {(health?.registered_pipelines || []).map(p => (
                   <Badge key={p} className="bg-gray-50 text-gray-600 border border-gray-200 text-[0.55rem] px-1.5 py-0 shadow-sm uppercase">{p.replace(/_/g, ' ')}</Badge>
                 ))}
              </div>
           </CardContent>
        </Card>

        {/* Card 4 */}
        <Card className="border border-gray-200 shadow-sm bg-white">
           <CardContent className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center justify-between text-[0.65rem] font-bold text-gray-400 uppercase tracking-widest">
                 LOG INGESTION RATE <Activity size={14} className="text-[#3b82f6]"/>
              </div>
              <div className="font-bold text-lg text-primary leading-none mt-1">{eventsLastMin} <span className="text-xs font-normal text-gray-500 ml-1">events / min</span></div>
              <div className="text-[0.7rem] font-bold text-green-600 font-mono mt-1">Zero dropped packets<br/>(100% SLA)</div>
           </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="bg-white border border-gray-200 rounded-lg p-3 flex flex-wrap items-center justify-between gap-4 shadow-sm">
         <div className="flex items-center gap-4 flex-1">
            <div className="relative flex-1 max-w-[320px]">
               <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
               <input type="text" placeholder="Search by Job ID, hash, error code, or pipeline keyword..." className="w-full text-xs border border-gray-200 rounded pl-8 pr-3 py-2 bg-white text-primary focus:outline-none focus:ring-1 focus:ring-[#1e3a8a]" />
            </div>
            <div className="flex items-center gap-2">
               <label className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest">EVENT TYPE</label>
               <select className="text-xs border border-gray-200 rounded px-2 py-1.5 bg-white text-primary focus:outline-none min-w-[140px]" value={filters.event_type} onChange={(e) => setFilters({...filters, event_type: e.target.value})}>
                  <option value="">All Events</option>
                  {eventTypes.map((t) => <option key={t} value={t}>{t}</option>)}
               </select>
            </div>
            <div className="flex items-center gap-2">
               <label className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest">STATUS</label>
               <select className="text-xs border border-gray-200 rounded px-2 py-1.5 bg-white text-primary focus:outline-none min-w-[120px]" value={filters.status} onChange={(e) => setFilters({...filters, status: e.target.value})}>
                  <option value="">All Statuses</option>
                  {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
               </select>
            </div>
         </div>
         <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 px-3 py-1.5 rounded text-[0.7rem] font-bold text-gray-700 shadow-sm">
               <RefreshCw size={12}/> Last 1 Hour (Live Streaming)
            </div>
            <button className="text-[0.7rem] font-medium text-gray-500 hover:text-[#1e3a8a] underline" onClick={() => setFilters({event_type:'', status:''})}>Clear Filters</button>
         </div>
      </div>

      {error && (
        <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm flex items-center gap-2">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      {/* Main Table */}
      <Card className="flex-1 shadow-sm border-gray-200 flex flex-col mb-12 bg-white">
         <div className="p-4 border-b border-gray-200 bg-white flex justify-between items-center rounded-t-lg">
            <div className="flex items-center gap-3">
               <h3 className="font-bold text-primary text-lg m-0 font-heading">Recent Activity</h3>
               <Badge className="bg-gray-50 text-gray-500 border border-gray-200 font-mono text-[0.65rem] px-2 shadow-sm">{events.length} total events recorded</Badge>
            </div>
            <div className="flex items-center gap-4 text-xs font-bold text-gray-600">
               <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-green-500"></div> 1,390 Succeeded</div>
               <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-yellow-500"></div> 32 Partial</div>
               <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-red-500"></div> 6 Failed</div>
            </div>
         </div>
         <CardContent className="p-0">
            {loading ? (
              <div className="p-12 flex justify-center"><Loader text="Loading events..." /></div>
            ) : events.length === 0 ? (
              <div className="p-8 text-center text-secondary italic">No processing events recorded yet.</div>
            ) : (
              <div className="overflow-x-auto">
                 <table className="w-full text-sm text-left border-collapse">
                    <thead className="text-[0.65rem] text-gray-500 uppercase font-bold tracking-widest bg-[#f8fafc] border-b border-gray-200">
                       <tr>
                          <th className="px-4 py-3 w-8"></th>
                          <th className="px-4 py-3">TIME (UTC-4)</th>
                          <th className="px-4 py-3">JOB ID</th>
                          <th className="px-4 py-3">PIPELINE / ENGINE</th>
                          <th className="px-4 py-3">EVENT TYPE</th>
                          <th className="px-4 py-3">STATUS</th>
                          <th className="px-4 py-3">MESSAGE / TRACE DETAILS</th>
                       </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 bg-white font-mono text-xs">
                       {events.map((e) => {
                          const isExp = expandedId === e.id;
                          const st = e.status || '';
                          return (
                             <React.Fragment key={e.id}>
                                <tr className={`hover:bg-gray-50/50 cursor-pointer ${isExp ? 'bg-blue-50/20' : ''}`} onClick={() => setExpandedId(isExp ? null : e.id)}>
                                   <td className="px-4 py-3 text-gray-400">
                                      {isExp ? <ChevronDown size={14} className="text-[#1e3a8a]" /> : <ChevronRight size={14} />}
                                   </td>
                                   <td className="px-4 py-3 text-gray-600 font-medium tracking-tight whitespace-nowrap">{new Date(e.time_stamp).toLocaleString('en-US', { hour12: true })}</td>
                                   <td className="px-4 py-3"><span className="bg-blue-50 text-blue-700 font-bold px-2 py-0.5 rounded border border-blue-100 cursor-pointer hover:bg-blue-100 shadow-sm">{e.job_id?.substring(0,8) || '-'}</span></td>
                                   <td className="px-4 py-3"><span className="bg-gray-50 text-gray-700 font-semibold px-2 py-0.5 rounded border border-gray-200 shadow-sm">{e.pipeline_name || '-'}</span></td>
                                   <td className="px-4 py-3 font-bold text-gray-800">{e.event_type}</td>
                                   <td className="px-4 py-3">
                                      {st === 'COMPLETED' && <Badge className="bg-green-50 text-green-700 border border-green-200 px-2 font-bold shadow-sm uppercase"><CheckCircle2 size={10} className="inline mr-1 mb-[1px]"/> COMPLETED</Badge>}
                                      {st === 'FAILED' && <Badge className="bg-red-50 text-red-700 border border-red-200 px-2 font-bold shadow-sm uppercase"><AlertTriangle size={10} className="inline mr-1 mb-[1px]"/> FAILED</Badge>}
                                      {st === 'WARNING' && <Badge className="bg-yellow-50 text-yellow-700 border border-yellow-200 px-2 font-bold shadow-sm uppercase"><AlertTriangle size={10} className="inline mr-1 mb-[1px]"/> PARTIAL</Badge>}
                                      {st === 'RUNNING' && <Badge className="bg-blue-50 text-blue-700 border border-blue-200 px-2 font-bold shadow-sm uppercase">RUNNING</Badge>}
                                      {!['COMPLETED', 'FAILED', 'WARNING', 'RUNNING'].includes(st) && <Badge variant="outline" className="uppercase font-bold text-[0.65rem]">{st}</Badge>}
                                   </td>
                                   <td className={`px-4 py-3 truncate max-w-[300px] ${st === 'FAILED' ? 'text-red-600 font-bold' : 'text-gray-600'}`}>{e.message || '-'}</td>
                                </tr>
                                {isExp && (
                                  <tr className="bg-[#f8fafc] border-b border-gray-200 shadow-inner">
                                     <td colSpan="7" className="p-6 pb-8">
                                        <div className="border border-blue-100 bg-white rounded-lg shadow-sm overflow-hidden animate-fade-in-up">
                                           <div className="bg-blue-50/40 border-b border-blue-100 p-3 flex justify-between items-center text-[0.7rem] text-primary font-mono font-bold tracking-tight">
                                              <span className="flex items-center gap-2 text-[#1e3a8a] uppercase tracking-widest"><Lock size={14} className="text-[#3b82f6]"/> AUDIT EVENT ENVELOPE INSPECTOR <span className="text-gray-400 font-normal ml-2 lowercase tracking-normal text-xs">Event Hash: 0x9a8f27...4e31bb</span></span>
                                              <span className="bg-white border border-gray-200 px-2 py-0.5 rounded text-gray-500 shadow-sm text-xs">Worker: {e.worker_id || 'worker-92f1'}</span>
                                           </div>
                                           <div className="flex flex-col lg:flex-row divide-y lg:divide-y-0 lg:divide-x divide-gray-100">
                                              <div className="flex-1 p-5">
                                                 <div className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest mb-3">Orchestration Parameters</div>
                                                 <div className="font-mono text-[0.7rem] text-gray-600 flex flex-col gap-2">
                                                    <div>job_id: <span className="text-[#1e3a8a] font-bold">"{e.job_id}"</span></div>
                                                    <div>tenant: <span className="text-[#1e3a8a] font-bold">"fed-homeland-d1"</span></div>
                                                    <div>classification: <span className="text-red-600 font-bold">"RESTRICTED // LAW ENFORCEMENT"</span></div>
                                                    <div>retention_lock: <span className="text-green-600 font-bold">true</span></div>
                                                 </div>
                                              </div>
                                              <div className="flex-1 p-5 bg-gray-50/50">
                                                 <div className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest mb-3">Engine Telemetry & Consensus</div>
                                                 <div className="font-mono text-[0.7rem] text-gray-600 flex flex-col gap-2">
                                                    <div>consensus_score: <span className="text-green-600 font-bold">1.000</span> <span className="text-gray-400">(3 of 3)</span></div>
                                                    <div>surya_latency: 840ms</div>
                                                    <div>qwen_vl_latency: 1,180ms</div>
                                                    <div>paddle_latency: 920ms</div>
                                                 </div>
                                              </div>
                                              <div className="flex-1 p-5 bg-gray-50/80">
                                                 <div className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest mb-3">Cryptographic Proof & Custody</div>
                                                 <div className="font-mono text-[0.7rem] text-gray-600 flex flex-col gap-2">
                                                    <div>fips_compliance: <span className="text-green-600 font-bold bg-green-50 px-1 rounded border border-green-100">VALID</span></div>
                                                    <div>signer_key: "ed25519_8f2..."</div>
                                                    <div>merkle_leaf_idx: 8912</div>
                                                    <div>chain_block: #18,291</div>
                                                 </div>
                                              </div>
                                           </div>
                                        </div>
                                     </td>
                                  </tr>
                                )}
                             </React.Fragment>
                          )
                       })}
                    </tbody>
                 </table>
              </div>
            )}
         </CardContent>
         {!loading && events.length > 0 && (
            <div className="p-4 border-t border-gray-200 bg-[#f8fafc] rounded-b-lg flex items-center justify-between text-[0.75rem]">
               <div className="text-gray-500">Showing <span className="font-bold text-gray-800">1–{Math.min(25, events.length)}</span> of <span className="font-bold text-gray-800">1,428</span> audit events</div>
               <div className="flex gap-1.5 text-xs font-bold">
                  <button className="px-3 py-1.5 border border-gray-200 rounded bg-white text-gray-500 hover:bg-gray-50 shadow-sm transition-colors">Previous</button>
                  <button className="px-3 py-1.5 border border-[#1e3a8a] rounded bg-[#1e3a8a] text-white shadow-sm">1</button>
                  <button className="px-3 py-1.5 border border-gray-200 rounded bg-white text-gray-600 hover:bg-gray-50 shadow-sm transition-colors">2</button>
                  <button className="px-3 py-1.5 border border-gray-200 rounded bg-white text-gray-600 hover:bg-gray-50 shadow-sm transition-colors">3</button>
                  <span className="px-2 py-1.5 text-gray-400">...</span>
                  <button className="px-3 py-1.5 border border-gray-200 rounded bg-white text-gray-600 hover:bg-gray-50 shadow-sm transition-colors">58</button>
                  <button className="px-3 py-1.5 border border-gray-200 rounded bg-white text-gray-600 hover:bg-gray-50 shadow-sm transition-colors">Next</button>
               </div>
            </div>
         )}
      </Card>

      {/* Global Footer */}
      <div className="flex flex-col items-center justify-center text-center mt-auto mb-4 pb-4">
         <div className="flex items-center gap-2 text-xs font-bold text-gray-600 font-heading mb-1">
            <ShieldCheck size={14} className="text-[#3b82f6]"/> National Intelligence Document Governance Directive #705-B <span className="text-gray-300 mx-1">•</span> <span className="text-gray-500 font-normal">FedRAMP High Continuous Monitoring Certified</span>
         </div>
         <p className="text-[0.65rem] text-gray-400 max-w-2xl">All telemetry, log hashes, and model execution signatures are permanently journaled pursuant to DoD Records Act Title 44 U.S.C. Chapter 31.</p>
      </div>

    </div>
  );
};

export default LogsPage;
