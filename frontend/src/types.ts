export type Source = {
  name: string;
  quote: string;
  page: number | null;
  bbox?: number[] | null;
  independent: boolean;
  document_id: string | null;
  preview_available: boolean;
};
export type Answer = {
  id: string;
  question: string;
  section: string;
  status: string;
  status_label: string;
  original_status: string;
  needs_attention: boolean;
  why: string;
  next_step: string;
  value_text: string;
  period_label: string;
  draft_text: string;
  notices: string[];
  evidence_needed: string[];
  sources: Source[];
  company_answers: { value?: number | null; unit?: string; raw?: string; source?: string }[];
  flags: string[];
  technical_reason: string;
};
export type Document = {
  id: string;
  name: string;
  role: 'evidence' | 'company_answer';
  size: number;
  pages: number;
  example: boolean;
};
export type ReviewNote = { answer: string; text: string; revision: number; updated_at: string };
export type Project = {
  id: string;
  company_name: string;
  year: number;
  industry: string;
  framework: string;
  mode: 'live' | 'example';
  documents: Document[];
  input_revision: number;
  result_revision: number | null;
  result: { answers: Answer[]; limitations: string[]; generated_at: string; mode: string } | null;
  notes: Record<string, ReviewNote>;
  stale: boolean;
  updated_at: string;
  job: {
    status: 'idle' | 'queued' | 'running' | 'complete' | 'failed';
    stage: string;
    error: string;
  };
};
export type ProjectSummary = Pick<Project, 'id' | 'company_name' | 'year' | 'mode' | 'updated_at'>;
export type Company = Pick<Project, 'company_name' | 'year' | 'industry' | 'framework'>;
export type Config = {
  analysis_available: boolean;
  max_file_mb: number;
  max_project_mb: number;
  frameworks: { key: string; label: string; description: string }[];
};
