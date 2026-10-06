import React from 'react';
import type {RequestKind,LanguageCode,UiRequestType} from '../api';
import {t} from '../i18n';
import {useModalFocus} from '../hooks/useModalFocus';
interface Props{isOpen:boolean;kind:RequestKind;requestTypes:UiRequestType[];room:string;quantity:number;time:string;party:string;note:string;dataConsent:boolean;showConsent:boolean;language:LanguageCode;onClose:()=>void;onSave:(values:{kind:RequestKind;room:string;quantity:number;time:string;party:string;note:string;dataConsent:boolean})=>void;}
export const EditRequestModal:React.FC<Props>=({isOpen,kind,requestTypes,room,quantity,time,party,note,dataConsent,showConsent,language,onClose,onSave})=>{
 const [values,setValues]=React.useState({kind,room,quantity,time,party,note,dataConsent});
 const titleId=React.useId();
 const dialogRef=useModalFocus(isOpen,onClose);
 React.useEffect(()=>{setValues({kind,room,quantity,time,party,note,dataConsent});},[isOpen,kind,room,quantity,time,party,note,dataConsent]);
 if(!isOpen||requestTypes.length===0)return null;
 const active=requestTypes.find(item=>item.kind===values.kind)||requestTypes[0];
 const fields=new Set(active.fields);
 return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
  <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} className="bg-white max-w-lg w-full shadow-2xl border border-brand-borderLight rounded-lg max-h-[90vh] overflow-auto outline-none">
   <div className="px-6 py-4 border-b border-[#E8DFC9] bg-[#FAF7F2] flex justify-between items-start gap-3"><div><h3 id={titleId} className="font-serif font-bold text-lg text-brand-navyDark">{t(language,'editRequest')}</h3><p className="text-xs text-gray-500">{t(language,'preferences')}</p></div><button type="button" className="min-w-11 min-h-11 inline-flex items-center justify-center rounded-lg" aria-label={t(language,'close')} onClick={onClose}>✕</button></div>
   <form className="p-6 space-y-4 text-xs" onSubmit={e=>{e.preventDefault();onSave(values);}}>
    <label className="block font-semibold">{t(language,'serviceType')}<select value={values.kind} onChange={e=>setValues({...values,kind:e.target.value as RequestKind})} className="mt-1 w-full min-h-11 rounded-lg border p-2 bg-stone-50">{requestTypes.map(item=><option key={item.kind} value={item.kind}>{item.labels[language]}</option>)}</select></label>
    {(fields.has('room_number')||fields.has('quantity'))&&<div className="grid grid-cols-2 gap-3">
      {fields.has('room_number')&&<label className="block font-semibold">{t(language,'room')}<input maxLength={30} value={values.room} onChange={e=>setValues({...values,room:e.target.value})} className="block w-full min-h-11 mt-1 rounded-lg border p-2"/></label>}
      {fields.has('quantity')&&<label className="block font-semibold">{t(language,'quantity')}<input min={1} max={20} type="number" value={values.quantity} onChange={e=>setValues({...values,quantity:Number(e.target.value)})} className="block w-full min-h-11 mt-1 rounded-lg border p-2"/></label>}
    </div>}
    {(fields.has('preferred_time')||fields.has('party_size'))&&<div className="grid grid-cols-2 gap-3">
      {fields.has('preferred_time')&&<label className="block font-semibold">{t(language,'preferredTime')}<input id="request-time" type="datetime-local" value={values.time} onChange={e=>setValues({...values,time:e.target.value})} className="block w-full min-h-11 mt-1 rounded-lg border p-2"/></label>}
      {fields.has('party_size')&&<label className="block font-semibold">{t(language,'partySize')}<input id="request-party" type="number" min={1} max={30} value={values.party} onChange={e=>setValues({...values,party:e.target.value})} className="block w-full min-h-11 mt-1 rounded-lg border p-2"/></label>}
    </div>}
    <label className="block font-semibold">{t(language,'requestDetails')}<textarea required maxLength={400} value={values.note} onChange={e=>setValues({...values,note:e.target.value})} className="block w-full mt-1 rounded-lg border p-2 min-h-24" placeholder={t(language,'notePlaceholder')}/></label>
    {showConsent&&<label className="flex gap-2 items-start rounded-lg border border-[#E8DFC9] bg-[#FAF7F2] p-3"><input type="checkbox" checked={values.dataConsent} onChange={e=>setValues({...values,dataConsent:e.target.checked})} className="mt-0.5"/><span>{({vi:'Tôi đồng ý lưu thông tin cần thiết để xử lý yêu cầu này.',en:'I agree to store the information needed to process this request.',zh:'我同意保存处理此请求所需的信息。',ko:'이 요청 처리에 필요한 정보 저장에 동의합니다.'} as Record<LanguageCode,string>)[language]}</span></label>}
    <p className="text-gray-500">{t(language,'staffVerify')}</p><div className="flex justify-end gap-2"><button type="button" onClick={onClose} className="min-h-11 px-4 py-2 border rounded-lg">{t(language,'cancel')}</button><button type="submit" className="min-h-11 px-4 py-2 bg-[#142742] text-white rounded-lg">{t(language,'saveReview')}</button></div>
   </form>
  </div>
 </div>;
};
