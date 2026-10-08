// AUTO-GENERATED from FastAPI OpenAPI. Do not edit by hand.
export type LanguageCode = 'en' | 'ko' | 'vi' | 'zh';
export type RequestKind = 'dining' | 'directions' | 'facilities' | 'front_office' | 'housekeeping' | 'human' | 'tour' | 'transport';
export type RequestStatus = 'pending_staff' | 'approved' | 'in_progress' | 'paused' | 'rejected' | 'completed';
export interface ServicePayload {
  note?: string;
  party_size?: number;
  preferred_time?: string;
  price_acknowledged?: boolean;
  quantity?: number;
  restaurant_name?: string;
  room_number?: string;
}
