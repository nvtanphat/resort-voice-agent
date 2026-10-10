/** Same-origin guest transport. CSRF stays in memory; session identity is HttpOnly. */
import type {LanguageCode, RequestKind, RequestStatus, ServicePayload} from './generated/api-contracts';
export type {LanguageCode, RequestKind, RequestStatus, ServicePayload} from './generated/api-contracts';
export type TaskProgress = {id:string;kind:'knowledge'|'navigation'|'planning'|RequestKind;
  status:'available'|'unavailable'|'awaiting_guest_choice'};
export interface GuestRequestProgress {
  id:string;kind:RequestKind;language:LanguageCode;status:RequestStatus;
  status_history:Array<{status:RequestStatus;at:number|null}>;
  can_cancel:boolean;can_modify:boolean;staff_review_required:true;
  change_state?:string;change_updated_at?:number;details?:string;
  payload?:ServicePayload;effective_payload?:ServicePayload;
  feedback_requested?:boolean;feedback_submitted?:boolean;feedback?:{rating:number;note:string;created_at:number}|null;
}
export interface ServiceInfo {
  id: string;
  request_kind: RequestKind | null;
  title: Record<LanguageCode, string>;
  question: Record<LanguageCode, string>;
  icon_category: string;
  actions: string[];
}
export interface UiLanguage { code:LanguageCode; label:string; }
export interface UiRequestType {
  kind:RequestKind; labels:Record<LanguageCode,string>; icon_category:string;
  fields:Array<'room_number'|'quantity'|'preferred_time'|'party_size'|'note'>;
  actions:Array<'ask'|'create_request'>;
}
export interface UiContract {
  contract_version:number; languages:UiLanguage[]; request_types:UiRequestType[];
  capabilities:{service_menu:boolean;create_request:boolean;language_switch:boolean};
}
export interface Citation {
  citation_id: string; source_id: string; revision: string; chunk_id: string;
  title: string; heading: string; quote: string; claim: string;
  effective_from: string; effective_to: string | null;
  claim_spans?: Array<{text:string; quote_start:number; quote_end:number}>;
}
export interface SpeechPlan {
  turn_id:string; protocol:2; chunks:Array<{id:string;ordinal:number}>;
}
export interface VoiceGreeting {
  turn_id:string; text:string; speech_plan:SpeechPlan;
}
export interface ProactiveSuggestion {
  kind:string; entity_id:string; language:LanguageCode; text:string;
  capability:string; expires_at:number; write_authority:false;
}
export interface RelatedTopic { label:string; query:string; domain:string; language:string; }
export interface SupportContact {
  label:string; department:string; phones:string[]; extensions:string[]; email:string|null;
  verified_at:string|null; grounding:'manifest_pinned_directory';
}
export interface Answer {
  service_payload?: ServicePayload;
  answer: string; answer_title?: string | null; citations: Citation[]; speech_turn_id: string;
  speech_plan?: SpeechPlan;
  clear_suggestions?: boolean;
  suggested_action: {change?:RequestChangeTarget;kind: RequestKind; details: string; service?: string} | null;
  action_options?: Array<{kind:RequestKind}>;
  service_options?: ServiceOption[];
  needs_review?: boolean;
  tool_route?: string; retrieval_mode?: string;
  plan_is_draft?: boolean; plan_topics?: string[]; missing_topics?: string[];
  plan?: DraftPlan; evidence_status?: 'SUPPORTED'|'PARTIALLY_SUPPORTED'|'CONFLICTING'|'UNSUPPORTED'|'REVOKED_SOURCE';
  omitted_claims?: number; query_rewrite_applied?: boolean;
  related_topics?: RelatedTopic[]; support_contact?: SupportContact|null; recovery_mode?: 'self_service'|'staff_review'|'not_needed';
  map_guidance?: MapGuidance;
  task_progress?: TaskProgress[];
  agent_progress?: Array<{step:number;capability:string|null;status:string;requirement_id?:string|null}>;
  session_update?: {language?: LanguageCode} | null;
  emergency_ui?: {show_staff_location?:boolean;normal_request_disabled?:boolean};
  agent_trace?: {status:string;steps:Array<{tool:string;status:string}>;business_writes?:number;termination_reason?:string;plan_replans?:number};
  autonomous_action?: {executed:boolean;request_id:string;status:RequestStatus;authority_level:string;policy_reason:string;fulfillment_confirmed:false};
}
export interface DraftPlan {
  plan_id:string; status:'draft'; language:LanguageCode;
  days:Array<{day_index:number;activity_indices:number[];scheduled:false}>;
  activities:Array<{topic:string;description:string;day_index?:number|null;suggested_time:null|{start:string;end:string;label?:string};verified_availability:false;
    verification_status:'verified_information'; requires_booking_confirmation:true;
    source_references:Array<{source_id:string;revision:string;chunk_id:string;citation_id:string}>}>;
  constraints:Array<{kind:string;value:number;origin:'guest_request'}>;
  unverified_items:string[];requires_confirmation:true;booking_status:'not_booked';
}
export interface MapPlace { id:string; label:string; }
export interface MapPlaceList {
  status:'verified'|'unavailable'; default_origin_id?:string; places:MapPlace[];
}
export interface MapGuidance {
  status:'verified'|'unavailable'; revision?:string; source_id?:string; source_revision?:string;
  origin?:string; destination?:string; origin_id?:string; destination_id?:string; steps?:string[];
}
export interface RequestRow { id:string; kind:RequestKind; language:LanguageCode; status:RequestStatus; updated_at:number; guest_change_state?:string; }
/** A service offered when a turn was not understood; choosing it opens the request form for review. */
export interface ServiceOption { kind:RequestKind; service:string; label:string; details:string; payload?:ServicePayload; }

export interface KioskConfig {
  property_name:string; languages:LanguageCode[]; voice_available:boolean;
  voice_transport?:'legacy'|'pipecat'; voice_agent_available?:boolean;
  public_origin?:string;
  understanding_ready?:boolean;
  tts_languages:LanguageCode[];
  max_audio_bytes:number; max_audio_seconds:number;
  incremental_voice_available?:boolean; incremental_voice_language?:LanguageCode;
  api_version?:number; default_language?:LanguageCode; enabled_languages?:LanguageCode[];
  session_policy?:{idle_timeout_seconds:number;warning_seconds:number};
  voice_policy?:{protocol:number;sample_rate:number;max_frame_bytes:number;max_windowed_audio_bytes:number;queue_bytes:number;credit_bytes:number;
    vad_start_ms:number;vad_end_silence_ms:number;barge_preview_ms:number;barge_confirm_ms:number;
    false_interruption_recovery_ms:number;backpressure_timeout_ms:number;
    vad:{engine:'energy'|'silero_v5';noise_floor_initial:number;positive_threshold:number;negative_threshold:number;redemption_ms:number;pre_speech_pad_ms:number;min_speech_ms:number;playback_positive_threshold:number;continuation_extra_ms:number};
    continuation_cues:Record<LanguageCode,string[]>;clause_delimiters:Record<LanguageCode,string[]>;sentence_endings:Record<LanguageCode,string[]>};
  stt_modes?:Partial<Record<LanguageCode,string>>;
  data_consent_required?:boolean;
}
let csrfToken = '';
export const csrf = () => csrfToken;
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}
export class RequestTimeoutError extends Error { constructor(){super('Request timed out');this.name='RequestTimeoutError';} }
/** Deadline covers response headers. Do not automatically retry
 * mutations: a timeout can happen AFTER a ticket has committed. */
async function timedFetch(path:string,options:RequestInit,timeoutMs=30000):Promise<Response> {
  const controller=new AbortController();
  const original=options.signal;
  if(original?.aborted)throw new DOMException('Cancelled','AbortError');
  let rejectCancelled:(reason:Error)=>void=()=>{};
  const cancelled=new Promise<never>((_,reject)=>{rejectCancelled=reject;});
  const cancel=()=>{controller.abort(original?.reason);rejectCancelled(new DOMException('Cancelled','AbortError'));};
  original?.addEventListener('abort',cancel,{once:true});
  let timer:ReturnType<typeof setTimeout>|undefined;
  let timedOut=false;
  try {
    const deadline=new Promise<never>((_,reject)=>{
      timer=setTimeout(()=>{timedOut=true;controller.abort();reject(new RequestTimeoutError());},timeoutMs);
    });
    const response=await Promise.race([fetch(path,{...options,signal:controller.signal}),deadline,cancelled]);
    return response;
  } catch(error){
    if(timedOut)throw new RequestTimeoutError();
    throw error;
  } finally {if(timer)clearTimeout(timer);original?.removeEventListener('abort',cancel);}
}
async function request<T>(path:string, method='GET', body?:unknown, signal?:AbortSignal):Promise<T> {
  const headers:Record<string,string> = {'Accept':'application/json'};
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await timedFetch(path, {method, headers, credentials:'same-origin', cache:'no-store',
    body:body===undefined?undefined:JSON.stringify(body), signal});
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { const error = await response.json(); detail = typeof error.detail==='string'?error.detail:detail; } catch { /* status is authoritative */ }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
export async function startSession(signal?:AbortSignal):Promise<void> {
  const result = await request<{csrf_token:string}>('/api/session','POST',undefined,signal);
  if(signal?.aborted)throw new DOMException('Cancelled','AbortError');
  csrfToken=result.csrf_token;
}
export async function endSession():Promise<void> {
  try { if(csrfToken) await request('/api/session/end','POST'); }
  finally { csrfToken=''; }
}
export function voiceAgentConnection(language:LanguageCode):{wsUrl:string;token:string}{
  if(!csrfToken)throw new ApiError(401,'Voice session is not initialized');
  const url=new URL('/api/voice/agent',window.location.href);
  url.protocol=url.protocol==='https:'?'wss:':'ws:';
  url.searchParams.set('language',language);
  return {wsUrl:url.toString(),token:csrfToken};
}
/** Privacy-minimal, best-effort client latency. Never sends text, audio, citations,
 * session IDs or turn IDs. The authoritative business path does not await this. */
export function reportVoiceLatency(stage:'client.stt'|'client.ask'|'client.tts_first_audio'|
  'client.e2e_first_audio'|'client.playback_total'|'client.barge_pause'|
  'client.barge_false_pause', durationMs:number,
  language:LanguageCode, eventId:string):Promise<void> {
  if(!Number.isFinite(durationMs)||durationMs<0||durationMs>120000||!csrfToken)return Promise.resolve();
  return request<{accepted:boolean}>('/api/telemetry','POST',{
    event_id:eventId,stage,duration_ms:Math.round(durationMs*10)/10,language,
  }).then(()=>{});
}

export const config = () => request<KioskConfig>('/api/config');
export const services = () => request<{items:ServiceInfo[]}>('/api/services');
export const uiContract = () => request<UiContract>('/api/ui-contract');
export const mapPlaces = (language:LanguageCode) =>
  request<MapPlaceList>(`/api/map/places?language=${encodeURIComponent(language)}`);
export function ask(query:string,language:LanguageCode,previous_query:string,signal?:AbortSignal,turn?:string,startLocation?:string,turnNonce?:string,source:'dialogue'|'sos_button'='dialogue'):Promise<Answer> {
  const headers:Record<string,string> = {'Content-Type':'application/json','X-CSRF-Token':csrfToken};
  if(turn) headers['X-Voice-Turn-ID']=turn;
  return timedFetch('/api/ask',{method:'POST',credentials:'same-origin',cache:'no-store',headers,
    body:JSON.stringify({query,language,previous_query,source,start_location:startLocation||null,turn_nonce:turnNonce||null}),signal},70000).then(async response=>{
      if(!response.ok){let detail=`HTTP ${response.status}`;try{const data=await response.json();detail=data.detail||detail;}catch{}
        throw new ApiError(response.status,detail);}
      return response.json() as Promise<Answer>;
    });
}
export type RequestChangeTarget = {request_id:string;action:'cancel'|'modify'};
export const prepare = (kind:RequestKind,language:LanguageCode,details:string,nonce:string,payload?:ServicePayload,dataConsent=false,service?:string,change?:RequestChangeTarget) =>
  request<{proposal_id:string;kind:RequestKind;details:string;status:string;expires_at:number;requires_confirmation:boolean;staff_verification_required:boolean;service_code?:string;price_disclosure_required?:boolean;price_disclosure?:string;outside_operating_hours?:boolean;next_open_at?:number|null}>(
    '/api/requests/prepare','POST',{kind,language,details,nonce,payload:payload||null,data_consent:dataConsent,...(service?{service}:{}),...(change?{change}:{})});
export interface GuestVerificationInput { room_number:string; last_name?:string; room_qr_token?:string; }
export const confirm = (proposal_id:string, verification?:GuestVerificationInput, priceAcknowledged=false) =>
  request<{request_id:string;status:RequestStatus;message:string;change_state?:string;guest_verification_state:string;eta_minutes:number|null;external_dispatch_state:string;confirmation_code:string;status_url:string;status_token_expires_at:number}>('/api/requests/confirm','POST',{proposal_id,confirmed:true,price_acknowledged:priceAcknowledged,verification:verification||null});
export const cancelProposal = (proposal_id:string) => request('/api/requests/cancel','POST',{proposal_id});
export const myRequests = () => request<{items:RequestRow[]}>('/api/requests/mine?limit=30');
export const requestProgress = (id:string) => request<GuestRequestProgress>(`/api/requests/${encodeURIComponent(id)}/progress`);
export const proactiveSuggestions = (language:LanguageCode, consent=false) =>
  request<{suggestions:ProactiveSuggestion[];consent_required:boolean}>(`/api/proactive/suggestions?language=${encodeURIComponent(language)}&consent=${consent ? 'true' : 'false'}`);
export const requestFeedback = (id:string,rating:number,note='') =>
  request<{request_id:string;rating:number;note:string;created_at:number}>(
    `/api/requests/${encodeURIComponent(id)}/feedback`,'POST',{rating,note});
export const requestChange = (id:string, action:'cancel'|'modify', nonce:string, payload?:ServicePayload, note='') =>
  request<Awaited<ReturnType<typeof prepare>>>(`/api/requests/${encodeURIComponent(id)}/change`,'POST',{action,nonce,payload:payload||null,note});
export const startVoiceTurn = () => request<{turn_id:string}>('/api/audio/turn/start','POST');
export const voiceGreeting = (language:LanguageCode, signal?:AbortSignal) =>
  request<VoiceGreeting>(`/api/audio/greeting?language=${encodeURIComponent(language)}`,'POST',undefined,signal);
export interface TranscriptResult {
  text:string;
  detected_language:LanguageCode|null;
  language_probability:number|null;
  confidence:number|null;
  reject_reason:string|null;
  suggest_language_switch:boolean;
}
const transcriptResult=(text:string,detected:LanguageCode|null=null,probability:number|null=null,switchLanguage=false,confidence:number|null=null,rejectReason:string|null=null):TranscriptResult =>
  ({text,detected_language:detected,language_probability:probability,confidence,reject_reason:rejectReason,suggest_language_switch:switchLanguage});
export const cancelVoiceTurn = (id:string) => request(`/api/audio/turn/cancel?turn_id=${encodeURIComponent(id)}`,'POST');
/** A turnless HTTP transcript is permitted for provisional barge-in verification.
 * It NEVER invokes /ask or authorizes speech. A genuine request receives its
 * own new turn only after the transcript is classified. */
export async function transcribe(blob:Blob,language:LanguageCode,turnId?:string,signal?:AbortSignal):Promise<TranscriptResult> {
  const form = new FormData(); form.append('file',blob,blob.type.includes('wav')?'voice.wav':blob.type.includes('ogg')?'voice.ogg':blob.type.includes('mp4')?'voice.m4a':'voice.webm');
  const response=await timedFetch(`/api/audio/transcribe?language=${encodeURIComponent(language)}${turnId?`&turn_id=${encodeURIComponent(turnId)}`:''}`,
    {method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRF-Token':csrfToken},body:form,signal},45000);
  if(!response.ok){let detail=`HTTP ${response.status}`;try{detail=(await response.json()).detail||detail;}catch{}
    throw new ApiError(response.status,detail);}
  const result=await response.json() as {text:string;final:boolean;detected_language?:LanguageCode|null;language_probability?:number|null;confidence?:number|null;reject_reason?:string|null;suggest_language_switch?:boolean};
  return transcriptResult(result.text,result.detected_language??null,result.language_probability??null,result.suggest_language_switch===true,result.confidence??null,result.reject_reason??null);
}

/** Revalidate current hotel policy before playing prefetched audio. */
export const checkSpeechCurrent = (turnId:string,signal?:AbortSignal):Promise<{authorized:boolean}> =>
  request<{authorized:boolean}>(`/api/audio/turn/proof?turn_id=${encodeURIComponent(turnId)}`,'GET',undefined,signal);

/** speech transport. The server owns chunk text/language; the browser holds
 * only an opaque chunk capability returned by /api/ask. */
export const checkSpeechChunk = (chunkId:string,signal?:AbortSignal):Promise<{authorized:boolean;chunk_id:string}> =>
  request<{authorized:boolean;chunk_id:string}>('/api/audio/proof','POST',{chunk_id:chunkId},signal);

export const markSpeechChunkPlayed = (chunkId:string,signal?:AbortSignal):Promise<{acknowledged:boolean;chunk_id:string}> =>
  request<{acknowledged:boolean;chunk_id:string}>('/api/audio/played','POST',{chunk_id:chunkId},signal);

export const markSpeechChunkPlaybackFailed = (chunkId:string):Promise<{acknowledged:boolean;chunk_id:string}> =>
  request<{acknowledged:boolean;chunk_id:string}>('/api/audio/playback-failed','POST',{chunk_id:chunkId});

export async function speakChunk(chunkId:string,signal?:AbortSignal):Promise<Blob> {
  const response=await timedFetch('/api/audio/speak',
    {method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRF-Token':csrfToken,'Content-Type':'application/json'},
     body:JSON.stringify({chunk_id:chunkId}),signal},35000);
  if(!response.ok){let detail=`HTTP ${response.status}`;try{detail=(await response.json()).detail||detail;}catch{}
    throw new ApiError(response.status,detail);}
  return response.blob();
}

/** Diagnostic-only turn stage journal; never authoritative for transactions. */
export const turnEvents = (id:string,after=0,signal?:AbortSignal) => request<{turn_id:string;events:Array<{sequence:number;type:string;elapsed_ms:number}>;latest_sequence:number;terminal:boolean}>(`/api/turns/${encodeURIComponent(id)}/events?after=${after}`,'GET',undefined,signal);

export class VoiceTransportError extends Error { constructor(message:string,public readonly safeFallback:boolean){super(message);} }

/** Authenticated per-utterance WebSocket. Keeps HTTP STT as a safe transport fallback.
 * Each binary frame is bounded by the server's 128 KiB limit. */
export async function streamTranscribe(blob:Blob,language:LanguageCode,turnId:string,signal?:AbortSignal,maxWindowedAudioBytes=Number.MAX_SAFE_INTEGER,httpMaxAudioBytes=Number.MAX_SAFE_INTEGER):Promise<TranscriptResult> {
  if (blob.size>maxWindowedAudioBytes) throw new VoiceTransportError('Windowed voice size limit exceeded',blob.size<=httpMaxAudioBytes);
  if (!csrfToken) throw new ApiError(401,'Voice session is not initialized');
  if (signal?.aborted) throw new DOMException('Cancelled','AbortError');
  const url = new URL('/api/audio/stream',window.location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return new Promise<TranscriptResult>((resolve,reject) => {
    let socket:WebSocket;
    try { socket = new WebSocket(url); }
    catch { reject(new VoiceTransportError('Voice transport unavailable',true)); return; }
    let settled = false;
    let helloSent = false, uploaded = false, ready = false;
    const transportFailure = (message:string) => new VoiceTransportError(message,!helloSent && !uploaded);
    const finish = (error?:Error, value?:TranscriptResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      signal?.removeEventListener('abort',cancel);
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) socket.close();
      if (error) reject(error); else resolve(value || transcriptResult(''));
    };
    const cancel = () => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'cancel'}));
      socket.close(); finish(new DOMException('Cancelled','AbortError'));
    };
    const timeout = setTimeout(() => finish(transportFailure('Voice socket timed out')),35000);
    signal?.addEventListener('abort',cancel,{once:true});
    socket.onerror = () => finish(transportFailure('Voice transport unavailable'));
    socket.onclose = event => finish(new VoiceTransportError('Voice transport disconnected',
      event.code===1009 && blob.size<=httpMaxAudioBytes));
    socket.onopen = () => {socket.send(JSON.stringify({type:'start',csrf:csrfToken,turn_id:turnId,language,mime:blob.type}));helloSent=true;};
    socket.onmessage = async event => {
      if (settled) return;
      try {
        const data = JSON.parse(event.data) as {type:string;turn_id?:string;text?:string;code?:string;max_windowed_audio_bytes?:number;
          detected_language?:LanguageCode|null;language_probability?:number|null;confidence?:number|null;reject_reason?:string|null;suggest_language_switch?:boolean};
        if (data.type === 'ready') {
          if(ready || data.turn_id !== turnId){finish(new Error('Invalid voice turn acknowledgement'));return;}
          ready=true;
          const serverLimit=typeof data.max_windowed_audio_bytes==='number'?data.max_windowed_audio_bytes:maxWindowedAudioBytes;
          if(blob.size>Math.min(maxWindowedAudioBytes,serverLimit)){finish(new VoiceTransportError('Windowed voice size limit exceeded',blob.size<=httpMaxAudioBytes));return;}
          const buffer = await blob.arrayBuffer();
          if (settled || signal?.aborted || socket.readyState !== WebSocket.OPEN) return;
          for (let position = 0; position < buffer.byteLength; position += 65536) {
            socket.send(buffer.slice(position,position+65536));
          }
          uploaded = true;
          socket.send(JSON.stringify({type:'end'}));
        } else if (data.type === 'final' && ready && uploaded && data.turn_id === turnId)
          finish(undefined,transcriptResult(data.text||'',data.detected_language??null,data.language_probability??null,data.suggest_language_switch===true,data.confidence??null,data.reject_reason??null));
        else if (data.type === 'error') finish(new Error(`STT: ${data.code || 'unavailable'}`));
      } catch { finish(new Error('Invalid voice response')); }
    };
  });
}

/** Deliberately narrow: ONLY ignore a short acknowledgement spoken over the
 * assistant. Never suppress "no", "stop", "yes", named services or room requests.
 * This lexical heuristic is NOT LiveKit's adaptive acoustic interruption model. */
export function isPlaybackBackchannel(text:string,language:LanguageCode):boolean {
  const cue=text.normalize('NFKC').trim().toLowerCase().replace(/[.,!?…。，！？]/gu,'').replace(/\s+/gu,' ');
  const acknowledgements:Record<LanguageCode,readonly string[]>={
    vi:['ừ','ừm','ờ','ừ hử'],
    en:['mm','mhm','mm-hmm','uh-huh','uh huh','hmm','um'],
    zh:['嗯','嗯嗯'],
    ko:['음','으음','흠'],
  };
  return acknowledgements[language].includes(cue);
}

export const transcribeStream = streamTranscribe;

/** Live 16 kHz PCM capture transport. PCM frames are emitted while the guest is
 * speaking. Partial Vosk hypotheses have no business/RAG authority; only the
 * final transcript resolves the promise. The old WAV path stays available. */
export interface PCMTransport {
  push(frame:Uint8Array):void;
  end():Promise<TranscriptResult>;
  abort():void;
}
export function openPCMStream(language:LanguageCode,turnId:string,signal?:AbortSignal,
  onPartial?:(text:string,revision:number,stableText?:string,segmentComplete?:boolean)=>void,
  policy?:KioskConfig['voice_policy']):PCMTransport {
  if(!csrfToken)throw new ApiError(401,'Voice session is not initialized');
  if(signal?.aborted)throw new DOMException('Cancelled','AbortError');
  const url=new URL('/api/audio/stream',window.location.href);
  url.protocol=url.protocol==='https:'?'wss:':'ws:';
  // Protocol is application-level backpressure: the browser may transmit
  // only bytes for which the server has issued credit. Never rely on the
  // WebSocket implementation's bufferedAmount as a flow-control contract.
  const QUEUE_CAP=Math.min(1_000_000,Math.max(65536,policy?.queue_bytes??1_000_000));
  const queued:Uint8Array[]=[];
  let queueBytes=0, ready=false, helloSent=false, ended=false;
  let endSent=false, settled=false, lastRevision=0;
  let creditBytes=0,maxFrameBytes=Math.min(131072,Math.max(4096,policy?.max_frame_bytes??131072));
  let backpressureTimer:ReturnType<typeof setTimeout>|undefined;
  let socket:WebSocket;
  let resolve!:(value:TranscriptResult)=>void,reject!:(error:Error)=>void;
  const result=new Promise<TranscriptResult>((ok,fail)=>{resolve=ok;reject=fail;});
  const finish=(error?:Error,value?:TranscriptResult)=>{
    if(settled)return;
    settled=true;clearTimeout(timeout);if(backpressureTimer)clearTimeout(backpressureTimer);signal?.removeEventListener('abort',cancel);
    if(socket.readyState===WebSocket.CONNECTING||socket.readyState===WebSocket.OPEN)socket.close();
    if(error)reject(error);else resolve(value||transcriptResult(''));
  };
  const cancel=()=>{
    if(settled)return;
    if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'cancel'}));
    finish(new DOMException('Cancelled','AbortError'));
  };
  const armBackpressureTimeout=()=>{
    if(backpressureTimer||!queued.length||settled)return;
    const delay=Math.min(10000,Math.max(500,policy?.backpressure_timeout_ms??3000));
    backpressureTimer=setTimeout(()=>finish(new VoiceTransportError('BACKPRESSURE_TIMEOUT',false)),delay);
  };
  const sendFrame=(frame:Uint8Array):boolean=>{
    if(frame.byteLength>creditBytes)return false;
    const copy=new Uint8Array(frame);socket.send(copy.buffer);creditBytes-=copy.byteLength;return true;
  };
  const flush=()=>{
    if(!ready||settled||socket.readyState!==WebSocket.OPEN)return;
    while(queued.length&&!settled&&queued[0].byteLength<=creditBytes){const frame=queued.shift()!;queueBytes-=frame.byteLength;sendFrame(frame);}
    if(!queued.length&&backpressureTimer){clearTimeout(backpressureTimer);backpressureTimer=undefined;}
    if(queued.length)armBackpressureTimeout();
    if(ended&&!queued.length&&!endSent&&!settled){endSent=true;socket.send(JSON.stringify({type:'end'}));}
  };
  try{socket=new WebSocket(url);}catch{throw new VoiceTransportError('Voice transport unavailable',true);}
  // A rejected result is always observed by App even if the microphone ends
  // abruptly or the UI cancels its turn before awaiting end().
  void result.catch(()=>{});
  const timeout=setTimeout(()=>finish(new VoiceTransportError('Voice stream timed out',!helloSent)),38000);
  signal?.addEventListener('abort',cancel,{once:true});
  socket.onerror=()=>finish(new VoiceTransportError('Voice transport unavailable',!helloSent));
  socket.onclose=()=>finish(new VoiceTransportError('Voice stream disconnected',!helloSent));
  socket.onopen=()=>{
    if(settled)return;
    helloSent=true;
    socket.send(JSON.stringify({type:'start',csrf:csrfToken,turn_id:turnId,language,
      mime:'audio/pcm16;rate=16000;channels=1',protocol:2}));
  };
  socket.onmessage=event=>{
    if(settled)return;
    try{
      const data=JSON.parse(event.data) as {type:string;turn_id?:string;decoder?:string;
        text?:string;revision?:number;code?:string;final?:boolean;
        stable_text?:string;segment_complete?:boolean;protocol?:number;
        credit_bytes?:number;max_frame_bytes?:number;bytes?:number;
        detected_language?:LanguageCode|null;language_probability?:number|null;confidence?:number|null;reject_reason?:string|null;suggest_language_switch?:boolean};
      if(data.type==='ready'){
        if(ready||data.turn_id!==turnId||data.decoder!=='vosk_incremental_pcm16'||data.protocol!==2||
          !Number.isSafeInteger(data.credit_bytes)||data.credit_bytes!<=0||
          !Number.isSafeInteger(data.max_frame_bytes)||data.max_frame_bytes!<=0){
          finish(new VoiceTransportError('Invalid PCM decoder acknowledgement',false));return;
        }
        creditBytes=data.credit_bytes!;maxFrameBytes=data.max_frame_bytes!;
        ready=true;flush();
      }else if(data.type==='credit'){
        if(!ready||!Number.isSafeInteger(data.bytes)||data.bytes!<=0||data.bytes!>1_000_000){
          finish(new VoiceTransportError('Invalid voice credit',false));return;
        }
        creditBytes=Math.min(QUEUE_CAP,creditBytes+data.bytes!);flush();
      }else if(data.type==='partial'){
        if(ready&&data.turn_id===turnId&&data.final===false&&
          Number.isSafeInteger(data.revision)&&data.revision!>lastRevision){
          lastRevision=data.revision!;
          const stable=(typeof data.stable_text==='string' && data.stable_text.length<=1000 &&
            (data.text||'').startsWith(data.stable_text))?data.stable_text:'';
          onPartial?.(data.text||'',lastRevision,stable,data.segment_complete===true);
        }
      }else if(data.type==='final'){
        if(!ready||!endSent||data.turn_id!==turnId||data.final!==true){
          finish(new VoiceTransportError('Invalid PCM final transcript',false));return;
        }
        finish(undefined,transcriptResult(data.text||'',data.detected_language??null,data.language_probability??null,data.suggest_language_switch===true,data.confidence??null,data.reject_reason??null));
      }else if(data.type==='error'){
        // Pre-ready decoder absence is a controlled reason to allocate a *new*
        // turn and use the legacy WAV transport; never reuse this claimed turn.
        finish(new VoiceTransportError(`STT: ${data.code||'unavailable'}`,
          !ready&&['incremental_unavailable','incremental_language_unavailable','stt_busy'].includes(data.code||'')));
      }
    }catch{finish(new VoiceTransportError('Invalid PCM voice response',false));}
  };
  return {
    push(frame){
      if(settled||ended)throw new VoiceTransportError('PCM turn is not recording',false);
      if(!(frame instanceof Uint8Array)||!frame.byteLength||frame.byteLength%2||frame.byteLength>maxFrameBytes)
        throw new VoiceTransportError('Invalid PCM frame',false);
      if(ready&&frame.byteLength<=creditBytes)sendFrame(frame);
      else {
        if(queueBytes+frame.byteLength>QUEUE_CAP){
          finish(new VoiceTransportError('PCM queue limit exceeded',false));
          throw new VoiceTransportError('PCM queue limit exceeded',false);
        }
        queued.push(new Uint8Array(frame));queueBytes+=frame.byteLength;if(ready)armBackpressureTimeout();
      }
    },
    end(){ended=true;flush();return result;},
    abort:cancel,
  };
}
