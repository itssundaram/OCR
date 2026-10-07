import React, { useState, useEffect } from 'react';
import { 
  HelpCircle, Cloud, Cpu, ShieldCheck, Search, Flag, Share2, 
  GitFork, FileText, BadgeCheck, Headphones, BookOpen, Phone, Clock, Mail, 
  Ticket, Download, ExternalLink, ArrowRight, FlaskConical, Globe, ChevronDown, CheckCircle2, GitMerge, Code, ChevronRight
} from 'lucide-react';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';

const FAQS = [
  {
    icon: <GitMerge size={16} className="text-[#3b82f6]" />,
    q: 'What is the difference between Single OCR and Multi-Pipeline Orchestration?',
    a: (
      <div className="flex flex-col gap-4 text-sm text-gray-600 leading-relaxed pr-6 mt-1 mb-2">
        <p><strong className="text-gray-800">Single OCR</strong> is optimized for deterministic, rapid throughput. It directs documents through a single designated vision-language model (e.g., Tesseract OCR or PaddleOCR) with minimal latency (sub-400ms per standard page). It is ideal for standardized invoices, high-contrast digital forms, and low-complexity text.</p>
        <p><strong className="text-gray-800">Multi-Pipeline Orchestration</strong> engages three independent AI engines concurrently (Tesseract, DocTr, and Claude Vision / Llama-3-Vision). The results undergo mathematical consensus arbitration, optical alignment scoring, and character-level discrepancy flagging. If two engines disagree, the arbitrator selects the highest confidence token and generates an anomaly audit flag for human-in-the-loop verification.</p>
        <div className="flex items-center gap-3 mt-1 bg-gray-50/50 p-2 rounded-lg border border-gray-100 self-start">
           <Badge className="bg-blue-50 text-blue-700 border-none px-2 font-mono text-[0.7rem] shadow-sm">Consensus Threshold: ≥94.5%</Badge>
           <span className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest px-2">Recommended for: Classified Contracts, SF-86 forms</span>
        </div>
      </div>
    ),
    isOpen: true
  },
  {
    icon: <Flag size={16} className="text-[#3b82f6]" />,
    q: 'Why is an extraction field flagged for review or marked as partial?',
    a: 'A field is flagged when the selected pipelines disagree on its value, or when confidence is too low to resolve automatically. Flagged fields still get a best-guess consensus value, but it is worth a manual check.'
  },
  {
    icon: <Share2 size={16} className="text-[#3b82f6]" />,
    q: 'How do I share a pre-filled upload link or API endpoint with another team?',
    a: 'Use the Generate URL page: pick a department and (optionally) a template, generate a link, and copy it. Opening that link pre-fills the OCR page’s department/template selectors.'
  },
  {
    icon: <GitFork size={16} className="text-[#3b82f6]" />,
    q: 'Where do I manage departments, templates, and Pydantic schema validation?',
    a: 'The Manage page handles both: create/deactivate departments, and create/activate/deactivate/delete templates per department.'
  },
  {
    icon: <FileText size={16} className="text-[#3b82f6]" />,
    q: 'What document formats and file size limits are supported?',
    a: 'Standard supported formats are PDF, PNG, JPG, and TIFF. File size limits depend on the infrastructure, but the default is 45MB per upload.'
  },
  {
    icon: <BadgeCheck size={16} className="text-[#3b82f6]" />,
    q: 'Does DocInt require CAC/PIV authentication or DoD login clearance?',
    a: 'No — authentication is intentionally out of scope for the current deployment. This is an internal tool; treat generated links as convenience links, not access control.'
  }
];

const FaqItem = ({ icon, q, a, isOpen }) => {
  const [open, setOpen] = useState(isOpen || false);
  return (
    <div className={`border-b border-gray-100 ${open ? 'pb-2' : ''}`}>
      <button
        className="w-full flex items-center justify-between py-4 text-left hover:bg-gray-50/50 px-5 transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-3">
           {icon}
           <span className="font-bold text-gray-800 text-[0.95rem]">{q}</span>
        </div>
        <ChevronDown size={18} className="text-gray-400 transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>
      {open && <div className="px-[3.25rem]">{typeof a === 'string' ? <p className="text-sm text-gray-600 pb-2 pr-6 leading-relaxed">{a}</p> : a}</div>}
    </div>
  );
};

const HelpSupportPage = () => {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    pipelinesService.health().then((res) => setHealth(res.data)).catch(() => setHealth(null));
  }, []);

  return (
    <div className="flex flex-col gap-6 max-w-5xl mx-auto p-4 w-full min-h-full animate-fade-in-up pb-12 bg-[#f8fafc]">
      
      {/* Header */}
      <div className="flex flex-col text-center mt-6 mb-2">
        <div className="mx-auto bg-[#1e3a8a] text-white p-3 rounded-xl shadow-md mb-4">
           <HelpCircle size={28} />
        </div>
        <h1 className="text-[2rem] font-bold text-primary font-heading mb-3 tracking-tight">
          Help &amp; Knowledge Center
        </h1>
        <p className="text-gray-600 max-w-2xl mx-auto text-[0.95rem] leading-relaxed">
           Answers to operational guidelines, agency pipeline orchestration, schema validation, and secure integration status.
        </p>
      </div>

      {/* FAQS */}
      <Card className="border border-gray-200 shadow-sm overflow-hidden mb-2">
        <CardHeader className="bg-[#f8fafc] border-b border-gray-200 p-4">
           <div className="flex items-center justify-between">
             <CardTitle className="flex items-center gap-2 text-[0.95rem] text-[#1e3a8a] font-bold"><BookOpen size={18} /> Frequently Asked Questions & Operational Directives</CardTitle>
             <span className="text-[0.65rem] font-bold text-gray-400 uppercase tracking-widest">{FAQS.length} SYSTEM GUIDES</span>
           </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="flex flex-col">
             {FAQS.map((item, idx) => <FaqItem key={item.q} {...item} idx={idx} />)}
          </div>
        </CardContent>
      </Card>


      {/* Global Footer */}
      <div className="flex items-center justify-between border-t border-gray-200 pt-6 mt-4 pb-4">
         <div className="flex items-start gap-3 max-w-2xl">
            <Globe size={18} className="text-gray-400 mt-0.5" />
            <div className="flex flex-col gap-1">
               <span className="text-xs font-bold text-gray-700">DocInt Enterprise Operating Environment <span className="text-gray-400 mx-1">—</span> <span className="text-gray-500 font-normal">Authorized Federal Law Enforcement & Regulatory Agency Use Only.</span></span>
               <span className="text-[0.65rem] text-gray-400">Compliant with National Intelligence Document Governance Directive #705-B & DoD Records Act Title 44.</span>
            </div>
         </div>
         <div className="flex items-center gap-6">
            <div className="flex flex-col items-end">
               <span className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest">RELEASE:</span>
               <span className="text-xs font-mono text-gray-600">v4.2.1-PROD</span>
            </div>
            <div className="text-gray-300 text-lg">•</div>
            <div className="flex flex-col items-end">
               <span className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest">BUILD:</span>
               <span className="text-xs font-mono text-gray-600">#9842.FED</span>
            </div>
            <div className="text-gray-300 text-lg">•</div>
            <div className="text-[0.65rem] font-bold text-[#1e3a8a] uppercase tracking-widest cursor-pointer hover:underline">LEGAL<br/>ADVISORY</div>
         </div>
      </div>

    </div>
  );
};

export default HelpSupportPage;
