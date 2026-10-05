import React from 'react';
import type {LanguageCode} from '../api';
import {t} from '../i18n';
import {useModalFocus} from '../hooks/useModalFocus';

interface Props {
 isOpen:boolean; language:LanguageCode; room:string;
 onClose:()=>void; onSubmit:(lastName:string,roomQrToken:string)=>void;
}

export const VerificationModal:React.FC<Props>=({isOpen,language,room,onClose,onSubmit})=>{
 const [lastName,setLastName]=React.useState('');
 const [roomQrToken,setRoomQrToken]=React.useState('');
 const titleId=React.useId();
 const dialogRef=useModalFocus(isOpen,onClose);
 React.useEffect(()=>{if(isOpen){setLastName('');setRoomQrToken('');}},[isOpen]);
 if(!isOpen)return null;
 return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
  <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} className="bg-white max-w-lg w-full rounded-lg shadow-2xl border border-brand-borderLight outline-none">
   <div className="px-6 py-4 border-b border-[#E8DFC9] bg-[#FAF7F2] flex justify-between items-center gap-3">
    <h3 id={titleId} className="font-serif font-bold text-lg text-brand-navyDark">{t(language,'verificationTitle')}</h3>
    <button type="button" className="min-w-11 min-h-11" aria-label={t(language,'close')} onClick={onClose}>✕</button>
   </div>
   <form className="p-6 space-y-4 text-xs" onSubmit={e=>{e.preventDefault();onSubmit(lastName.trim(),roomQrToken.trim());}}>
    <label className="block font-semibold">{t(language,'guestLastName')}<input autoFocus maxLength={120} value={lastName} onChange={e=>setLastName(e.target.value)} className="block w-full min-h-11 mt-1 rounded-lg border p-2" /></label>
    <label className="block font-semibold">{t(language,'roomQrToken')}<input maxLength={512} value={roomQrToken} onChange={e=>setRoomQrToken(e.target.value)} className="block w-full min-h-11 mt-1 rounded-lg border p-2" /></label>
    <p className="text-gray-500">{t(language,'verificationSkip')}</p>
    <div className="flex justify-end gap-2"><button type="button" onClick={onClose} className="min-h-11 px-4 py-2 border rounded-lg">{t(language,'cancel')}</button><button type="submit" className="min-h-11 px-4 py-2 bg-[#142742] text-white rounded-lg">{t(language,'verificationSubmit')}</button></div>
   </form>
  </div>
 </div>;
};
