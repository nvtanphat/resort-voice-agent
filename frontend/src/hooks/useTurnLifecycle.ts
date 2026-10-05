import {useCallback,useRef} from 'react';

export function useTurnLifecycle(){
  const versionRef=useRef(0);
  const controllerRef=useRef<AbortController|null>(null);
  const currentTurnRef=useRef<string|null>(null);

  const beginRequest=useCallback(()=>{
    const version=++versionRef.current;
    controllerRef.current?.abort();
    const controller=new AbortController();
    controllerRef.current=controller;
    return {version,controller};
  },[]);

  const invalidateRequest=useCallback(()=>{
    ++versionRef.current;
    controllerRef.current?.abort();
    controllerRef.current=null;
  },[]);

  const clearController=useCallback((controller:AbortController)=>{
    if(controllerRef.current===controller)controllerRef.current=null;
  },[]);

  return {versionRef,controllerRef,currentTurnRef,beginRequest,invalidateRequest,clearController};
}
