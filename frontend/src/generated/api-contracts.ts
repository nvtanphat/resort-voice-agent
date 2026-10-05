// AUTO-GENERATED from FastAPI OpenAPI. Do not edit by hand.
export type LanguageCode = 'zh' | 'en' | 'ko' | 'vi';
export type RequestKind = 'housekeeping' | 'front_office' | 'facilities' | 'directions' | 'tour' | 'transport' | 'human' | 'dining';
export type RequestStatus = 'pending_staff' | 'approved' | 'in_progress' | 'paused' | 'rejected' | 'completed';
export interface ServicePayload {
  room_number?: string;
  quantity?: number;
  preferred_time?: string;
  party_size?: number;
  note?: string;
  price_acknowledged?: boolean;
}
