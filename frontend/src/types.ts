export type Source = {
  name: string;
  quote: string;
  page: number | null;
  bbox?: number[] | null;
  independent: boolean;
  document_id: string | null;
  preview_available: boolean;
  version?: number | null;
  manual?: boolean;
};
export type ReviewValues = {
  answer: string;
  unit: string;
  scope: string;
  sources: Source[];
  memo: string;
  reason: string;
};
export type Candidate = Pick<ReviewValues, 'answer' | 'unit' | 'scope' | 'sources'> & {
  id: string;
  notices: string[];
};
export type History = {
  at: string;
  action: string;
  reason: string;
  before: ReviewValues;
  after: ReviewValues;
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
  scope_label: string;
  comparison_label: string;
  draft_sources: string[];
  reference_sources: Source[];
  draft_text: string;
  notices: string[];
  evidence_needed: string[];
  sources: Source[];
  company_answers: { value?: number | null; unit?: string; raw?: string; source?: string }[];
  flags: string[];
  technical_reason: string;
  saved: ReviewValues;
  automatic: ReviewValues;
  review_status: 'pending' | 'complete' | 'missing' | 'again';
  review_label: string;
  method: string;
  history: History[];
  candidates: Candidate[];
};
export type DocumentVersion = {
  version: number;
  name: string;
  pages: number;
  size: number;
  error?: string;
};
export type Document = {
  id: string;
  name: string;
  role: 'evidence' | 'company_answer';
  included: boolean;
  size: number;
  pages: number;
  example: boolean;
  version: number;
  versions?: DocumentVersion[];
  error?: string;
  status: string;
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
  result: {
    answers: Answer[];
    limitations: string[];
    generated_at: string;
    mode: string;
    document_errors?: Record<string, string>;
  } | null;
  notes: Record<string, ReviewNote>;
  stale: boolean;
  affected_questions: string[];
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
export type ListState = { filter: string; search: string; section: string; page: number };
