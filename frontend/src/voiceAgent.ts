import {PipecatClient} from '@pipecat-ai/client-js';
import {ProtobufFrameSerializer,WebSocketTransport} from '@pipecat-ai/websocket-transport';
import type {Answer,LanguageCode} from './api';
import {voiceAgentConnection} from './api';

export interface VoiceAgentCallbacks{
  onState:(state:'connecting'|'ready'|'speaking'|'processing'|'playing'|'off')=>void;
  onTranscript:(text:string)=>void;
  onProgress:(event:string)=>void;
  onAnswer:(answer:Answer)=>void;
  onError:(error:Error)=>void;
}

type ServerEvent={type?:string;event?:string;answer?:Answer};

export class VoiceAgent{
  private client:PipecatClient|null=null;
  private stopping=false;

  constructor(private readonly callbacks:VoiceAgentCallbacks){}

  async start(language:LanguageCode):Promise<void>{
    if(this.client)return;
    this.stopping=false;
    this.callbacks.onState('connecting');
    const transport=new WebSocketTransport({
      serializer:new ProtobufFrameSerializer(),
      recorderSampleRate:16000,
      playerSampleRate:16000,
    });
    const client=new PipecatClient({
      transport,enableMic:true,enableCam:false,
      callbacks:{
        onConnected:()=>this.callbacks.onState('ready'),
        onDisconnected:()=>{if(!this.stopping)this.callbacks.onState('off');},
        onUserStartedSpeaking:()=>this.callbacks.onState('speaking'),
        onUserStoppedSpeaking:()=>this.callbacks.onState('processing'),
        onBotStartedSpeaking:()=>this.callbacks.onState('playing'),
        onBotStoppedSpeaking:()=>this.callbacks.onState('ready'),
        onUserTranscript:data=>{if(data.final&&data.text.trim())this.callbacks.onTranscript(data.text.trim());},
        onServerMessage:data=>{
          const event=data as ServerEvent;
          if(event.type==='agent.progress'&&event.event)this.callbacks.onProgress(event.event);
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
      throw error;
    }
  }

  async stop():Promise<void>{
    const client=this.client;
    this.client=null;
    this.stopping=true;
    if(client)await client.disconnect();
    this.callbacks.onState('off');
  }
}
