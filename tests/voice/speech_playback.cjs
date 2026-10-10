const {test}=require('node:test');
const assert=require('node:assert/strict');
const {SpeechPlayback}=require('../../.cache/voice-tests/speechPlayback.js');

function harness(){
  const sources=[],acks=[];
  const context={state:'running',currentTime:0,destination:{},
    createBuffer:(_,length,rate)=>({duration:length/rate,getChannelData:()=>new Float32Array(length)}),
    createBufferSource:()=>{
      const source={connect(){},disconnect(){},start(){},stop(){},onended:null};
      sources.push(source);return source;
    },async close(){this.state='closed';}};
  return {player:new SpeechPlayback(context,16000,(...ack)=>acks.push(ack)),sources,acks,context};
}

test('ACK waits for every audio source to end',()=>{
  const {player,sources,acks}=harness();
  player.begin();player.enqueue(new Int16Array(160));player.enqueue(new Int16Array(160));
  player.end('token');assert.deepEqual(acks,[]);
  sources[0].onended();assert.deepEqual(acks,[]);
  sources[1].onended();assert.deepEqual(acks,[['token','played']]);
});
test('audio ended before marker still requires owned token',()=>{
  const {player,sources,acks}=harness();
  player.begin();player.enqueue(new Int16Array(160));sources[0].onended();
  assert.deepEqual(acks,[]);player.end('token');assert.deepEqual(acks,[['token','played']]);
});
test('interruption before end marker reports failure',()=>{
  const {player,acks}=harness();player.begin();player.enqueue(new Int16Array(160));
  player.interrupt();player.end('token');assert.deepEqual(acks,[['token','failed']]);
});
test('empty or suspended audio cannot be acknowledged as played',()=>{
  for(const suspended of [false,true]){
    const {player,context,acks}=harness();player.begin();
    if(suspended){context.state='suspended';player.enqueue(new Int16Array(160));}
    player.end('token');assert.deepEqual(acks,[['token','failed']]);
  }
});
test('disconnect cannot generate a success ACK',async()=>{
  const {player,acks}=harness();player.begin();player.enqueue(new Int16Array(160));
  await player.close();player.end('token');assert.deepEqual(acks,[]);
});
