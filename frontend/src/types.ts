import type { Citation, RequestKind, RequestStatus, MapGuidance, DraftPlan, TaskProgress, RelatedTopic, SupportContact } from './api';
export type { LanguageCode } from './api';
export interface ServiceItem { id:string; title:string; description:string; requestKind:RequestKind|null; question:string; iconCategory:string; actions:string[]; }
export interface ChatMessage {
  id:string; sender:'user'|'assistant'; time:string; text:string;
  answerTitle?:string|null;
 citations?:Citation[]; suggestedAction?:{kind:RequestKind;details:string;service?:string}|null; speechTurnId?:string;
 planIsDraft?:boolean; missingTopics?:string[]; mapGuidance?:MapGuidance;
 plan?:DraftPlan; evidenceStatus?:string; omittedClaims?:number;
 actionOptions?:Array<{kind:RequestKind}>;
 taskProgress?:TaskProgress[]; relatedTopics?:RelatedTopic[]; supportContact?:SupportContact|null;
 agentProgress?:Array<{step:number;capability:string|null;status:string;requirement_id?:string|null}>;
}
export interface RequestItem {
 id:string; title:string; subtitle:string; status:RequestStatus; kind?:RequestKind; statusText:string;
 statusIcon:string; iconType:string;
}
export interface QuickQuestion {id:string;text:string;icon:string;iconColor:string;borderColor:string;hoverBg:string;}
