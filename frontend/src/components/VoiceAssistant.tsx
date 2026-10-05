import React from 'react';
import type {LanguageCode} from '../api';
import type {VoiceStatus} from '../hooks/useVoiceSession';
import {t} from '../i18n';
interface Props { enabled:boolean; available:boolean; state:VoiceStatus; message:string; language:LanguageCode; volume:number; onToggle:()=>void; }
export const VoiceAssistant:React.FC<Props>=({enabled,available,state,message,language,volume,onToggle})=>{
 const statusKey=state==='playing'?'voicePlaying':state==='processing'||state==='recovering'?'voiceProcessing':state==='speaking'||state==='interrupted'?'voiceSpeaking':state==='ready'?'voiceListening':'voiceStarting';
 const voiceStatus=enabled?t(language,statusKey):t(language,'voicePaused');
 return <div className="relative bg-white border border-brand-borderLight shadow-sm flex flex-col items-center p-3 rounded-lg shrink-0" data-purpose="voice-assistant-card">
  <h3 className="font-serif font-semibold text-base text-brand-navyDark">{t(language,'voiceAssistant')}</h3>
  <p className="text-[11px] text-brand-textMuted mt-0.5">{t(language,'voiceDescription')}</p>
  <div className="relative flex items-center justify-center my-3 w-24 h-24">
   {enabled&&<div aria-hidden="true" className="absolute w-24 h-24 rounded-full bg-amber-400/20 animate-ping"/>}
   <button type="button" aria-label={t(language,enabled?'stopMic':'startMic')} aria-pressed={enabled} disabled={!available} onClick={onToggle} className="relative z-10 w-20 h-20 rounded-full bg-[#11243A] text-white flex items-center justify-center shadow-lg border-4 border-[#FAF5EC] disabled:opacity-40 text-2xl">{enabled?'◉':'🎙'}</button>
  </div>
  <p role="status" aria-live="polite" className="text-xs text-center font-semibold text-[#142742]">{available?voiceStatus:t(language,'voiceUnavailable')}</p>
  <div className="w-24 h-1.5 rounded-full bg-slate-200 overflow-hidden" role="meter" aria-label="Microphone level" aria-valuemin={0} aria-valuemax={1} aria-valuenow={enabled?volume:0}>
   <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-75" style={{width:`${enabled?Math.round(volume*100):0}%`}}/>
  </div>
  {message&&<p role="status" aria-live="polite" className="text-[10px] text-gray-500 mt-1 text-center">{message}</p>}
  <p className="text-[10px] text-gray-500 mt-2">{t(language,'micPermission')}</p>
 </div>;
};
