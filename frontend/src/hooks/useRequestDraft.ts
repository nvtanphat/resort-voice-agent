import {useState} from 'react';
import type {RequestKind} from '../api';

export type SuggestedRequest = {kind:RequestKind;details:string;service?:string};
export type PendingProposal = {
  proposal_id:string;kind:RequestKind;details:string;expires_at:number;
  staff_verification_required?:boolean;price_disclosure_required?:boolean;
  price_disclosure?:string;outside_operating_hours?:boolean;next_open_at?:number|null;
};

/** Own the mutable request form independently from chat and voice state. */
export function useRequestDraft(){
 const [requestType,setRequestType]=useState<RequestKind|null>(null);
 const [quantity,setQuantity]=useState(1);
 const [roomNumber,setRoomNumber]=useState('');
 const [requestTime,setRequestTime]=useState('');
 const [partySize,setPartySize]=useState('');
 const [note,setNote]=useState('');
 const [suggestedDetails,setSuggestedDetails]=useState<SuggestedRequest|null>(null);
 const [pendingProposal,setPendingProposal]=useState<PendingProposal|null>(null);
 const [priceAcknowledged,setPriceAcknowledged]=useState(false);
 const [submitting,setSubmitting]=useState(false);
 const [verificationModalOpen,setVerificationModalOpen]=useState(false);
 const [isEditModalOpen,setIsEditModalOpen]=useState(false);
 const [dataConsent,setDataConsent]=useState(false);
 return {
  requestType,setRequestType,quantity,setQuantity,roomNumber,setRoomNumber,
  requestTime,setRequestTime,partySize,setPartySize,note,setNote,
  suggestedDetails,setSuggestedDetails,pendingProposal,setPendingProposal,
  priceAcknowledged,setPriceAcknowledged,submitting,setSubmitting,
  verificationModalOpen,setVerificationModalOpen,isEditModalOpen,setIsEditModalOpen,
  dataConsent,setDataConsent,
 };
}
