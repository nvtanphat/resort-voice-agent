import {useEffect,useRef} from 'react';

const selector='button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useModalFocus(isOpen:boolean,onClose:()=>void){
  const dialogRef=useRef<HTMLDivElement|null>(null);
  const closeRef=useRef(onClose);
  closeRef.current=onClose;

  useEffect(()=>{
    if(!isOpen)return;
    const dialog=dialogRef.current;
    if(!dialog)return;
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const overlay=dialog.parentElement;
    const appShell=overlay?.parentElement;
    const background=appShell?Array.from(appShell.children).filter((node):node is HTMLElement=>node instanceof HTMLElement&&node!==overlay):[];
    const priorBackground=background.map(node=>({node,inert:node.inert,ariaHidden:node.getAttribute('aria-hidden')}));
    for(const node of background){node.inert=true;node.setAttribute('aria-hidden','true');}
    const focusables=()=>Array.from(dialog.querySelectorAll<HTMLElement>(selector)).filter(el=>!el.hasAttribute('aria-hidden'));
    const focusInitial=()=>{const items=focusables();(items[0]||dialog).focus();};
    const frame=requestAnimationFrame(focusInitial);
    const onKeyDown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){
        event.preventDefault();closeRef.current();return;
      }
      if(event.key!=='Tab')return;
      const items=focusables();
      if(!items.length){event.preventDefault();dialog.focus();return;}
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    };
    document.addEventListener('keydown',onKeyDown);
    return()=>{
      cancelAnimationFrame(frame);document.removeEventListener('keydown',onKeyDown);
      for(const {node,inert,ariaHidden} of priorBackground){node.inert=inert;if(ariaHidden===null)node.removeAttribute('aria-hidden');else node.setAttribute('aria-hidden',ariaHidden);}
      if(previous?.isConnected)previous.focus();
    };
  },[isOpen]);

  return dialogRef;
}
