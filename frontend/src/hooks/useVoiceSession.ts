import {useCallback,useReducer} from 'react';

export type VoiceStatus='off'|'connecting'|'ready'|'speaking'|'processing'|'playing'|'interrupted'|'recovering'|'error';

export interface VoiceSessionState{
  enabled:boolean;
  status:VoiceStatus;
  message:string;
}

export type VoiceSessionEvent=
  | {type:'ENABLE'}
  | {type:'DISABLE'}
  | {type:'STATUS';status:VoiceStatus}
  | {type:'ENGINE_STATE';state:string}
  | {type:'MESSAGE';message:string}
  | {type:'INTERRUPT'}
  | {type:'RECOVER'}
  | {type:'ERROR'};

const engineStatus=(state:string):VoiceStatus=>{
  switch(state){
    case 'off': return 'off';
    case 'starting': return 'connecting';
    case 'ready': return 'ready';
    case 'speaking': return 'speaking';
    case 'processing': return 'processing';
    case 'playing': return 'playing';
    default: return 'ready';
  }
};

export const initialVoiceSessionState:VoiceSessionState={enabled:false,status:'off',message:''};

export function voiceSessionReducer(state:VoiceSessionState,event:VoiceSessionEvent):VoiceSessionState{
  switch(event.type){
    case 'ENABLE': return {enabled:true,status:'connecting',message:state.message};
    case 'DISABLE': return initialVoiceSessionState;
    case 'STATUS': return {...state,enabled:event.status==='off'?false:state.enabled,status:event.status};
    case 'ENGINE_STATE': {
      const status=engineStatus(event.state);
      return {...state,enabled:status!=='off',status};
    }
    case 'MESSAGE': return {...state,message:event.message};
    case 'INTERRUPT': return {...state,status:state.enabled?'interrupted':'off',message:''};
    case 'RECOVER': return {...state,status:state.enabled?'recovering':'off'};
    case 'ERROR': return {...state,status:state.enabled?'error':'off',message:''};
    default: return state;
  }
}

export function useVoiceSession(){
  const [state,dispatch]=useReducer(voiceSessionReducer,initialVoiceSessionState);
  const enable=useCallback(()=>dispatch({type:'ENABLE'}),[]);
  const disable=useCallback(()=>dispatch({type:'DISABLE'}),[]);
  const setStatus=useCallback((status:VoiceStatus)=>dispatch({type:'STATUS',status}),[]);
  const setEngineState=useCallback((engineState:string)=>dispatch({type:'ENGINE_STATE',state:engineState}),[]);
  const setMessage=useCallback((message:string)=>dispatch({type:'MESSAGE',message}),[]);
  const interrupt=useCallback(()=>dispatch({type:'INTERRUPT'}),[]);
  const recovering=useCallback(()=>dispatch({type:'RECOVER'}),[]);
  const error=useCallback(()=>dispatch({type:'ERROR'}),[]);
  return {state,enable,disable,setStatus,setEngineState,setMessage,interrupt,recovering,error};
}
