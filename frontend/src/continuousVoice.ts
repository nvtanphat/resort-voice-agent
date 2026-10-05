/** Local, continuous microphone capture with bounded PCM and conservative endpointing.
 * Microphone permission is requested once per Voice Mode. Only utterance windows
 * are sent to the existing, authenticated FastAPI voice transport; raw audio is
 * never stored. This is client-side energy VAD, not a speech-recognition model.
 */
export type VoiceState = 'off'|'starting'|'ready'|'speaking'|'processing'|'playing';
export interface VoiceCallbacks {
  onState: (state:VoiceState)=>void;
  onSpeechStart: ()=>void;
  /** Early acoustic candidate: pause output reversibly; do not cancel its turn. */
  onBargeInCandidate?: (elapsedMs:number)=>void;
  /** A short candidate ended before speech was confirmed; restore only after proof. */
  onBargeInRejected?: ()=>void;
  onUtterance: (audio:Blob)=>void;
  /** Optional incremental decoder transport; final transcript is still authoritative. */
  onPCMStart?: ()=>boolean;
  onPCMFrame?: (frame:Uint8Array)=>void;
  onPCMEnd?: (accepted:boolean)=>void;
  onError: (error:Error)=>void;
  isPlaybackActive: ()=>boolean;
  onActivity: ()=>void;
  /** Normalized input level for a small, privacy-safe mic meter. */
  onVolume?: (level:number)=>void;
  /** Speech was detected but too brief/quiet to send; ask the guest to repeat. */
  onUtteranceTooShort?: ()=>void;
}

export function wavFromFloat(samples:Float32Array, sampleRate:number):Blob {
  const bytes=new ArrayBuffer(44+samples.length*2);
  const view=new DataView(bytes);
  const ascii=(at:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
  ascii(0,'RIFF');view.setUint32(4,bytes.byteLength-8,true);ascii(8,'WAVE');ascii(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);ascii(36,'data');view.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++){
    const value=Math.max(-1,Math.min(1,samples[i]));
    view.setInt16(44+i*2,value<0?Math.round(value*32768):Math.round(value*32767),true);
  }
  return new Blob([bytes],{type:'audio/wav'});
}

/** Stateful linear interpolation to exactly 16 kHz mono PCM16. Input chunks may
 * be arbitrary AudioWorklet sizes; the interpolation position spans all chunks.
 * Does not persist audio or infer speech content. */
export class PCM16Resampler {
  private nextAt=0;
  private consumed=0;
  private previous=0;
  constructor(private readonly inputRate:number){
    if(!Number.isFinite(inputRate)||inputRate<16000||inputRate>192000)throw new Error('Unsupported microphone sample rate');
  }
  convert(frame:Float32Array):Uint8Array {
    if(!frame.length)return new Uint8Array();
    const start=this.consumed, end=start+frame.length-1, step=this.inputRate/16000;
    const values:number[]=[];
    while(this.nextAt<=end){
      const index=Math.floor(this.nextAt), fraction=this.nextAt-index;
      if(index===end && fraction>0)break; // next input frame supplies the upper sample
      const lower=index<start?this.previous:frame[index-start];
      const upper=index+1<=end?frame[index+1-start]:lower;
      const value=Math.max(-1,Math.min(1,lower+(upper-lower)*fraction));
      values.push(value<0?Math.round(value*32768):Math.round(value*32767));
      this.nextAt+=step;
    }
    this.previous=frame[frame.length-1];this.consumed+=frame.length;
    const bytes=new Uint8Array(values.length*2), view=new DataView(bytes.buffer);
    for(let i=0;i<values.length;i++)view.setInt16(i*2,values[i],true);
    return bytes;
  }
}

// Stage one is a reversible speaker pause. Stage two authorizes an STT probe,
// never a business request. The model transcript still decides interruption.
export interface VadTuning {
  engine?:'energy'|'silero_v5';noise_floor_initial:number;positive_threshold:number;negative_threshold:number;
  redemption_ms:number;pre_speech_pad_ms:number;min_speech_ms:number;
  playback_positive_threshold:number;continuation_extra_ms:number;
}
export interface VoicePolicyTuning {
  vad_start_ms:number;vad_end_silence_ms:number;barge_preview_ms:number;barge_confirm_ms:number;
  vad:VadTuning;continuation_cues?:string[];clause_delimiters?:string[];sentence_endings?:string[];
}
 // ignore one short VAD dropout before restoring the speaker
/** bounded *provisional lexical hint* for acoustic endpointing.
 * A partial transcript can be revised and is NEVER a final STT result or a
 * permission for /ask, memory, booking or TTS. Not model-based semantics. */
export function endpointSilenceMs(voicedMs:number, partial='', reliableClosing=true,
  baseEndSilenceMs:number, continuationCues:string[], continuationExtraMs:number,
  sentenceEndings:string[]):number {
  const text=partial.trim();
  const base=Math.min(1800,Math.max(400,baseEndSilenceMs));
  const baseline=voicedMs>=2400?Math.max(base,1100):base;
  if(!text)return baseline;
  // Conservative: an unfinished connective needs a longer breathing pause.
  const lowered=text.toLocaleLowerCase();
  if(continuationCues.some(cue=>lowered.endsWith(cue.toLocaleLowerCase())))
    return Math.min(1800,baseline+Math.max(0,continuationExtraMs));
  // Strong closing punctuation is only a pacing cue, never a transcript commit.
  if(reliableClosing&&sentenceEndings.some(ending=>text.endsWith(ending))&&voicedMs>=500)return 650;
  return baseline;
}

export class ContinuousVoice {
  private callbacks:VoiceCallbacks;
  private context:AudioContext|null=null;
  private media:MediaStream|null=null;
  private source:MediaStreamAudioSourceNode|null=null;
  private processor:AudioWorkletNode|null=null;
  private silent:GainNode|null=null;
  private active=false;
  private generation=0;
  private signalMs=0;
  private voicedMs=0;
  private silenceMs=0;
  private candidateMs=0;
  private bargeCandidate=false;
  private candidateSilenceMs=0;
  private noiseFloor=0;
  private envelope=0;
  private lastVoicedAt=0;
  private preRoll:Float32Array[]=[];
  private preRollSamples=0;
  private utterance:Float32Array[]|null=null;
  private utteranceSamples=0;
  private maxSamples=0;
  private maxBytes=0;
  private sampleRate=0;
  private pcmResampler:PCM16Resampler|null=null;
  private pcmChunks:Uint8Array[]=[];
  private pcmPendingBytes=0;
  private provisionalText='';
  private provisionalRevision=0;
  private provisionalClosingReliable=true;
  private tuning:{vadStartMs:number;vadEndSilenceMs:number;bargePreviewMs:number;bargeConfirmMs:number;vad:VadTuning;continuationCues:string[];clauseDelimiters:string[];sentenceEndings:string[]}|null=null;

  constructor(callbacks:VoiceCallbacks){this.callbacks=callbacks;}
  update(callbacks:VoiceCallbacks){this.callbacks=callbacks;}
  get running(){return this.active;}
  /** Accept only a newer hint for an active incremental utterance. */
  noteProvisionalTranscript(text:string,revision:number,stableText?:string,segmentComplete=false):void {
    if(!this.active||!this.utterance||!this.pcmResampler||
       !Number.isSafeInteger(revision)||revision<=this.provisionalRevision||
       typeof text!=='string'||text.length>1000)return;
    this.provisionalRevision=revision;
    this.provisionalText=text;
    // New Vosk partials supply a repeated-token prefix. Punctuation in an
    // unstable hypothesis must not prematurely close the guest's turn.
    // A completed Kaldi segment is still only a pacing hint, never final STT.
    this.provisionalClosingReliable = stableText===undefined || segmentComplete ||
      (typeof stableText==='string' && text.startsWith(stableText) &&
       /[.!?。！？]$/u.test(stableText.trim()));
  }

  async start(maxSeconds:number,maxBytes:number,tuning?:VoicePolicyTuning):Promise<void>{
    if(!tuning?.vad||!Array.isArray(tuning.continuation_cues)||!Array.isArray(tuning.sentence_endings))
      throw new Error('Voice policy tuning is unavailable');
    this.tuning={
      vadStartMs:Math.min(600,Math.max(100,tuning.vad_start_ms)),
      vadEndSilenceMs:Math.min(1800,Math.max(400,tuning.vad_end_silence_ms)),
      bargePreviewMs:Math.min(400,Math.max(80,tuning.barge_preview_ms)),
      bargeConfirmMs:Math.min(1200,Math.max(250,tuning.barge_confirm_ms)),
      vad:tuning.vad,continuationCues:tuning.continuation_cues,
      clauseDelimiters:tuning.clause_delimiters||[],sentenceEndings:tuning.sentence_endings,
    };
    if(this.tuning.bargePreviewMs>=this.tuning.bargeConfirmMs)throw new Error('Invalid voice policy');
    if(this.active)return;
    if(!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode)throw new Error('Web Audio microphone is unavailable');
    const generation=++this.generation;
    const current=()=>this.active&&this.generation===generation;
    this.noiseFloor=tuning.vad.noise_floor_initial;
    this.active=true;
    this.callbacks.onState('starting');
    try{
      const media=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      // A permission dialog may finish after the guest already disabled Voice Mode.
      if(!current()){media.getTracks().forEach(track=>track.stop());return;}
      this.media=media;
      media.getAudioTracks().forEach(track=>{track.onended=()=>{if(current()){this.stop();this.callbacks.onError(new Error('Microphone disconnected'));}};});
      const context=new AudioContext();this.context=context;
      await context.audioWorklet.addModule('/static/pcm-worklet.js');
      if(!current())return;
      this.sampleRate=context.sampleRate;
      // The backend applies a byte cap and native decoder duration limit.
      this.maxSamples=Math.min(Math.floor(Math.min(maxSeconds,23)*this.sampleRate),Math.floor((maxBytes-44)/2));
      this.maxBytes=maxBytes;
      if(this.maxSamples<=this.sampleRate)throw new Error('Audio size limit is too small');
      this.source=context.createMediaStreamSource(media);
      this.processor=new AudioWorkletNode(context,'concierge-pcm');
      this.silent=context.createGain();this.silent.gain.value=0;
      this.processor.port.onmessage=(event:MessageEvent<Float32Array>)=>{if(current())this.receive(event.data);};
      this.source.connect(this.processor);this.processor.connect(this.silent);this.silent.connect(context.destination);
      await context.resume();if(current())this.callbacks.onState('ready');
    }catch(error){
      // A stopped prior generation must never shut down a newer active one.
      if(!current())return;
      this.stop();throw error;
    }
  }

  stop():void{
    ++this.generation;
    if(this.pcmResampler){this.callbacks.onPCMEnd?.(false);this.pcmResampler=null;}
    this.pcmChunks=[];this.pcmPendingBytes=0;this.provisionalText='';this.provisionalRevision=0;this.provisionalClosingReliable=true;
    this.active=false;this.utterance=null;this.preRoll=[];this.preRollSamples=0;this.utteranceSamples=0;
    this.voicedMs=0;this.silenceMs=0;this.candidateMs=0;this.candidateSilenceMs=0;this.bargeCandidate=false;this.signalMs=0;this.noiseFloor=this.tuning?.vad.noise_floor_initial??0;this.envelope=0;this.lastVoicedAt=0;
    if(this.processor){this.processor.port.onmessage=null;this.processor.disconnect();this.processor=null;}
    this.source?.disconnect();this.source=null;this.silent?.disconnect();this.silent=null;
    this.media?.getTracks().forEach(track=>track.stop());this.media=null;
    if(this.context){void this.context.close().catch(()=>{});this.context=null;}
    this.callbacks.onState('off');
  }

  private receive(frame:Float32Array):void{
    if(!this.active||!frame?.length)return;
    const duration=1000*frame.length/this.sampleRate;
    let power=0;for(let i=0;i<frame.length;i++)power+=frame[i]*frame[i];
    const rms=Math.sqrt(power/frame.length);
    this.callbacks.onVolume?.(Math.max(0,Math.min(1,rms*12)));
    const playing=this.callbacks.isPlaybackActive();
    const tuning=this.tuning;if(!tuning)return;
    // Do not train the ambient noise floor on our own speaker output.
    // Peak-hold envelope (~50 ms): single 2.7 ms frames dip below the threshold
    // inside a word, which used to reset the start-of-speech counter.
    this.envelope=Math.max(rms,this.envelope*0.96);
    const threshold=playing?Math.max(tuning.vad.playback_positive_threshold,this.noiseFloor*6):Math.max(tuning.vad.positive_threshold,this.noiseFloor*2.5);
    const voiced=this.envelope>=threshold;
    const nowMs=performance.now();
    if(voiced)this.lastVoicedAt=nowMs;
    // Learn the ambient floor ONLY from frames that are not speech. Training on
    // every quiet frame made a soft voice raise its own threshold above itself.
    // Also freeze it for 1 s after any speech: soft syllables between louder
    // ones are below the threshold too and must not raise it.
    if(!playing&&!this.utterance&&!voiced&&nowMs-this.lastVoicedAt>1000&&rms<tuning.vad.negative_threshold*8)this.noiseFloor=Math.min(tuning.vad.positive_threshold*1.34,0.985*this.noiseFloor+0.015*rms);
    this.preRoll.push(frame);this.preRollSamples+=frame.length;
    const limit=Math.round(tuning.vad.pre_speech_pad_ms*this.sampleRate/1000);
    while(this.preRollSamples>limit+frame.length&&this.preRoll.length>1){
      const removed=this.preRoll.shift()!;this.preRollSamples-=removed.length;
    }
    if(!this.utterance){
      if(!voiced){
        this.candidateSilenceMs+=duration;
        if(this.candidateSilenceMs>=tuning.vad.redemption_ms){
          // A preview must never strand an approved answer paused. Debounce
          // a brief VAD dropout so natural consonant gaps do not flap audio.
          if(this.bargeCandidate){
            this.bargeCandidate=false;
            this.callbacks.onBargeInRejected?.();
          }
          this.candidateMs=0;
        }
      }else{
        this.candidateSilenceMs=0;
        this.candidateMs+=duration;
        if(playing&&!this.bargeCandidate&&this.candidateMs>=tuning.bargePreviewMs){
          this.bargeCandidate=true;
          this.callbacks.onBargeInCandidate?.(this.candidateMs);
        }
      }
      if(this.candidateMs<(playing?tuning.bargeConfirmMs:tuning.vadStartMs))return;
      this.bargeCandidate=false;this.candidateSilenceMs=0;
      this.utterance=this.preRoll.slice();this.utteranceSamples=this.preRollSamples;
      this.voicedMs=this.candidateMs;this.silenceMs=0;this.signalMs=0;
      this.provisionalText='';this.provisionalRevision=0;this.provisionalClosingReliable=true;
      this.callbacks.onSpeechStart();this.callbacks.onActivity();this.callbacks.onState('speaking');
      if(this.callbacks.onPCMStart?.() && this.callbacks.onPCMFrame){
        this.pcmResampler=new PCM16Resampler(this.sampleRate);
        this.pcmChunks=[];this.pcmPendingBytes=0;
        for(const buffered of this.utterance)this.emitPCM(buffered);
      }
      this.candidateMs=0;
      return;
    }
    // Pre-roll already owns the first frame on transition to speaking.
    // Never exceed the declared WAV byte limit, even when a worklet frame
    // straddles the exact cap (e.g. an unusual sample rate or max_audio_bytes).
    const part=frame.subarray(0,Math.max(0,this.maxSamples-this.utteranceSamples));
    this.utterance.push(part);this.utteranceSamples+=part.length;
    this.emitPCM(part);
    const keptMs=1000*part.length/this.sampleRate;this.signalMs+=keptMs;
    if(voiced){this.voicedMs+=keptMs;this.silenceMs=0;}else this.silenceMs+=keptMs;
    if(this.silenceMs>=endpointSilenceMs(this.voicedMs,this.provisionalText,this.provisionalClosingReliable,
      tuning.vadEndSilenceMs,tuning.continuationCues,tuning.vad.continuation_extra_ms,tuning.sentenceEndings)||this.utteranceSamples>=this.maxSamples)this.finish();
  }

  private emitPCM(frame:Float32Array):void{
    if(!this.pcmResampler||!frame.length)return;
    const bytes=this.pcmResampler.convert(frame);
    if(bytes.length){this.pcmChunks.push(bytes);this.pcmPendingBytes+=bytes.byteLength;}
    if(this.pcmPendingBytes>=2048)this.flushPCM();
  }

  private flushPCM():void{
    if(!this.pcmPendingBytes)return;
    const merged=new Uint8Array(this.pcmPendingBytes);let offset=0;
    for(const chunk of this.pcmChunks){merged.set(chunk,offset);offset+=chunk.byteLength;}
    this.pcmChunks=[];this.pcmPendingBytes=0;
    this.callbacks.onPCMFrame?.(merged);
  }

  private finish():void{
    const segments=this.utterance;const length=this.utteranceSamples;
    this.utterance=null;this.utteranceSamples=0;this.signalMs=0;this.candidateMs=0;this.candidateSilenceMs=0;this.bargeCandidate=false;
    this.provisionalText='';this.provisionalRevision=0;this.provisionalClosingReliable=true;
    this.preRoll=[];this.preRollSamples=0;
    if(!this.active||!segments)return;
    const accepted=this.voicedMs>=(this.tuning?.vad.min_speech_ms??0) && length*2+44<=this.maxBytes;
    if(this.pcmResampler){
      if(accepted)this.flushPCM();
      this.callbacks.onPCMEnd?.(accepted);this.pcmResampler=null;
      this.pcmChunks=[];this.pcmPendingBytes=0;
    }
    if(!accepted){
      this.callbacks.onState(this.callbacks.isPlaybackActive()?'playing':'ready');
      if(length*2+44<=this.maxBytes&&!this.callbacks.isPlaybackActive())this.callbacks.onUtteranceTooShort?.();
      return;
    }
    const pcm=new Float32Array(length);let offset=0;
    for(const part of segments){pcm.set(part,offset);offset+=part.length;}
    // Do not block the AudioWorklet callback on inference or transport.
    this.callbacks.onState('processing');this.callbacks.onUtterance(wavFromFloat(pcm,this.sampleRate));
  }
}
