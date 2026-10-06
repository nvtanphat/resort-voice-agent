import React, {useEffect, useRef} from 'react';
import type {ChatMessage} from '../types';
import type {LanguageCode, MapPlace, RequestKind} from '../api';
import {localeFor,t,taskLabel} from '../i18n';
import MarkdownText from './MarkdownText';
interface ChatSectionProps {
  propertyName:string; language:LanguageCode; messages:ChatMessage[]; value:string;
  onChange:(text:string)=>void; onSendMessage:(text:string)=>void; isThinking:boolean;
  ready:boolean; error:string|null; onRetry:()=>void;
  onSuggested:(kind:RequestKind,details:string,service?:string)=>void; kindLabel:(kind:RequestKind)=>string;
  onStartRequest:()=>void; canCreateRequest:boolean; onVoice:()=>void; voiceAvailable:boolean;
  mapPlaces:MapPlace[]; startLocation:string; onStartLocation:(id:string)=>void;
  requestPreview:string|null; prepared:boolean; pending:boolean;
  priceDisclosure:string; priceDisclosureRequired:boolean; priceAcknowledged:boolean; onPriceAcknowledged:(value:boolean)=>void;
  outsideOperatingHours:boolean; nextOpenAt:number|null;
  onOpenEditModal:()=>void; onPrepare:()=>void; onConfirm:()=>void; onCancel:()=>void;
}
export const ChatSection:React.FC<ChatSectionProps>=({propertyName,language,messages,value,onChange,onSendMessage,isThinking,ready,error,onRetry,onSuggested,kindLabel,onStartRequest,canCreateRequest,onVoice,voiceAvailable,mapPlaces,startLocation,onStartLocation,requestPreview,prepared,pending,priceDisclosure,priceDisclosureRequired,priceAcknowledged,onPriceAcknowledged,outsideOperatingHours,nextOpenAt,onOpenEditModal,onPrepare,onConfirm,onCancel})=>{
 const bottom=useRef<HTMLDivElement>(null);
 const nextOpenLabel=nextOpenAt?new Date(nextOpenAt*1000).toLocaleString(localeFor(language)):'';
 useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[messages.length,isThinking,prepared]);
 const send=(event:React.FormEvent)=>{event.preventDefault();if(ready&&!isThinking&&value.trim().length>=2)onSendMessage(value.trim());};
 return <section className="lg:col-span-6 flex flex-col h-full min-h-0 overflow-hidden space-y-2.5" data-purpose="chat-concierge-center">
  <div className="relative px-5 py-3 border border-brand-borderLight shadow-sm overflow-hidden flex-shrink-0 rounded-lg bg-white/80">
   <div className="absolute -top-6 -right-6 w-40 h-40 opacity-30 pointer-events-none palm-watermark"/>
   <span className="text-[10px] tracking-[0.25em] font-semibold text-[#8E764E] uppercase">{t(language,'welcomeTo')}</span>
   <h1 className="font-serif text-[22px] lg:text-[24px] font-semibold text-brand-navyDark leading-tight">{propertyName}</h1>
   <p className="text-xs text-brand-textMuted mt-0.5">{t(language,'intro')}</p>
  </div>
  {error&&<div role="alert" className="bg-rose-50 text-rose-800 rounded-lg border border-rose-200 p-3 text-xs flex justify-between gap-2"><span>{error}</span><button onClick={onRetry} className="min-h-11 px-2 underline font-semibold">{t(language,'reconnect')}</button></div>}
  <div className="flex-1 min-h-0 flex flex-col space-y-3 custom-scrollbar overflow-y-auto pr-1" data-purpose="chat-messages-container" aria-live="polite">
   {messages.length===0&&<div className="text-xs text-gray-500 bg-white/70 border border-brand-borderLight p-4 rounded-xl">{t(language,ready?'choose':'connecting')}</div>}
   {messages.map(msg=><div key={msg.id} className={`flex items-start gap-2.5 ${msg.sender==='user'?'justify-end pl-10':'pr-2'}`}>
    {msg.sender==='assistant'&&<span aria-hidden="true" className="w-8 h-8 rounded-lg bg-[#3C5B82] text-white flex items-center justify-center shrink-0">✧</span>}
    <div className={`max-w-full min-w-0 ${msg.sender==='user'?'bg-[#EBF3FA] border-[#D8E6F5] rounded-tr-none':'bg-white/85 border-[#E9E1D2]'} border rounded-xl px-4 py-3 shadow-sm text-xs leading-relaxed`}>
     {msg.sender==='user'?<p className="whitespace-pre-wrap break-words">{msg.text}</p>:<>{!!msg.answerTitle&&<p className="text-[10px] font-semibold text-brand-navy mb-1">{msg.answerTitle}</p>}<MarkdownText className="whitespace-pre-wrap break-words" text={msg.text}/></>}
     {msg.sender==='assistant'&&<>
      {!!msg.evidenceStatus&&<p className="text-[10px] mt-2 text-gray-500">{t(language,'evidence')}: {msg.evidenceStatus}{msg.omittedClaims?` · ${msg.omittedClaims} ${t(language,'unsupported')}`:''}</p>}
      {!!msg.citations?.length&&<details className="mt-2 text-[11px] rounded bg-[#FAF7F2] p-2"><summary className="cursor-pointer font-semibold text-brand-navy">{t(language,'sources')} ({msg.citations.length})</summary>
       {msg.citations.map(c=><div key={c.citation_id} className="border-t border-[#E9E1D2] mt-2 pt-2"><strong>{c.title} · {c.revision}</strong><MarkdownText className="whitespace-pre-wrap mt-1" text={c.quote}/><small>{t(language,'source')}: {c.source_id} · {c.heading}</small></div>)}
      </details>}
      {msg.mapGuidance?.status==='verified'&&<div className="mt-2 rounded border border-[#E9E1D2] p-2"><strong>{msg.mapGuidance.origin} → {msg.mapGuidance.destination}</strong><ol className="list-decimal pl-5 mt-1">{msg.mapGuidance.steps?.map((step,i)=><li key={i}>{step}</li>)}</ol></div>}
      {!!msg.plan&&<details className="mt-2 rounded border border-[#E9E1D2] p-2"><summary className="cursor-pointer font-semibold">{t(language,'draftPlan')}</summary>{msg.plan.activities.map((a,i)=><p key={i} className="mt-1">{a.suggested_time?<strong>{a.suggested_time.start}–{a.suggested_time.end} · </strong>:null}{a.description}</p>)}<p className="mt-2 text-amber-800">{t(language,'bookingDisclaimer')}</p></details>}
      {!!msg.taskProgress?.length&&<div className="mt-2 border-t border-[#E9E1D2] pt-2">{msg.taskProgress.map(task=><p key={task.id}>{(['knowledge','navigation','planning'] as string[]).includes(task.kind)?taskLabel(language,task.kind as 'knowledge'|'navigation'|'planning'):kindLabel(task.kind as RequestKind)}: {taskLabel(language,task.status)}</p>)}</div>}
      {!!msg.agentProgress?.length&&<details className="mt-2 rounded border border-[#E9E1D2] bg-[#FAF7F2] p-2"><summary className="cursor-pointer font-semibold text-brand-navy">{t(language,'agentProgress')}</summary><ol className="mt-1 space-y-1 list-decimal pl-4">{msg.agentProgress.map(item=><li key={`${item.step}:${item.requirement_id||''}`}><span className="font-medium">{item.capability||t(language,'agentStep')}</span><span className="text-gray-500"> · {item.status}</span></li>)}</ol></details>}
      {!!msg.supportContact&&<div className="mt-2 rounded border border-[#D9E5D7] bg-[#F6FAF5] p-2"><strong>{t(language,'supportContact')}: {msg.supportContact.label}</strong>{!!msg.supportContact.extensions.length&&<p>{t(language,'extension')}: {msg.supportContact.extensions.join(' / ')}</p>}{!!msg.supportContact.phones.length&&<p>{t(language,'phone')}: {msg.supportContact.phones.join(' / ')}</p>}{msg.supportContact.email&&<p>{t(language,'email')}: {msg.supportContact.email}</p>}</div>}
      {!!msg.relatedTopics?.length&&<div className="mt-2"><p className="text-[11px] font-semibold text-brand-navy">{t(language,'relatedTopics')}</p><div className="flex flex-wrap gap-2 mt-1">{msg.relatedTopics.map(topic=><button key={`${topic.language}:${topic.label}`} type="button" onClick={()=>onSendMessage(topic.query)} className="min-h-11 border border-[#D8E6F5] bg-[#F4F8FC] rounded-full px-3 py-1.5 hover:bg-[#EAF2FA]">{topic.label}</button>)}</div></div>}
      {!!msg.suggestedAction&&<button type="button" onClick={()=>onSuggested(msg.suggestedAction!.kind,msg.suggestedAction!.details,msg.suggestedAction!.service)} className="mt-2 min-h-11 rounded-full border border-[#B89B6A] text-[#73532C] px-3 py-1.5 hover:bg-[#F8F1E7]">{t(language,'reviewKind').replace('{kind}',kindLabel(msg.suggestedAction.kind))}</button>}
      {!!msg.actionOptions?.length&&<div className="flex flex-wrap gap-2 mt-2">{msg.actionOptions.map((a,i)=><button key={i} type="button" onClick={()=>onSuggested(a.kind,'')} className="min-h-11 border border-stone-300 rounded-full px-3 py-1 hover:bg-stone-50">{kindLabel(a.kind)}</button>)}</div>}
     </>}
     <div className="mt-1 text-[10px] text-gray-400 text-right">{msg.time}</div>
    </div>
   </div>)}
   {isThinking&&<div role="status" className="text-xs text-brand-navy bg-white border border-[#E9E1D2] rounded-xl p-3 self-start">{t(language,'consulting')}</div>}
   {requestPreview&&<div className="border border-[#E3D7C5] bg-[#F6EFE3] p-3.5 shadow-sm space-y-2 rounded-lg" data-purpose="review-request-card">
    <div className="flex justify-between gap-2 items-center"><div><h3 className="font-serif font-bold text-base text-brand-navyDark">{t(language,'reviewRequest')}</h3><p className="text-[11px] text-gray-600">{t(language,'reviewDisclaimer')}</p></div>
     {!prepared&&<button disabled={pending} onClick={onOpenEditModal} className="min-h-11 text-xs border bg-white rounded-full px-3 py-1">{t(language,'edit')}</button>}</div>
    <p className="whitespace-pre-wrap break-words bg-white/70 rounded-lg border border-[#E9E1D2] p-3 text-xs">{requestPreview}</p>
    {prepared&&priceDisclosureRequired&&<div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs space-y-2"><p>{priceDisclosure}</p><label className="flex gap-2 items-start"><input type="checkbox" checked={priceAcknowledged} onChange={event=>onPriceAcknowledged(event.target.checked)} className="mt-0.5"/><span>{t(language,'priceDisclosureAcknowledgement')}</span></label></div>}
    {prepared&&outsideOperatingHours&&<p className="rounded-lg border border-sky-200 bg-sky-50 p-3 text-xs">{t(language,'outsideOperatingHours').replace('{time}',nextOpenLabel||t(language,'staffVerify'))}</p>}
    {prepared?<div className="flex flex-wrap gap-2"><button disabled={pending||(priceDisclosureRequired&&!priceAcknowledged)} onClick={onConfirm} className="min-h-11 bg-[#75552D] text-white rounded-lg text-xs py-2 px-4 disabled:opacity-50">{t(language,pending?'processing':'confirmStaff')}</button><button disabled={pending} onClick={onCancel} className="min-h-11 border border-[#73532C] rounded-lg text-xs py-2 px-4">{t(language,'discard')}</button></div>:<div className="flex flex-wrap gap-2"><button disabled={pending} onClick={onPrepare} className="min-h-11 bg-[#75552D] text-white rounded-lg text-xs py-2 px-4 disabled:opacity-50">{t(language,pending?'preparing':'prepare')}</button><button disabled={pending} onClick={onStartRequest} className="min-h-11 border border-[#73532C] rounded-lg text-xs py-2 px-4">{t(language,'editDetails')}</button></div>}
   </div>}
   {!requestPreview&&ready&&canCreateRequest&&<button className="self-start min-h-11 border border-[#E9E1D2] bg-white/75 text-[#73532C] rounded-full px-4 py-2 text-xs" onClick={onStartRequest}>{t(language,'createRequest')}</button>}
   <div ref={bottom}/>
  </div>
  {mapPlaces.length>0&&<label className="text-[11px] text-gray-600">{t(language,'mapOrigin')} <select className="rounded border p-1 bg-white ml-1" value={startLocation} onChange={e=>onStartLocation(e.target.value)}><option value="">{t(language,'default')}</option>{mapPlaces.map(p=><option value={p.id} key={p.id}>{p.label}</option>)}</select></label>}
  <form onSubmit={send} className="p-1.5 pl-4 border border-[#E0D8C8] shadow-sm flex items-center gap-3 shrink-0 rounded-lg bg-white/85" data-purpose="chat-input-bar">
   <input aria-label={t(language,'askConcierge')} value={value} maxLength={500} disabled={!ready||isThinking} onChange={e=>onChange(e.target.value)} className="flex-1 min-w-0 text-xs py-2 outline-none bg-transparent" placeholder={t(language,'askPlaceholder')}/>
   <button type="button" onClick={onVoice} disabled={!ready||!voiceAvailable} title={t(language,'voiceToggle')} className="min-w-11 min-h-11 text-[#73532C] disabled:opacity-40 text-xs px-2">🎙</button>
   <button type="submit" disabled={!ready||isThinking||value.trim().length<2} className="min-h-11 bg-[#142742] text-white rounded-lg py-2 px-4 text-xs disabled:opacity-40">{t(language,'send')}</button>
  </form>
 </section>;
};
