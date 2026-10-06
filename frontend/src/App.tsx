import React, {useCallback,useEffect,useRef,useState} from 'react';
import type {LanguageCode,RequestKind,KioskConfig,Citation} from './api';
import * as api from './api';
import {t,statusLabel} from './i18n';
import {ContinuousVoice} from './continuousVoice';
import {VoiceAgent} from './voiceAgent';
import {useVoiceSession} from './hooks/useVoiceSession';
import {useTurnLifecycle} from './hooks/useTurnLifecycle';
import {useSpeechPlayback} from './hooks/useSpeechPlayback';
import type {RequestItem,ChatMessage,ServiceItem} from './types';
import {Header} from './components/Header';
import {SidebarNav} from './components/SidebarNav';
import {ChatSection} from './components/ChatSection';
import {VoiceAssistant} from './components/VoiceAssistant';
import {MyRequests} from './components/MyRequests';
import {EditRequestModal} from './components/EditRequestModal';
import {TicketModal} from './components/TicketModal';
import {VerificationModal} from './components/VerificationModal';

function clock(lang:LanguageCode){return new Date().toLocaleTimeString(lang,{hour:'2-digit',minute:'2-digit'});}
function uuid(): string {
 const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
 bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
}
function abortableDelay(ms:number,signal:AbortSignal):Promise<void>{
 if(signal.aborted)return Promise.reject(new DOMException('Cancelled','AbortError'));
 return new Promise((resolve,reject)=>{
  const done=()=>{signal.removeEventListener('abort',cancel);resolve();};
  const id=setTimeout(done,ms);
  const cancel=()=>{clearTimeout(id);signal.removeEventListener('abort',cancel);reject(new DOMException('Cancelled','AbortError'));};
  signal.addEventListener('abort',cancel,{once:true});
 });
}

export const App:React.FC=()=>{
 const [selectedLanguage,setSelectedLanguage]=useState<LanguageCode>('vi');
 const [serverConfig,setServerConfig]=useState<KioskConfig|null>(null);
 const [uiContract,setUiContract]=useState<api.UiContract|null>(null);
 const [serviceItems,setServiceItems]=useState<ServiceItem[]>([]);
 const [allowedRequestKinds,setAllowedRequestKinds]=useState<RequestKind[]>([]);
 const [selectedServiceId,setSelectedServiceId]=useState('');
 const [mapPlaces,setMapPlaces]=useState<api.MapPlace[]>([]);
 const [startLocation,setStartLocation]=useState('');
 const [sessionReady,setSessionReady]=useState(false);
 const [toastMessage,setToastMessage]=useState<string|null>(null);
 const [dataConsent,setDataConsent]=useState(false);
 const [latestStatus,setLatestStatus]=useState<{code:string;url:string;qr:string}|null>(null);
 const [now,setNow]=useState(new Date());
 const [requestType,setRequestType]=useState<RequestKind|null>(null);
 const [quantity,setQuantity]=useState(1);
 const [roomNumber,setRoomNumber]=useState('');
 const [requestTime,setRequestTime]=useState('');
 const [partySize,setPartySize]=useState('');
 const [note,setNote]=useState('');
 const [suggestedDetails,setSuggestedDetails]=useState<{kind:RequestKind;details:string}|null>(null);
 const [pendingProposal,setPendingProposal]=useState<{proposal_id:string;kind:RequestKind;details:string;expires_at:number;staff_verification_required?:boolean;price_disclosure_required?:boolean;price_disclosure?:string;outside_operating_hours?:boolean;next_open_at?:number|null}|null>(null);
 const [priceAcknowledged,setPriceAcknowledged]=useState(false);
 const [submitting,setSubmitting]=useState(false);
 const [verificationModalOpen,setVerificationModalOpen]=useState(false);
 const [isEditModalOpen,setIsEditModalOpen]=useState(false);
 const [isThinking,setIsThinking]=useState(false);
 const [inputText,setInputText]=useState('');
 const [messages,setMessages]=useState<ChatMessage[]>([]);
 const [requestsList,setRequestsList]=useState<RequestItem[]>([]);
 const [expandedRequest,setExpandedRequest]=useState<string|null>(null);
 const expandedRequestRef=useRef<string|null>(null);
 const [requestProgress,setRequestProgress]=useState<api.GuestRequestProgress|null>(null);
 const [requestProgressError,setRequestProgressError]=useState('');
 const [changeTargetId,setChangeTargetId]=useState<string|null>(null);
 const [changeBusy,setChangeBusy]=useState(false);
 const [openCitation,setOpenCitation]=useState<string|null>(null);
 const [errorText,setErrorText]=useState<string|null>(null);
 const [lastQuery,setLastQuery]=useState('');
 const [voiceVolume,setVoiceVolume]=useState(0);
 const {state:voiceUI,enable:enableVoice,disable:disableVoice,setStatus:setVoiceStatus,setEngineState:setVoiceEngineState,setMessage:setVoiceMessage,interrupt:setVoiceInterrupted,recovering:setVoiceRecovering}=useVoiceSession();
 const {versionRef:version,currentTurnRef:currentTurn,beginRequest,invalidateRequest,clearController}=useTurnLifecycle();
 const sessionEpoch=useRef(0), pendingRefresh=useRef(0), connectAbort=useRef<AbortController|null>(null);
 const draftNonce=useRef<string|null>(null);
 const {
  playbackRef:playback,pausedForSpeechRef:pausedForSpeech,pauseRecoveryRef:pauseRecovery,
  bargeProbeRevisionRef:bargeProbeRevision,bargePauseStartedRef:bargePauseStarted,
  stopPlayback,playPlan:playSpeechPlan,
 }=useSpeechPlayback();
 const langRef=useRef<LanguageCode>('vi'), previousRef=useRef('');
 const languageRevision=useRef(0), userSelectedLanguage=useRef(false);
 const voiceEngine=useRef<ContinuousVoice|null>(null), voiceEpoch=useRef(0);
 const pipecatAgent=useRef<VoiceAgent|null>(null);
 const voiceTransport=useRef<AbortController|null>(null);
 const playbackTransport=useRef<AbortController|null>(null);
 const greetedRef=useRef(false);
 // Serialize turn allocations: a delayed, now-obsolete /turn/start must never
 // arrive after a newer one and invalidate the guest's latest spoken turn.
 const turnStartQueue=useRef<Promise<void>>(Promise.resolve());
 // Timings are anonymous observations only: no query, transcript or room data.
 const observeVoice=(stage:Parameters<typeof api.reportVoiceLatency>[0],ms:number,language:LanguageCode)=>{
   void api.reportVoiceLatency(stage,ms,language,uuid()).catch(()=>{});
 };
 const requestTypeMeta=(kind:RequestKind|null)=>kind?uiContract?.request_types.find(item=>item.kind===kind):undefined;
 const requestKindLabel=(kind:RequestKind,language=langRef.current)=>requestTypeMeta(kind)?.labels[language]||kind;
 const requestFields=(kind:RequestKind|null)=>new Set(requestTypeMeta(kind)?.fields||[]);

 const activeRef=useRef(false), lastActivity=useRef(Date.now());
 const showToast=(message:string)=>setToastMessage(message);
 useEffect(()=>{langRef.current=selectedLanguage;document.documentElement.lang=selectedLanguage;},[selectedLanguage]);
 useEffect(()=>{const offline=()=>setErrorText(t(langRef.current,'networkLost'));const online=()=>setErrorText(t(langRef.current,'recovery'));window.addEventListener('offline',offline);window.addEventListener('online',online);return()=>{window.removeEventListener('offline',offline);window.removeEventListener('online',online);};},[]);
 useEffect(()=>{const id=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(id);},[]);
 useEffect(()=>{if(!toastMessage)return;const id=setTimeout(()=>setToastMessage(null),5000);return()=>clearTimeout(id);},[toastMessage]);
 const invalidate=useCallback(()=>{connectAbort.current?.abort();connectAbort.current=null;voiceEpoch.current++;activeRef.current=false;voiceTransport.current?.abort();voiceTransport.current=null;playbackTransport.current?.abort();playbackTransport.current=null;voiceEngine.current?.stop();voiceEngine.current=null;void pipecatAgent.current?.stop();pipecatAgent.current=null;disableVoice();invalidateRequest();
  const turn=currentTurn.current;currentTurn.current=null;if(turn) void api.cancelVoiceTurn(turn).catch(()=>{});
  stopPlayback();setIsThinking(false);setVoiceMessage('');},[disableVoice,invalidateRequest,stopPlayback,setVoiceMessage]);
 const expireIfAuth=(error:unknown)=>{if(error instanceof api.ApiError&&[401,403].includes(error.status)){invalidate();setSessionReady(false);setErrorText(t(langRef.current,'sessionExpired'));return true;}return false;};
 const refreshRequests=useCallback(async()=>{const epoch=sessionEpoch.current;const seq=++pendingRefresh.current;try {const result=await api.myRequests();
  if(epoch!==sessionEpoch.current||seq!==pendingRefresh.current)return;
  setRequestsList(result.items.map(req=>{
    const changeText=req.guest_change_state==='cancel_requested'?t(langRef.current,'cancelPending'):
      req.guest_change_state==='modify_requested'?t(langRef.current,'modifyPending'):
      req.guest_change_state==='cancelled'?t(langRef.current,'cancelAccepted'):
      req.guest_change_state==='modified'?t(langRef.current,'modifyAccepted'):
      req.guest_change_state==='change_rejected'?t(langRef.current,'changeRejected'):null;
    return {id:req.id,title:requestKindLabel(req.kind),
      subtitle:new Date(req.updated_at*1000).toLocaleString(langRef.current),status:req.status,kind:req.kind,
      statusText:changeText||statusLabel(langRef.current,req.status),
      statusIcon:req.guest_change_state==='cancelled'?'✕':req.guest_change_state?.endsWith('_requested')?'⏱':req.status==='rejected'?'✕':req.status==='pending_staff'?'⏱':'✓',iconType:requestTypeMeta(req.kind)?.icon_category||'info'};}));
  const selected=expandedRequestRef.current;
  if(selected){try{const progress=await api.requestProgress(selected);
    if(epoch===sessionEpoch.current&&selected===expandedRequestRef.current)setRequestProgress(progress);
   }catch{if(epoch===sessionEpoch.current&&selected===expandedRequestRef.current)setRequestProgressError(t(langRef.current,'progressError'));}}
 }catch(err){if(epoch!==sessionEpoch.current)return;if(err instanceof api.ApiError&&[401,403].includes(err.status)){invalidate();setSessionReady(false);setErrorText(t(langRef.current,'sessionExpired'));}else if(!navigator.onLine)setErrorText(t(langRef.current,'networkLost'));}},[invalidate]);
 const connect=useCallback(async()=>{sessionEpoch.current++;invalidate();const languageAtStart=languageRevision.current;const init=new AbortController();connectAbort.current=init;setSessionReady(false);setErrorText(null);setMessages([]);setRequestsList([]);setMapPlaces([]);setStartLocation('');
 greetedRef.current=false;
 expandedRequestRef.current=null;setExpandedRequest(null);setRequestProgress(null);setRequestProgressError('');
  previousRef.current='';setLastQuery('');setPendingProposal(null);setPriceAcknowledged(false);setSuggestedDetails(null);draftNonce.current=null;
  setInputText('');setNote('');setRoomNumber('');setRequestTime('');setPartySize('');setQuantity(1);setRequestType(null);setAllowedRequestKinds([]);setUiContract(null);setSelectedServiceId('');setIsEditModalOpen(false);setChangeTargetId(null);setChangeBusy(false);setOpenCitation(null);setToastMessage(null);
  const epoch=sessionEpoch.current;
  let configuredOrigin='http://localhost:8000';
  try{const [settings,catalog,ui]=await Promise.all([api.config(),api.services(),api.uiContract()]);if(epoch!==sessionEpoch.current)return;await api.startSession(init.signal);if(epoch!==sessionEpoch.current||init.signal.aborted)return;
   configuredOrigin=settings.public_origin||configuredOrigin;
   const defaultLanguage=(settings.default_language&&settings.enabled_languages?.includes(settings.default_language))?settings.default_language:langRef.current;
   const profileLanguage=(!userSelectedLanguage.current&&languageRevision.current===languageAtStart)?defaultLanguage:langRef.current;
   langRef.current=profileLanguage;setSelectedLanguage(profileLanguage);
   const backendKinds=ui.request_types.filter(item=>item.actions.includes('create_request')).map(item=>item.kind);
   setServerConfig(settings);setUiContract(ui);serverCatalog.current=catalog.items;setAllowedRequestKinds(backendKinds);
   if(backendKinds.length)setRequestType(backendKinds[0]);
   setServiceItems(catalog.items.map(item=>({id:item.id,requestKind:item.request_kind,
     title:item.title[profileLanguage],description:item.question[profileLanguage],question:item.question[profileLanguage],iconCategory:item.icon_category,actions:item.actions})));setSessionReady(true);
   await refreshRequests();
  }catch(err){if(epoch===sessionEpoch.current&&!init.signal.aborted){
   const wrongOrigin=err instanceof api.ApiError&&err.status===403&&
     (err.message==='Origin not allowed'||window.location.hostname==='127.0.0.1'||window.location.hostname==='0.0.0.0');
   setErrorText(wrongOrigin?t(langRef.current,'originMismatch').replace('{origin}',configuredOrigin):!navigator.onLine?t(langRef.current,'networkLost'):err instanceof api.RequestTimeoutError?t(langRef.current,'timeout'):t(langRef.current,'recovery'));
  }}finally{if(connectAbort.current===init)connectAbort.current=null;}},[invalidate,refreshRequests]);
 useEffect(()=>{void connect();return()=>{invalidate();};},[connect,invalidate]);
 // An operator-pinned, currently authorized map is the only source of
 // selectable starting points. Re-check on language/session changes.
 useEffect(()=>{
   if(!sessionReady)return;
   let live=true;
   void api.mapPlaces(selectedLanguage).then(result=>{
     if(!live)return;
     const places=result.status==='verified'?result.places:[];
     setMapPlaces(places);
     setStartLocation(old=>places.some(p=>p.id===old)?old:(result.default_origin_id||''));
   }).catch(()=>{if(live){setMapPlaces([]);setStartLocation('');}});
   return()=>{live=false;};
 },[sessionReady,selectedLanguage]);
 useEffect(()=>{setServiceItems(items=>items.map(item=>({ ...item,
   title:serverCatalog.current?.find(s=>s.id===item.id)?.title[selectedLanguage]||item.title,
   description:serverCatalog.current?.find(s=>s.id===item.id)?.question[selectedLanguage]||item.description,
   question:serverCatalog.current?.find(s=>s.id===item.id)?.question[selectedLanguage]||item.question,
  })));},[selectedLanguage]);
 const serverCatalog=useRef<api.ServiceInfo[]>([]);
 useEffect(()=>{if(sessionReady)void refreshRequests();},[selectedLanguage,sessionReady,refreshRequests]);
 useEffect(()=>{if(sessionReady){let running=true;const id=setInterval(()=>{if(running)void refreshRequests();},12000);
  return()=>{running=false;clearInterval(id);};}},[sessionReady,refreshRequests]);
 const handleEndSession=async()=>{if(!window.confirm(t(selectedLanguage,'startOver')))return;invalidate();
  try{await api.endSession();}catch{}await connect();};
 const handleSelectService=(service:ServiceItem)=>{setSelectedServiceId(service.id);if(service.requestKind)setRequestType(service.requestKind);
  if(service.question)void handleSendMessage(service.question);};
 const applySuggested=(kind:RequestKind,details:string)=>{if(!allowedRequestKinds.includes(kind))return;if(pendingProposal){showToast(t(selectedLanguage,'cancel'));return;}setRequestType(kind);setSuggestedDetails(details.trim()?{kind,details}:null);setNote(details);draftNonce.current=null;
  if(details.trim())showToast(t(selectedLanguage,'reviewRequest'));else setIsEditModalOpen(true);};
 const handleSendMessage=async(textToSend?:string,voiceTurn?:string,voiceSignal?:AbortSignal,turnLanguage?:LanguageCode,
                               endedAt?:number,source:'dialogue'|'sos_button'='dialogue')=>{
  const text=(textToSend??inputText).trim();if(!sessionReady||!text||text.length<2)return;
  const language=turnLanguage??langRef.current;
  const localeRevision=languageRevision.current;
  // Typing while Voice Mode is listening invalidates an in-flight STT turn,
  // but deliberately leaves the microphone open for the next utterance.
  if(!voiceTurn && activeRef.current){voiceEpoch.current++;voiceTransport.current?.abort();playbackTransport.current?.abort();}
  if(text.length>500){showToast(t(language,'maxQuestion'));return;}
  const {version:turnVersion,controller:requestController}=beginRequest();stopPlayback();
  if(currentTurn.current&&!voiceTurn)void api.cancelVoiceTurn(currentTurn.current).catch(()=>{});
  currentTurn.current=voiceTurn||null;setInputText('');setIsThinking(true);setVoiceMessage(t(language,'processing'));
  const previous=previousRef.current;
  setMessages(items=>[...items,{id:uuid(),sender:'user',time:clock(language),text}]);
  const askStarted=performance.now();
  const actionNonce=uuid();
  let lifecycleClosed=false;
  const lifecyclePoll=voiceTurn?(async()=>{
    let after=0;
    while(!lifecycleClosed&&!requestController.signal.aborted&&version.current===turnVersion&&languageRevision.current===localeRevision){
      try{
        const journal=await api.turnEvents(voiceTurn,after,requestController.signal);
        after=journal.latest_sequence;
        for(const event of journal.events){
          if(version.current!==turnVersion||lifecycleClosed)break;
          if(event.type==='router.decided')setVoiceMessage(t(language,'voiceProcessing'));
          else if(event.type==='retrieval.completed')setVoiceMessage(t(language,'consulting'));
          else if(event.type==='agent.plan.started'||event.type==='agent.replanned')setVoiceMessage(t(language,'consulting'));
          else if(event.type==='agent.step.completed')setVoiceMessage(t(language,'consulting'));
          else if(event.type==='response.approved')setVoiceMessage(t(language,'voicePlaying'));
        }
        if(journal.terminal)break;
      }catch(error){
        if(error instanceof api.ApiError&&[401,403,409].includes(error.status))break;
      }
      try{await abortableDelay(180,requestController.signal);}catch{break;}
    }
  })():null;
  try{const result=await api.ask(text,language,previous,requestController.signal,voiceTurn,startLocation||undefined,actionNonce,source);
   if(version.current!==turnVersion||languageRevision.current!==localeRevision)return;
   if(voiceTurn)observeVoice('client.ask',performance.now()-askStarted,language);
   if(voiceTurn&&result.speech_turn_id!==voiceTurn)throw new Error('Superseded voice response');
   const switchedLanguage=result.session_update?.language;
   if(switchedLanguage&&switchedLanguage!==langRef.current&&
      (uiContract?.languages.map(item=>item.code)||serverConfig?.enabled_languages||serverConfig?.languages||[]).includes(switchedLanguage)){
     // This update belongs to the accepted current turn, so do not increment
     // languageRevision here: doing so would incorrectly invalidate the same
     // response and its authorized speech plan. Future turns use the new locale.
     userSelectedLanguage.current=true;langRef.current=switchedLanguage;setSelectedLanguage(switchedLanguage);
   }
   previousRef.current=text;setLastQuery(text);
   const clearSuggestions=result.clear_suggestions===true;
   setMessages(items=>[...(clearSuggestions?items.map(item=>({...item,suggestedAction:undefined})):items),{id:uuid(),sender:'assistant',time:clock(language),text:result.answer,answerTitle:result.answer_title,
     citations:result.citations||[],suggestedAction:clearSuggestions?null:(result.suggested_action&&allowedRequestKinds.includes(result.suggested_action.kind)?result.suggested_action:null),speechTurnId:result.speech_turn_id,
     planIsDraft:result.plan_is_draft,missingTopics:result.missing_topics,mapGuidance:result.map_guidance,
     plan:result.plan,evidenceStatus:result.evidence_status,omittedClaims:result.omitted_claims,
     relatedTopics:result.related_topics||[],supportContact:result.support_contact||null,
     actionOptions:(result.action_options||[]).filter(a=>allowedRequestKinds.includes(a.kind)),taskProgress:result.task_progress,
     agentProgress:result.agent_progress}]);
   if(result.autonomous_action?.executed)void refreshRequests();
   setIsThinking(false);setVoiceMessage('');
   if(voiceTurn&&result.speech_turn_id===voiceTurn){
     try{
       const chunks=result.speech_plan?.protocol===2&&result.speech_plan.turn_id===voiceTurn
         ?result.speech_plan.chunks:[];
       if(!chunks.length){
         // An incomplete approved prefix is displayed, never read aloud.
         if(currentTurn.current===voiceTurn)currentTurn.current=null;
         if(activeRef.current)setVoiceStatus('ready');
         return;
       }
       const current=()=>version.current===turnVersion&&languageRevision.current===localeRevision&&!voiceSignal?.aborted&&currentTurn.current===voiceTurn;
       // Speech delivery is owned by a dedicated lifecycle hook: synthesis is
       // never an ACK, evidence is revalidated before playback, and /played is
       // sent only after the browser actually finishes each chunk.
       await playSpeechPlan({
         chunks,signal:voiceSignal,isCurrent:current,language,endedAt,
         setStatus:status=>{if(activeRef.current)setVoiceStatus(status);},
         observe:observeVoice,
       });
       if(current()){
         setVoiceMessage('');currentTurn.current=null;
         if(activeRef.current)setVoiceStatus('ready');}
     }catch(err){
       if(version.current===turnVersion){stopPlayback();setVoiceMessage('');if(currentTurn.current===voiceTurn)currentTurn.current=null;
         if(activeRef.current)setVoiceStatus('ready');
         if(err instanceof api.ApiError&&err.status===409&&err.message.startsWith('Approved hotel evidence')){
           // Source withdrawal after /ask invalidates the displayed attribution
           // too; never leave a withdrawn hotel policy visible as "verified".
           setMessages(items=>items.map(item=>item.speechTurnId===voiceTurn
             ?{...item,text:t(language,'missingEvidence'),answerTitle:undefined,citations:[],suggestedAction:undefined,mapGuidance:undefined,plan:undefined,planIsDraft:false,evidenceStatus:'REVOKED_SOURCE',omittedClaims:0,actionOptions:[],taskProgress:[]}:item));
         }
         if(!(err instanceof DOMException&&err.name==='AbortError'))showToast(err instanceof api.ApiError&&[401,403,409].includes(err.status)?t(language,'speechExpired'): t(language,'playbackError'));}
     }
   }
  }catch(err){if(version.current!==turnVersion||languageRevision.current!==localeRevision)return;
    if(err instanceof api.ApiError&&[401,403].includes(err.status)){invalidate();setSessionReady(false);setErrorText(t(language,'sessionExpired'));}
    else if(!navigator.onLine)setErrorText(t(language,'networkLost'));
    if(!(err instanceof DOMException&&err.name==='AbortError')){
      setMessages(items=>[...items,{id:uuid(),sender:'assistant',time:clock(language),text:err instanceof api.ApiError&&[401,403].includes(err.status)?t(language,'sessionExpired'):err instanceof api.RequestTimeoutError?t(language,'timeout'):!navigator.onLine?t(language,'networkLost'):t(language,'askError')}]);
    }
 }finally{lifecycleClosed=true;void lifecyclePoll?.catch(()=>{});
   if(version.current===turnVersion){setIsThinking(false);clearController(requestController);
    if(!playback.current){currentTurn.current=null;if(activeRef.current)setVoiceStatus('ready');}}}
 };
 useEffect(()=>{
   const onSos=()=>{if(sessionReady&&!isThinking)void handleSendMessage('SOS',undefined,undefined,undefined,undefined,'sos_button');};
   window.addEventListener('concierge:sos',onSos);
   return()=>window.removeEventListener('concierge:sos',onSos);
 },[sessionReady,isThinking]);
 // The microphone and AudioWorklet remain live for the full Voice Mode.
 // Every accepted utterance uses a new backend turn; no speech is replayed after cancellation.
 const interruptVoice=()=>{
   voiceEpoch.current++;
   voiceTransport.current?.abort();voiceTransport.current=null;
   playbackTransport.current?.abort();playbackTransport.current=null;
   invalidateRequest();
   if(currentTurn.current)void api.cancelVoiceTurn(currentTurn.current).catch(()=>{});
   currentTurn.current=null;stopPlayback();setIsThinking(false);setVoiceMessage('');
 };
 // The allocation queue prevents out-of-order responses from /turn/start
 // superseding a more recent utterance, including the HTTP fallback's new ID.
 const queueVoiceTurn=async(epoch:number,signal:AbortSignal):Promise<string|null>=>{
   const allocation=turnStartQueue.current.catch(()=>{}).then(async()=>{
     if(epoch!==voiceEpoch.current||!activeRef.current||signal.aborted)return null;
     const {turn_id}=await api.startVoiceTurn();
     if(epoch!==voiceEpoch.current||!activeRef.current||signal.aborted){
       await api.cancelVoiceTurn(turn_id).catch(()=>{});return null;
     }
     return turn_id;
   });
   turnStartQueue.current=allocation.then(()=>{},()=>{});
   return allocation;
 };
 const handleVoiceSearch=async()=>{
   if(activeRef.current){activeRef.current=false;voiceEngine.current?.stop();voiceEngine.current=null;
     await pipecatAgent.current?.stop().catch(()=>{});pipecatAgent.current=null;
     disableVoice();interruptVoice();return;}
   if(!sessionReady||!serverConfig?.voice_available||!serverConfig.tts_languages.includes(langRef.current)){showToast(t(langRef.current,'voiceUnavailable'));return;}
   if(serverConfig.voice_transport==='pipecat'&&serverConfig.voice_agent_available){
     const language=langRef.current;
     const agent=new VoiceAgent({
       onState:state=>{if(pipecatAgent.current===agent&&activeRef.current)setVoiceEngineState(state);},
       onTranscript:text=>{
         if(pipecatAgent.current!==agent||!activeRef.current)return;
         previousRef.current=text;setLastQuery(text);
         setMessages(items=>[...items,{id:uuid(),sender:'user',time:clock(language),text}]);
       },
       onProgress:event=>{
         if(pipecatAgent.current!==agent||!activeRef.current)return;
         setIsThinking(event!=='response.approved');
         setVoiceMessage(event==='response.approved'?t(language,'voicePlaying'):t(language,'consulting'));
       },
       onAnswer:result=>{
         if(pipecatAgent.current!==agent||!activeRef.current)return;
         const clearSuggestions=result.clear_suggestions===true;
         setMessages(items=>[...(clearSuggestions?items.map(item=>({...item,suggestedAction:undefined})):items),{
           id:uuid(),sender:'assistant',time:clock(language),text:result.answer,
           answerTitle:result.answer_title,citations:result.citations||[],
           suggestedAction:clearSuggestions?null:(result.suggested_action&&allowedRequestKinds.includes(result.suggested_action.kind)?result.suggested_action:null),
           speechTurnId:result.speech_turn_id,planIsDraft:result.plan_is_draft,
           missingTopics:result.missing_topics,mapGuidance:result.map_guidance,plan:result.plan,
           evidenceStatus:result.evidence_status,omittedClaims:result.omitted_claims,
           relatedTopics:result.related_topics||[],supportContact:result.support_contact||null,
           actionOptions:(result.action_options||[]).filter(a=>allowedRequestKinds.includes(a.kind)),
           taskProgress:result.task_progress,agentProgress:result.agent_progress,
         }]);
         if(result.autonomous_action?.executed)void refreshRequests();
         setIsThinking(false);
       },
       onError:error=>{if(pipecatAgent.current===agent){showToast(error.message);setVoiceStatus('error');}},
     });
     pipecatAgent.current=agent;activeRef.current=true;enableVoice();
     try{await agent.start(language);}catch{
       if(pipecatAgent.current===agent){pipecatAgent.current=null;activeRef.current=false;disableVoice();showToast(t(language,'voiceError'));}
     }
     return;
   }
   // Live PCM is a functional transport change, not a UI redesign. Each
   // utterance receives one allocated backend turn before streaming starts.
   type LiveJob={epoch:number;abort:AbortController;turn?:string;
     transport?:api.PCMTransport;frames:Uint8Array[];bytes:number;
     open:Promise<{turn:string;transport:api.PCMTransport}>};
   let liveJob:LiveJob|null=null;
   let provisionalJob:AbortController|null=null;
   // Reversible recovery is shared by a short acoustic preview and an STT-
   // classified false interruption. Revalidate the original approved speech.
   // A later candidate, session change, or turn change invalidates the proof.
   const recoverPausedPlayback=(guard:()=>boolean,language:LanguageCode,signal?:AbortSignal)=>{
     const saved=pausedForSpeech.current, oldTurn=currentTurn.current;
     if(!saved||!oldTurn||!guard())return;
     pauseRecovery.current?.abort();
     const abort=new AbortController(),revision=++bargeProbeRevision.current;
     pauseRecovery.current=abort;
     setVoiceRecovering();
     if(signal?.aborted){abort.abort();return;}
     signal?.addEventListener('abort',()=>abort.abort(),{once:true});
     const recoveryDelay=Math.min(2000,Math.max(100,serverConfig?.voice_policy?.false_interruption_recovery_ms??500));
     void new Promise<void>((resolve,reject)=>{const id=setTimeout(resolve,recoveryDelay);abort.signal.addEventListener('abort',()=>{clearTimeout(id);reject(new DOMException('Cancelled','AbortError'));},{once:true});}).then(()=>api.checkSpeechCurrent(oldTurn,abort.signal)).then(async()=>{
       if(abort.signal.aborted||revision!==bargeProbeRevision.current||!guard()||
          playback.current!==saved||pausedForSpeech.current!==saved||currentTurn.current!==oldTurn)return;
       await saved.play();
       if(abort.signal.aborted||revision!==bargeProbeRevision.current||!guard()||
          playback.current!==saved||currentTurn.current!==oldTurn){saved.pause();return;}
       pausedForSpeech.current=null;
       setVoiceStatus('playing');
     }).catch(error=>{
       if(abort.signal.aborted||revision!==bargeProbeRevision.current||!guard()||playback.current!==saved)return;
       // A failed proof must also invalidate the old playback loop. Stopping
       // only the current audio element would otherwise allow the loop to
       // synthesize/play its next sentence despite a revoked or failed proof.
       interruptVoice();setVoiceStatus('ready');
       if(error instanceof api.ApiError&&error.status===409&&error.message.startsWith('Approved hotel evidence')){
         setMessages(items=>items.map(item=>item.speechTurnId===oldTurn
           ?{...item,text:t(language,'missingEvidence'),answerTitle:undefined,citations:[],suggestedAction:undefined,mapGuidance:undefined,plan:undefined,planIsDraft:false,evidenceStatus:'REVOKED_SOURCE',omittedClaims:0,actionOptions:[],taskProgress:[]}:item));
       }
     }).finally(()=>{if(pauseRecovery.current===abort)pauseRecovery.current=null;});
   };
   const engine=new ContinuousVoice({
     onState:state=>{if(voiceEngine.current===engine&&activeRef.current)setVoiceEngineState(state);},
     onBargeInCandidate:elapsedMs=>{
       if(voiceEngine.current!==engine||!activeRef.current||!playback.current||pausedForSpeech.current)return;
       pausedForSpeech.current=playback.current;
       bargePauseStarted.current=performance.now();
       playback.current.pause();
       setVoiceInterrupted();
       observeVoice('client.barge_pause',elapsedMs,langRef.current);
     },
     onBargeInRejected:()=>{
       if(voiceEngine.current!==engine||!activeRef.current||!pausedForSpeech.current)return;
       if(bargePauseStarted.current!==null)
         observeVoice('client.barge_false_pause',performance.now()-bargePauseStarted.current,langRef.current);
       bargePauseStarted.current=null;
       recoverPausedPlayback(()=>voiceEngine.current===engine&&activeRef.current,langRef.current);
     },
     onSpeechStart:()=>{if(voiceEngine.current!==engine||!activeRef.current)return;
       lastActivity.current=Date.now();
       if(playback.current){
         // The 160 ms preview may have paused this very same audio. Do NOT
         // cancel the approved turn at 480 ms; wait for final STT classification.
         pauseRecovery.current?.abort();pauseRecovery.current=null;bargeProbeRevision.current++;
         if(!pausedForSpeech.current)pausedForSpeech.current=playback.current;
         playback.current.pause();
         bargePauseStarted.current=null;
         // Abort only a previous *probe*, NOT the controller that currently
         // authorizes this playing answer/TTS. Cancelling the latter would
         // destroy the exact audio we need to recover after a false alarm.
         if(provisionalJob){
           provisionalJob.abort();
           if(voiceTransport.current===provisionalJob)voiceTransport.current=null;
           provisionalJob=null;
         }
         return;
       }
       // While RAG runs there is no approved audio to recover: cancel the old
       // work and allocate the next turn (only for the supported language).
       interruptVoice();
         if(serverConfig.incremental_voice_available &&
             langRef.current===serverConfig.incremental_voice_language){
           const epoch=voiceEpoch.current, abort=new AbortController();
           const job={} as LiveJob;
           job.epoch=epoch;job.abort=abort;job.frames=[];job.bytes=0;
           voiceTransport.current=abort;
           job.open=(async()=>{
             const turn=await queueVoiceTurn(epoch,abort.signal);
             if(!turn||abort.signal.aborted||epoch!==voiceEpoch.current)
               throw new DOMException('Cancelled','AbortError');
             job.turn=turn;currentTurn.current=turn;
             const transport=api.openPCMStream(langRef.current,turn,abort.signal,(text,revision,stableText,segmentComplete)=>{
              if(liveJob===job&&voiceEngine.current===engine&&
                 epoch===voiceEpoch.current&&!abort.signal.aborted)
                engine.noteProvisionalTranscript(text,revision,stableText,segmentComplete);
            },serverConfig?.voice_policy);
             job.transport=transport;
             for(const frame of job.frames)transport.push(frame);
             job.frames=[];job.bytes=0;
             return {turn,transport};
           })();
           void job.open.catch(()=>{}); // cancelled short/noise turns must not leak rejections
           liveJob=job;
         }
     },
     onPCMStart:()=>!!liveJob && !liveJob.abort.signal.aborted,
     onPCMFrame:frame=>{
       const job=liveJob;
       if(!job||job.abort.signal.aborted)return;
       try{
         if(job.transport)job.transport.push(frame);
         else {
           job.bytes+=frame.byteLength;
           if(job.bytes>1_000_000)throw new Error('PCM queue limit exceeded');
           job.frames.push(frame);
         }
       }catch{
         job.abort.abort();
       }
     },
     onPCMEnd:accepted=>{
       if(accepted)return; // Only onUtterance can commit the final transcript.
       const job=liveJob;liveJob=null;
       if(job){job.abort.abort();if(job.turn)void api.cancelVoiceTurn(job.turn).catch(()=>{});}
     },
     onActivity:()=>{lastActivity.current=Date.now();},
     onVolume:level=>{if(voiceEngine.current===engine&&activeRef.current)setVoiceVolume(level);},
     onUtteranceTooShort:()=>{if(voiceEngine.current===engine&&activeRef.current)showToast(t(langRef.current,'voiceRepeat'));},
     isPlaybackActive:()=>!!playback.current,
     onError:error=>{if(voiceEngine.current!==engine)return;
       invalidate();showToast(error.message);},
     onUtterance:blob=>{
       if(voiceEngine.current!==engine||!activeRef.current)return;
       let epoch=voiceEpoch.current;
       const endedAt=performance.now();
       const provisional=pausedForSpeech.current!==null;
       const selectedLive=liveJob;liveJob=null;
       // A provisional probe must not abort the currently playing, approved
       // answer. Keep its own controller and invalidate only an older probe.
       if(provisional)provisionalJob?.abort();
       const requestAbort=selectedLive?.abort||new AbortController();
       if(provisional)provisionalJob=requestAbort;
       voiceTransport.current=requestAbort;
       const language=langRef.current;
       const isCurrent=()=>epoch===voiceEpoch.current&&activeRef.current&&voiceEngine.current===engine&&!requestAbort.signal.aborted;
       const resumeIfFalseInterruption=()=>{
         if(isCurrent())recoverPausedPlayback(isCurrent,language,requestAbort.signal);
       };
       void (async()=>{
         let turn:string|null=null;
         try{
           if(!isCurrent()||!sessionReady)return;
           if(provisional){
             // Turnless STT is an existing authenticated endpoint. It only
             // classifies a prospective interruption, so the previous approved
             // turn and its audio remain recoverable until there is real speech.
             const sttStarted=performance.now();
             let provisionalResult:api.TranscriptResult;
             try{provisionalResult=await api.transcribe(blob,language,undefined,requestAbort.signal);}
             catch(error){
               if(!isCurrent())return;
               if(error instanceof api.ApiError&&error.status===422){resumeIfFalseInterruption();return;}
               // Unknown STT failure is not a trustworthy interruption decision.
               // Resume existing approved speech; report the issue without
               // invoking RAG on an unverified transcript.
               resumeIfFalseInterruption();showToast(t(langRef.current,'voiceError'));return;
             }
             if(!isCurrent())return;
             const provisionalText=provisionalResult.text;
             const provisionalLanguage=(provisionalResult.suggest_language_switch&&provisionalResult.detected_language)||language;
             observeVoice('client.stt',performance.now()-sttStarted,provisionalLanguage);
             if(provisionalResult.suggest_language_switch)showToast(t(langRef.current,'detectedVoiceLanguage'));
             if(provisionalResult.reject_reason){resumeIfFalseInterruption();showToast(t(langRef.current,'voiceRepeat'));return;}
             if(!provisionalText.trim()||api.isPlaybackBackchannel(provisionalText,provisionalLanguage)){
               resumeIfFalseInterruption();return;
             }
             // This is a real interruption. The previous answer and any
             // one-chunk-ahead synthesis are now invalidated atomically at UI
             // level, before starting a fresh backend turn.
             if(voiceTransport.current===requestAbort)voiceTransport.current=null;
             interruptVoice();
             voiceTransport.current=requestAbort;
             epoch=voiceEpoch.current;
             if(!isCurrent())return;
             if(provisionalText.length>500){showToast(t(language,'voiceTooLong'));return;}
             turn=await queueVoiceTurn(epoch,requestAbort.signal);
             if(!turn||!isCurrent())return;
             currentTurn.current=turn;
             playbackTransport.current=requestAbort;
             await handleSendMessage(provisionalText,turn,requestAbort.signal,provisionalLanguage,endedAt);
             return;
           }
           let transcriptResult:api.TranscriptResult;
           const sttStarted=performance.now();
           if(selectedLive){
             try{
               const opened=await selectedLive.open;
               turn=opened.turn;
               if(!isCurrent())return;
               transcriptResult=await opened.transport.end();
             }catch(error){
               if(!isCurrent())return;
               if(!(error instanceof api.VoiceTransportError&&error.safeFallback))throw error;
               // A rejected PCM hello may have claimed the ID. Never replay to it.
               if(selectedLive.turn)await api.cancelVoiceTurn(selectedLive.turn).catch(()=>{});
               if(!isCurrent())return;
               turn=await queueVoiceTurn(epoch,requestAbort.signal);
               if(!turn||!isCurrent())return;
               currentTurn.current=turn;
               transcriptResult=await api.transcribeStream(blob,language,turn,requestAbort.signal,serverConfig.voice_policy?.max_windowed_audio_bytes??serverConfig.max_audio_bytes,serverConfig.max_audio_bytes);
             }
           }else{
             turn=await queueVoiceTurn(epoch,requestAbort.signal);
             if(!turn||!isCurrent())return;
             currentTurn.current=turn;
             try{transcriptResult=await api.transcribeStream(blob,language,turn,requestAbort.signal,serverConfig.voice_policy?.max_windowed_audio_bytes??serverConfig.max_audio_bytes,serverConfig.max_audio_bytes);}
             catch(error){
               if(!isCurrent())return;
               if(!(error instanceof api.VoiceTransportError && error.safeFallback))throw error;
               await api.cancelVoiceTurn(turn).catch(()=>{});
               if(!isCurrent())return;
               turn=await queueVoiceTurn(epoch,requestAbort.signal);
               if(!turn||!isCurrent())return;
               currentTurn.current=turn;
               transcriptResult=await api.transcribe(blob,language,turn,requestAbort.signal);
             }
           }
           if(!isCurrent())return;
           const transcript=transcriptResult.text;
           const responseLanguage=(transcriptResult.suggest_language_switch&&transcriptResult.detected_language)||language;
           observeVoice('client.stt',performance.now()-sttStarted,responseLanguage);
           if(transcriptResult.suggest_language_switch)showToast(t(langRef.current,'detectedVoiceLanguage'));
           if(transcriptResult.reject_reason){await api.cancelVoiceTurn(turn).catch(()=>{});if(isCurrent()){currentTurn.current=null;setVoiceStatus('ready');showToast(t(language,'voiceRepeat'));}return;}
           if(transcript.trim().length<2){await api.cancelVoiceTurn(turn).catch(()=>{});
             if(isCurrent()){currentTurn.current=null;setVoiceStatus('ready');}return;}
           if(transcript.length>500){await api.cancelVoiceTurn(turn).catch(()=>{});
             if(isCurrent()){currentTurn.current=null;setVoiceStatus('ready');showToast(t(language,'voiceTooLong'));}return;}
           playbackTransport.current=requestAbort;
           await handleSendMessage(transcript,turn,requestAbort.signal,responseLanguage,endedAt);
         }catch(error){
           if(isCurrent() && !(error instanceof DOMException&&error.name==='AbortError')){
             showToast(t(language,'voiceError'));setVoiceStatus('ready');}
           if(turn)void api.cancelVoiceTurn(turn).catch(()=>{});
           if(currentTurn.current===turn)currentTurn.current=null;
         }finally{
           if(voiceTransport.current===requestAbort)voiceTransport.current=null;
           if(provisionalJob===requestAbort)provisionalJob=null;
           if(playbackTransport.current===requestAbort)playbackTransport.current=null;
         }
       })();
     },
   });
   voiceEngine.current=engine;activeRef.current=true;enableVoice();
   lastActivity.current=Date.now();
   try{const policy=serverConfig.voice_policy;
   if(!policy)throw new Error('Voice policy unavailable');
   const voiceLanguage=langRef.current;
   await engine.start(serverConfig.max_audio_seconds,Math.min(serverConfig.max_audio_bytes,policy.max_windowed_audio_bytes??serverConfig.max_audio_bytes),{
     ...policy,continuation_cues:policy.continuation_cues[voiceLanguage]||[],
     clause_delimiters:policy.clause_delimiters[voiceLanguage]||[],sentence_endings:policy.sentence_endings[voiceLanguage]||[]});
   if(voiceEngine.current===engine&&activeRef.current&&!greetedRef.current){
     greetedRef.current=true;
     const greetingEpoch=voiceEpoch.current;
     const greetingAbort=new AbortController();
     let greetingTurn:string|null=null;
     playbackTransport.current=greetingAbort;
     try{
       const greeting=await api.voiceGreeting(langRef.current,greetingAbort.signal);
       greetingTurn=greeting.turn_id;
       const chunks=greeting.speech_plan?.protocol===2&&greeting.speech_plan.turn_id===greeting.turn_id
         ?greeting.speech_plan.chunks:[];
       const beforePlayback=()=>greetingEpoch===voiceEpoch.current&&activeRef.current&&
         voiceEngine.current===engine&&!greetingAbort.signal.aborted&&currentTurn.current===null;
       if(!beforePlayback()||!chunks.length){await api.cancelVoiceTurn(greeting.turn_id).catch(()=>{});return;}
       currentTurn.current=greeting.turn_id;
       const current=()=>greetingEpoch===voiceEpoch.current&&activeRef.current&&
         voiceEngine.current===engine&&!greetingAbort.signal.aborted&&currentTurn.current===greeting.turn_id;
       setMessages(items=>[...items,{id:uuid(),sender:'assistant',time:clock(langRef.current),text:greeting.text}]);
       await playSpeechPlan({chunks,signal:greetingAbort.signal,isCurrent:current,language:langRef.current,
         setStatus:status=>{if(activeRef.current)setVoiceStatus(status);},observe:observeVoice});
       if(current()){setVoiceMessage('');currentTurn.current=null;if(activeRef.current)setVoiceStatus('ready');}
     }catch{
       // A greeting is an enhancement only; microphone capture stays live.
       if(greetingTurn&&greetingAbort.signal.aborted)void api.cancelVoiceTurn(greetingTurn).catch(()=>{});
     }finally{
       if(playbackTransport.current===greetingAbort)playbackTransport.current=null;
       if(greetingTurn&&currentTurn.current===greetingTurn){currentTurn.current=null;if(activeRef.current)setVoiceStatus('ready');}
     }
   }
   }
   catch(error){if(voiceEngine.current===engine){
     activeRef.current=false;engine.stop();voiceEngine.current=null;disableVoice();invalidate();
     showToast(error instanceof Error?error.message:t(langRef.current,'voiceUnavailable'));
   }}
 };
 // A shared public kiosk must not expose an abandoned guest's conversation.
 useEffect(()=>{
   if(!sessionReady)return;
   const idleSeconds=Math.max(30,serverConfig?.session_policy?.idle_timeout_seconds??120);
   const warningSeconds=Math.min(idleSeconds-1,Math.max(5,serverConfig?.session_policy?.warning_seconds??20));
   const idleMs=idleSeconds*1000,warningAt=idleMs-warningSeconds*1000;
   let warned=false;
   const mark=()=>{lastActivity.current=Date.now();warned=false;};
   for(const name of ['pointerdown','keydown','touchstart'] as const)window.addEventListener(name,mark,{passive:true});
   const timer=setInterval(()=>{
     const elapsed=Date.now()-lastActivity.current;
     if(!warned&&elapsed>=warningAt&&elapsed<idleMs){warned=true;const seconds=Math.max(1,Math.ceil((idleMs-elapsed)/1000));showToast(t(langRef.current,'sessionWarning').replace('{seconds}',String(seconds)));}
     if(elapsed>=idleMs){lastActivity.current=Date.now();warned=false;invalidate();void api.endSession().finally(()=>void connect());}
   },1000);
   return()=>{clearInterval(timer);for(const name of ['pointerdown','keydown','touchstart'] as const)window.removeEventListener(name,mark);};
 },[sessionReady,serverConfig?.session_policy?.idle_timeout_seconds,serverConfig?.session_policy?.warning_seconds,invalidate,connect]);
 const buildDetails=()=>{if(!requestType)throw new Error(t(langRef.current,'prepareError'));if(suggestedDetails?.kind===requestType&&suggestedDetails.details===note){
   const original=note.trim();if(original.length<8||original.length>500)throw new Error(t(langRef.current,'detailRange'));return original;
  }const fields=requestFields(requestType);const type=requestKindLabel(requestType);const text=[type,
  fields.has('quantity')?`${t(langRef.current,'quantity')}: ${quantity}`:'',fields.has('room_number')&&roomNumber.trim()?`${t(langRef.current,'room')}: ${roomNumber.trim()}`:'',fields.has('preferred_time')&&requestTime?`${t(langRef.current,'preferredTime')}: ${requestTime}`:'',
  fields.has('party_size')&&partySize?`${t(langRef.current,'partySize')}: ${partySize}`:'',note.trim()].filter(Boolean).join('\n');
  if(text.length<8||text.length>500)throw new Error(t(langRef.current,'detailRange'));return text;};
 const handleCreateRequest=async()=>{if(!sessionReady||submitting||pendingProposal||!requestType||!uiContract?.capabilities.create_request)return;
  try{const details=buildDetails();setSubmitting(true);const epoch=sessionEpoch.current;
   const payload:api.ServicePayload={note:note.trim()};const fields=requestFields(requestType);
   if(fields.has('room_number')&&roomNumber.trim())payload.room_number=roomNumber.trim();
   if(fields.has('quantity'))payload.quantity=quantity;
   if(fields.has('preferred_time')&&requestTime)payload.preferred_time=requestTime;
   if(fields.has('party_size')&&partySize)payload.party_size=Number(partySize);
   if(serverConfig?.data_consent_required&&!dataConsent){showToast(({vi:'Vui lòng đồng ý chính sách dữ liệu.',en:'Please agree to the data notice.',zh:'请同意数据说明。',ko:'데이터 안내에 동의해 주세요.'} as Record<LanguageCode,string>)[selectedLanguage]);return;}
   const proposal=await api.prepare(requestType,selectedLanguage,details,draftNonce.current||(draftNonce.current=uuid()),payload,dataConsent);
   if(epoch!==sessionEpoch.current)return;setPendingProposal(proposal);setPriceAcknowledged(false);showToast(t(selectedLanguage,'reviewRequest'));
  }catch(err){expireIfAuth(err);showToast(!navigator.onLine?t(langRef.current,'networkLost'):err instanceof api.RequestTimeoutError?t(langRef.current,'requestPending'):err instanceof api.ApiError&&[401,403].includes(err.status)?t(langRef.current,'sessionExpired'):t(langRef.current,'prepareError'));}finally{setSubmitting(false);}};
 const handleConfirmRequest=async(confirmedVerification?:api.GuestVerificationInput)=>{if(!pendingProposal||submitting)return;if(pendingProposal.price_disclosure_required&&!priceAcknowledged){showToast(t(langRef.current,'priceDisclosureAcknowledgement'));return;}if(confirmedVerification===undefined&&pendingProposal.staff_verification_required&&roomNumber.trim()){setVerificationModalOpen(true);return;}setSubmitting(true);
  try{const epoch=sessionEpoch.current;let verification:api.GuestVerificationInput|undefined=confirmedVerification;
   const result=await api.confirm(pendingProposal.proposal_id,verification,priceAcknowledged);
   if(epoch!==sessionEpoch.current)return;setPendingProposal(null);setPriceAcknowledged(false);setSuggestedDetails(null);draftNonce.current=null;setNote('');setRoomNumber('');setQuantity(1);setRequestTime('');setPartySize('');setSuggestedDetails(null);setIsEditModalOpen(false);
   await refreshRequests();setDataConsent(false);
   if(result.confirmation_code&&result.status_url){setLatestStatus({code:result.confirmation_code,url:result.status_url,qr:result.status_url.replace('/status/','/api/status/')+'/qr.svg'});}
   showToast(`${t(langRef.current,'bookingDisclaimer')} ${result.confirmation_code||''}`);
  }catch(err){if(!expireIfAuth(err))void refreshRequests();showToast(err instanceof api.ApiError&&[401,403].includes(err.status)?t(langRef.current,'sessionExpired'):t(langRef.current,'confirmError'));}
  finally{setSubmitting(false);}};
 const handleCancelProposal=async()=>{if(!pendingProposal)return;setSubmitting(true);
  try{const epoch=sessionEpoch.current;await api.cancelProposal(pendingProposal.proposal_id);if(epoch!==sessionEpoch.current)return;setPendingProposal(null);setPriceAcknowledged(false);setSuggestedDetails(null);draftNonce.current=null;}
  catch(err){expireIfAuth(err);showToast(t(langRef.current,'cancelError'));}finally{setSubmitting(false);}};
 const handleResetForm=()=>{if(pendingProposal){void handleCancelProposal();return;}
  setNote('');setRoomNumber('');setQuantity(1);setRequestTime('');setPartySize('');setSuggestedDetails(null);draftNonce.current=null;};
 const checkRequest=async(id:string)=>{
  if(expandedRequestRef.current===id){expandedRequestRef.current=null;setExpandedRequest(null);setRequestProgress(null);return;}
  const epoch=sessionEpoch.current;expandedRequestRef.current=id;setExpandedRequest(id);
  setRequestProgress(null);setRequestProgressError('');
  try{const progress=await api.requestProgress(id);
   if(epoch!==sessionEpoch.current||expandedRequestRef.current!==id)return;
   setRequestProgress(progress);
  }catch(err){if(epoch===sessionEpoch.current&&expandedRequestRef.current===id)
    setRequestProgressError(t(langRef.current,'progressError'));}
 };
 const draftChanged=()=>{if(pendingProposal){showToast(t(selectedLanguage,'cancel'));return false;}draftNonce.current=null;setSuggestedDetails(null);return true;};
  // The request-time and request-party fields are owned by EditRequestModal.
  // No mock ticket, prepared booking, sample answer, or invented staff timeline is rendered.
  const activeFields=requestFields(requestType);
  const preview = pendingProposal?.details || (note.trim()&&requestType ? [requestKindLabel(requestType,selectedLanguage),
    activeFields.has('room_number')&&roomNumber.trim()?`${t(selectedLanguage,'room')}: ${roomNumber.trim()}`:'',
    activeFields.has('quantity')?`${t(selectedLanguage,'quantity')}: ${quantity}`:'',activeFields.has('preferred_time')&&requestTime?`${t(selectedLanguage,'preferredTime')}: ${requestTime}`:'',
    activeFields.has('party_size')&&partySize?`${t(selectedLanguage,'partySize')}: ${partySize}`:'',note.trim()].filter(Boolean).join('\n'):null);
  const handleSaveEdit=(values:{kind:RequestKind;room:string;quantity:number;time:string;party:string;note:string;dataConsent:boolean})=>{
    if(changeTargetId){
      const target=changeTargetId;
      const payload:api.ServicePayload={};
      if(values.room.trim())payload.room_number=values.room.trim();
      if(values.quantity>0)payload.quantity=values.quantity;
      if(values.time.trim())payload.preferred_time=values.time.trim();
      if(values.party.trim())payload.party_size=Number(values.party);
      if(values.note.trim())payload.note=values.note.trim();
      setChangeBusy(true);
      void api.requestChange(target,'modify',uuid(),payload).then(async()=>{
        setChangeTargetId(null);setIsEditModalOpen(false);showToast(t(langRef.current,'modifyQueued'));
        await refreshRequests();
      }).catch(err=>{if(!expireIfAuth(err))showToast(t(langRef.current,'changeError'));})
        .finally(()=>setChangeBusy(false));
      return;
    }
    if(!draftChanged())return;
    setRequestType(values.kind);setRoomNumber(values.room);setQuantity(values.quantity);setDataConsent(values.dataConsent);
    setRequestTime(values.time);setPartySize(values.party);setNote(values.note);
    setIsEditModalOpen(false);
  };
  const requestCancelSubmitted=async()=>{
    if(!requestProgress?.can_cancel||changeBusy)return;
    setChangeBusy(true);
    try{await api.requestChange(requestProgress.id,'cancel',uuid());showToast(t(langRef.current,'cancelQueued'));await refreshRequests();}
    catch(err){if(!expireIfAuth(err))showToast(t(langRef.current,'changeError'));}
    finally{setChangeBusy(false);}
  };
  const requestModifySubmitted=()=>{
    if(!requestProgress?.can_modify||changeBusy)return;
    const p=requestProgress.effective_payload||requestProgress.payload||{};
    setChangeTargetId(requestProgress.id);setRequestType(requestProgress.kind);
    setRoomNumber(p.room_number||'');setQuantity(p.quantity||1);setRequestTime(p.preferred_time||'');
    setPartySize(p.party_size?String(p.party_size):'');setNote(p.note||requestProgress.details||'');
    setIsEditModalOpen(true);
  };
  const submitFeedback=async(rating:number,note:string)=>{
    if(!requestProgress||changeBusy)return;
    setChangeBusy(true);
    try{await api.requestFeedback(requestProgress.id,rating,note);const progress=await api.requestProgress(requestProgress.id);setRequestProgress(progress);showToast(t(langRef.current,'feedbackSubmitted'));}
    catch(err){if(!expireIfAuth(err))setRequestProgressError(t(langRef.current,'progressError'));}
    finally{setChangeBusy(false);}
  };
  const changeLanguage=(language:LanguageCode)=>{
    if(language===langRef.current)return;
    userSelectedLanguage.current=true;languageRevision.current++;
    voiceEpoch.current++;activeRef.current=false;
    voiceTransport.current?.abort();voiceTransport.current=null;
    playbackTransport.current?.abort();playbackTransport.current=null;
    voiceEngine.current?.stop();voiceEngine.current=null;void pipecatAgent.current?.stop();pipecatAgent.current=null;disableVoice();
    invalidateRequest();
    const turn=currentTurn.current;currentTurn.current=null;if(turn)void api.cancelVoiceTurn(turn).catch(()=>{});
    stopPlayback();setIsThinking(false);setVoiceMessage('');
    langRef.current=language;setSelectedLanguage(language);
  };
  const voiceAvailable=Boolean(sessionReady&&serverConfig?.voice_available&&serverConfig.tts_languages.includes(selectedLanguage));
  return <div className="h-screen h-[100dvh] overflow-hidden flex flex-col selection:bg-[#8C6D3E] selection:text-white bg-[#F3EFE6]">
    {toastMessage&&<div role="status" className="fixed top-24 left-1/2 -translate-x-1/2 z-50 bg-[#142742] text-white px-5 py-2.5 rounded-lg shadow-xl text-xs border border-amber-500/40 max-w-[90vw]">{toastMessage}</div>}
    {latestStatus&&<div className="fixed top-36 left-1/2 -translate-x-1/2 z-40 bg-white border border-[#D8E6F5] rounded-xl shadow-xl p-3 text-xs flex gap-3 items-center max-w-[90vw]">
      <img src={latestStatus.qr} alt="QR status link" className="w-20 h-20 bg-white" onError={event=>{event.currentTarget.hidden=true;}} />
      <div><p className="font-semibold text-brand-navy">{latestStatus.code}</p><a className="underline text-[#73532C]" href={latestStatus.url} target="_blank" rel="noreferrer">{latestStatus.url}</a><button className="block mt-1 text-gray-500 underline" onClick={()=>setLatestStatus(null)}>×</button></div>
    </div>}
    <button type="button" disabled={!sessionReady||isThinking}
      aria-label={t(selectedLanguage,'sosEmergency')} title={t(selectedLanguage,'sosEmergency')}
      onClick={()=>void handleSendMessage('SOS',undefined,undefined,undefined,undefined,'sos_button')}
      className="fixed right-4 bottom-4 z-50 min-w-16 min-h-16 rounded-full bg-red-700 text-white font-black tracking-wide shadow-2xl border-4 border-white disabled:opacity-50">
      SOS
    </button>
    <Header currentLanguage={selectedLanguage} languages={uiContract?.languages||[]}
      propertyName={serverConfig?.property_name||'Concierge Kiosk'} now={now}
      onLanguageChange={changeLanguage}
      onEndSession={()=>void handleEndSession()}/>
    <main className="flex-1 w-full max-w-[1720px] mx-auto p-3 lg:p-4 grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-0 overflow-auto lg:overflow-hidden" data-purpose="concierge-dashboard-grid">
      <SidebarNav language={selectedLanguage} items={serviceItems} activeId={selectedServiceId} onSelectItem={id=>{
        const service=serviceItems.find(item=>item.id===id);if(service)handleSelectService(service);
      }}/>
      <ChatSection propertyName={serverConfig?.property_name||'Concierge Kiosk'} language={selectedLanguage}
        messages={messages} value={inputText} onChange={setInputText} onSendMessage={text=>void handleSendMessage(text)}
        isThinking={isThinking} ready={sessionReady} error={errorText} onRetry={()=>void connect()}
        onSuggested={applySuggested} kindLabel={kind=>requestKindLabel(kind,selectedLanguage)} onStartRequest={()=>{if(allowedRequestKinds.length)setIsEditModalOpen(true);}} canCreateRequest={Boolean(uiContract?.capabilities.create_request&&allowedRequestKinds.length>0)} onOpenEditModal={()=>setIsEditModalOpen(true)}
        onVoice={()=>void handleVoiceSearch()} voiceAvailable={voiceAvailable}
        mapPlaces={mapPlaces} startLocation={startLocation} onStartLocation={setStartLocation}
        requestPreview={preview} prepared={Boolean(pendingProposal)} pending={submitting}
        priceDisclosure={pendingProposal?.price_disclosure||''} priceDisclosureRequired={Boolean(pendingProposal?.price_disclosure_required)}
        priceAcknowledged={priceAcknowledged} onPriceAcknowledged={setPriceAcknowledged}
        outsideOperatingHours={Boolean(pendingProposal?.outside_operating_hours)} nextOpenAt={pendingProposal?.next_open_at??null}
        onPrepare={()=>void handleCreateRequest()} onConfirm={()=>void handleConfirmRequest()}
        onCancel={()=>void handleCancelProposal()}/>
      <aside className="lg:col-span-3 flex flex-col h-full min-h-0 overflow-y-auto custom-scrollbar space-y-2.5 pr-0.5" data-purpose="right-assistant-and-status">
        <VoiceAssistant language={selectedLanguage} enabled={voiceUI.enabled} available={voiceAvailable} state={voiceUI.status} message={voiceUI.message} volume={voiceVolume} onToggle={()=>void handleVoiceSearch()}/>
        <MyRequests language={selectedLanguage} tickets={requestsList.map(req=>({...req,title:req.kind?requestKindLabel(req.kind,selectedLanguage):req.title,statusText:statusLabel(selectedLanguage,req.status)}))} selectedId={expandedRequest} onSelectTicket={id=>void checkRequest(id)}/>
      </aside>
    </main>
    {requestType&&<EditRequestModal isOpen={isEditModalOpen} kind={requestType} requestTypes={(changeTargetId?uiContract?.request_types.filter(item=>item.kind===requestType):uiContract?.request_types.filter(item=>item.actions.includes('create_request')))||[]} room={roomNumber} quantity={quantity}
      time={requestTime} party={partySize} note={note} dataConsent={dataConsent} showConsent={!Boolean(changeTargetId)} language={selectedLanguage}
      onClose={()=>{setIsEditModalOpen(false);setChangeTargetId(null);}} onSave={handleSaveEdit}/>}
    <VerificationModal isOpen={verificationModalOpen} language={selectedLanguage} room={roomNumber}
      onClose={()=>setVerificationModalOpen(false)}
      onSubmit={(lastName,roomQrToken)=>{setVerificationModalOpen(false);void handleConfirmRequest({room_number:roomNumber.trim(),last_name:lastName,room_qr_token:roomQrToken});}}/>
    <TicketModal language={selectedLanguage} isOpen={Boolean(expandedRequest)} progress={requestProgress} error={requestProgressError}
      busy={changeBusy} onCancelRequest={()=>void requestCancelSubmitted()} onModifyRequest={requestModifySubmitted}
      onSubmitFeedback={(rating,note)=>void submitFeedback(rating,note)}
      onClose={()=>{expandedRequestRef.current=null;setExpandedRequest(null);setRequestProgress(null);}}/>
  </div>;
};
export default App;
