import React from 'react';
import type {RequestItem} from '../types';
import type {LanguageCode} from '../api';
import {t} from '../i18n';
interface Props{tickets:RequestItem[];selectedId:string|null;language:LanguageCode;onSelectTicket:(id:string)=>void;}
export const MyRequests:React.FC<Props>=({tickets,selectedId,language,onSelectTicket})=><div className="bg-white border border-brand-borderLight shadow-sm rounded-lg p-3 space-y-3" data-purpose="my-requests-card">
 <div className="flex items-center justify-between pb-1 border-b border-gray-100"><h3 className="font-serif font-semibold text-base text-brand-navyDark">{t(language,'myRequests')}</h3><span className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">{tickets.length} {t(language,'inSession')}</span></div>
 <div className="space-y-2">{tickets.length===0&&<p className="text-xs text-gray-500">{t(language,'noRequests')}</p>}{tickets.map(ticket=><button type="button" key={ticket.id} onClick={()=>onSelectTicket(ticket.id)} className={`w-full min-h-11 text-left flex items-center justify-between gap-2 p-2.5 bg-[#FAF7F2] rounded-xl border ${selectedId===ticket.id?'border-[#73532C]':'border-[#E9E1D2]'} hover:bg-white`}><div className="min-w-0"><h4 className="font-semibold text-xs text-brand-navyDark">{ticket.title}</h4><div className="text-[10px] font-mono text-[#73532C] break-all">{ticket.id}</div><div className="text-[10px] text-gray-600 mt-0.5">{ticket.subtitle}</div></div><span className="shrink-0 text-[9px] rounded px-2 py-1 bg-[#F8EBD8] text-[#845618]">{ticket.statusText}</span></button>)}</div>
 </div>;
