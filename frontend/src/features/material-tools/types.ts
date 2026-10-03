import type { EvidenceView } from '../../api/contracts';
export type Conversation = {conversation_id:string; material_id:string; knowledge_structure_revision:string;title:string;created_at:string};
export type VoiceTurn = {turn_id:string;question:string;answer:null|{text:string;supported:boolean;citations:{claim_id:string;text:string;evidence:EvidenceView[]}[]};status:string;error_code:string|null;audio_url:string|null};
export type VoiceView = Conversation & {is_current_revision:boolean;source_resolver:string;turns:VoiceTurn[]};
