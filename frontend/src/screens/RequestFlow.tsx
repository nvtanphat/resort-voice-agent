import type {ComponentProps} from 'react';
import {EditRequestModal} from '../components/EditRequestModal';
import {TicketModal} from '../components/TicketModal';
import {VerificationModal} from '../components/VerificationModal';

type EditProps=ComponentProps<typeof EditRequestModal>;
type VerificationProps=ComponentProps<typeof VerificationModal>;
type TicketProps=ComponentProps<typeof TicketModal>;

export type RequestFlowProps={
 edit:EditProps|null;
 verification:VerificationProps;
 ticket:TicketProps;
};

/** The prepare/confirm/edit modal flow, kept out of the chat screen. */
export function RequestFlow({edit,verification,ticket}:RequestFlowProps){
 return <>
  {edit&&<EditRequestModal {...edit}/>} 
  <VerificationModal {...verification}/>
  <TicketModal {...ticket}/>
 </>;
}
