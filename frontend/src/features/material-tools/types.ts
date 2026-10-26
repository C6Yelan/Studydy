import type { EvidenceView, PodcastContext } from '../../api/contracts';
export type Conversation = {conversation_id:string; material_id:string; knowledge_structure_revision:string;title:string;created_at:string};
export type VoiceTurn = {context?:PodcastContext|null;mode:'text'|'voice';turn_id:string;question:string;answer:null|{schema:'material-answer/v2'|'material-answer/v3';paragraphs?:{text:string;citation_ids:string[]}[];text:string;supported:boolean;question_interpretation?:{original:string;used:string};citations:{passage_id:string;text:string;evidence:EvidenceView[]}[]};status:string;error_code:string|null;audio_url:string|null};
export type VoiceView = Conversation & {is_current_revision:boolean;source_resolver:string;turns:VoiceTurn[]};
