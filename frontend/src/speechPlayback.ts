/** Acknowledge only audio that reaches AudioBufferSourceNode.onended. */
export class SpeechPlayback {
  private sources = new Set<AudioBufferSourceNode>();
  private nextAt = 0;
  private token: string|null = null;
  private failed = false;
  private receivedAudio = false;
  private closed = false;

  constructor(private context: AudioContext, private sampleRate: number,
              private acknowledge: (token:string,status:'played'|'failed')=>void) {}

  begin():void {
    this.interrupt();
    this.failed = false;
    this.receivedAudio = false;
    this.nextAt = this.context.currentTime;
  }

  enqueue(samples:Int16Array):void {
    if(this.failed||this.closed||!samples.length)return;
    try {
      if(this.context.state!=='running')throw new Error('Audio output is suspended');
      const buffer = this.context.createBuffer(1,samples.length,this.sampleRate);
      const channel = buffer.getChannelData(0);
      for(let i=0;i<samples.length;i++)channel[i]=samples[i]/32768;
      const source = this.context.createBufferSource();
      source.buffer=buffer;
      source.connect(this.context.destination);
      source.onended=()=>{
        this.sources.delete(source);
        source.disconnect();
        this.finish();
      };
      this.sources.add(source);
      this.nextAt=Math.max(this.nextAt,this.context.currentTime);
      source.start(this.nextAt);
      this.nextAt+=buffer.duration;
      this.receivedAudio=true;
    }catch {
      this.interrupt();
    }
  }

  end(token:string):void {
    if(this.closed)return;
    this.token=token;
    this.finish();
  }

  private finish():void {
    if(!this.token||this.sources.size)return;
    const token=this.token;
    this.token=null;
    this.acknowledge(token,!this.failed&&this.receivedAudio&&this.context.state==='running'
      ?'played':'failed');
  }

  interrupt():void {
    this.failed=true;
    for(const source of this.sources){
      source.onended=null;
      try{source.stop();}catch{}
      source.disconnect();
    }
    this.sources.clear();
    this.finish();
  }

  async close():Promise<void> {
    this.closed=true;
    this.token=null;
    this.interrupt();
    await this.context.close();
  }
}
