import {useCallback,useRef} from 'react';
import * as api from '../api';
import type {LanguageCode} from '../api';

export interface SpeechChunkRef{id:string;ordinal:number;}
export type VoiceLatencyStage=Parameters<typeof api.reportVoiceLatency>[0];

interface PlayPlanArgs{
  chunks:SpeechChunkRef[];
  signal?:AbortSignal;
  isCurrent:()=>boolean;
  language:LanguageCode;
  endedAt?:number;
  setStatus:(status:'playing'|'ready')=>void;
  observe:(stage:VoiceLatencyStage,ms:number,language:LanguageCode)=>void;
}

export function useSpeechPlayback(){
  const playbackRef=useRef<HTMLAudioElement|null>(null);
  const audioUrlRef=useRef<string|null>(null);
  const playbackWaiterRef=useRef<(()=>void)|null>(null);
  const pausedForSpeechRef=useRef<HTMLAudioElement|null>(null);
  const pauseRecoveryRef=useRef<AbortController|null>(null);
  const bargeProbeRevisionRef=useRef(0);
  const bargePauseStartedRef=useRef<number|null>(null);

  const stopPlayback=useCallback(()=>{
    pauseRecoveryRef.current?.abort();pauseRecoveryRef.current=null;
    bargeProbeRevisionRef.current++;bargePauseStartedRef.current=null;
    const settle=playbackWaiterRef.current;playbackWaiterRef.current=null;pausedForSpeechRef.current=null;
    if(playbackRef.current){
      playbackRef.current.onended=null;playbackRef.current.onerror=null;
      playbackRef.current.pause();playbackRef.current.removeAttribute('src');playbackRef.current=null;
    }
    if(audioUrlRef.current){URL.revokeObjectURL(audioUrlRef.current);audioUrlRef.current=null;}
    settle?.();
  },[]);

  const playPlan=useCallback(async({chunks,signal,isCurrent,language,endedAt,setStatus,observe}:PlayPlanArgs)=>{
    const ttsStarted=performance.now();
    let firstAudio=false;
    let playbackStarted=0;
    let prefetched:{id:string;promise:Promise<Blob|null>}|null=null;
    for(let index=0;index<chunks.length;index++){
      const chunk=chunks[index];
      if(!isCurrent())return;
      const chunkId=chunk.id;
      const prefetchedBlob=prefetched?.id===chunkId?await prefetched.promise:null;
      prefetched=null;
      const blob=prefetchedBlob??await api.speakChunk(chunkId,signal);
      if(!isCurrent())return;
      const next=chunks[index+1];
      // Synthesize one chunk ahead while this chunk is playing. The server still
      // revalidates proof immediately before each playback and ACK remains ordered.
      if(next)prefetched={id:next.id,promise:api.speakChunk(next.id,signal).catch(()=>null)};
      await api.checkSpeechChunk(chunkId,signal);
      if(!isCurrent())return;
      const url=URL.createObjectURL(blob);audioUrlRef.current=url;
      const audio=new Audio(url);playbackRef.current=audio;
      try{
        await new Promise<void>((resolve,reject)=>{
          const onStop=()=>resolve();playbackWaiterRef.current=onStop;
          const fail=(error:unknown)=>{
            if(playbackWaiterRef.current===onStop)playbackWaiterRef.current=null;
            stopPlayback();reject(error);
          };
          audio.onended=()=>stopPlayback();
          audio.onerror=()=>fail(new Error('Voice playback failed'));
          setStatus('playing');
          void audio.play().then(()=>{
            if(!isCurrent()||firstAudio)return;
            firstAudio=true;playbackStarted=performance.now();
            observe('client.tts_first_audio',playbackStarted-ttsStarted,language);
            if(endedAt!==undefined)observe('client.e2e_first_audio',playbackStarted-endedAt,language);
          }).catch(error=>{
            if(error instanceof DOMException&&error.name==='AbortError'&&pausedForSpeechRef.current===audio)return;
            fail(error);
          });
        });
      }catch(error){
        await api.markSpeechChunkPlaybackFailed(chunkId).catch(()=>{});
        throw error;
      }
      if(!isCurrent())return;
      await api.markSpeechChunkPlayed(chunkId,signal);
      if(!isCurrent())return;
    }
    if(playbackStarted)observe('client.playback_total',performance.now()-playbackStarted,language);
  },[stopPlayback]);

  return {
    playbackRef,audioUrlRef,playbackWaiterRef,pausedForSpeechRef,pauseRecoveryRef,
    bargeProbeRevisionRef,bargePauseStartedRef,stopPlayback,playPlan,
  };
}
