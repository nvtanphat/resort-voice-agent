import {PipecatClient} from '@pipecat-ai/client-js';
import {ProtobufFrameSerializer,WebSocketTransport,WavMediaManager} from '@pipecat-ai/websocket-transport';
import {SpeechPlayback} from './speechPlayback';
import type {Answer,LanguageCode} from './api';
import {voiceAgentConnection} from './api';

export interface VoiceAgentCallbacks{
  onState:(state:'connecting'|'ready'|'speaking'|'processing'|'playing'|'off')=>void;
  onTranscript:(text:string)=>void;
  onProgress:(event:string)=>void;
  onAnswer:(answer:Answer)=>void;
  onError:(error:Error)=>void;
}

type ServerEvent={type?:string;event?:string;answer?:Answer;token?:string};

class OrderedFrameSerializer extends ProtobufFrameSerializer {
  private pending:Promise<void>=Promise.resolve();
  deserialize(data:unknown){
    const parsed=this.pending.then(()=>super.deserialize(data));
    this.pending=parsed.then(()=>{},()=>{});
    return parsed;
  }
}

export class VoiceAgent{
  private client:PipecatClient|null=null;
  private stopping=false;
  private playback:SpeechPlayback|null=null;

  constructor(private readonly callbacks:VoiceAgentCallbacks){}

  async start(language:LanguageCode):Promise<void>{
    if(this.client)return;
    this.stopping=false;
    this.callbacks.onState('connecting');
    const context=new AudioContext({sampleRate:16000});
    await context.resume();
    const playback=this.playback=new SpeechPlayback(context,16000,(token,status)=>{
      this.client?.sendClientMessage('speech.playback',{token,status});
      if(status==='played')this.callbacks.onState('ready');
    });
    const media=new WavMediaManager(undefined,16000);
    media.bufferBotAudio=(data:Int16Array|ArrayBuffer)=>{
      const samples=data instanceof Int16Array?data:new Int16Array(data);
      playback.enqueue(samples);
      return samples;
    };
    const transport=new WebSocketTransport({
      serializer:new OrderedFrameSerializer(),
      recorderSampleRate:16000,
      playerSampleRate:16000,
      mediaManager:media,
    });
    const client=new PipecatClient({
      transport,enableMic:true,enableCam:false,
      callbacks:{
        onConnected:()=>this.callbacks.onState('ready'),
        onDisconnected:()=>{playback.interrupt();if(!this.stopping)this.callbacks.onState('off');},
        onUserStartedSpeaking:()=>{playback.interrupt();this.callbacks.onState('speaking');},
        onUserStoppedSpeaking:()=>this.callbacks.onState('processing'),
        onBotStartedSpeaking:()=>this.callbacks.onState('playing'),
        onBotStoppedSpeaking:()=>{}, // Browser playback completion sets ready.
        onUserTranscript:data=>{if(data.final&&data.text.trim())this.callbacks.onTranscript(data.text.trim());},
        onServerMessage:data=>{
          const event=data as ServerEvent;
          if(event.type==='speech.chunk.start')playback.begin();
          else if(event.type==='speech.chunk.end'&&typeof event.token==='string')playback.end(event.token);
          else if(event.type==='agent.progress'&&event.event)this.callbacks.onProgress(event.event);
          else if(event.type==='answer.card'&&event.answer)this.callbacks.onAnswer(event.answer);
        },
        onError:message=>this.callbacks.onError(new Error(
          String((message.data as {error?:string}|undefined)?.error||'Voice agent error'))),
        onMessageError:()=>this.callbacks.onError(new Error('Voice agent protocol error')),
        onDeviceError:error=>this.callbacks.onError(new Error(error.message||'Microphone unavailable')),
      },
    });
    this.client=client;
    try{
      await client.initDevices();
      await client.connect(voiceAgentConnection(language));
      this.callbacks.onState('ready');
    }catch(error){
      this.client=null;
      try{await client.disconnect();}catch{}
      await playback.close();
      this.playback=null;
      throw error;
    }
  }

  async stop():Promise<void>{
    const client=this.client;
    this.client=null;
    this.stopping=true;
    const playback=this.playback;
    this.playback=null;
    try{if(client)await client.disconnect();}
    finally{if(playback)await playback.close();}
    this.callbacks.onState('off');
  }
}
