import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface JobSummary {
  job_id: string;
  ppm_number: string;
  ppm_name: string;
  system_name: string;
  output_mode: string;
  status: string;
  story_count: number;
  task_count: number;
  created_at: number;
}

export interface AffectedComponents {
  frontend: string;
  backend: string;
  middleware: string;
  database: string;
}

export interface ApiContract {
  endpoint: string;
  request: Record<string, unknown>;
  response_success: Record<string, unknown>;
  response_error: Record<string, unknown>;
  status_codes: number[];
}

export interface DevTask {
  title: string;
  user_story: string;
  acceptance_criteria: string[];
  technical_approach: string[];
  affected_components: AffectedComponents;
  api_contract: ApiContract;
  business_rules: string[];
  error_handling: string[];
}

export interface UnitTestTask {
  title: string;
  test_objective: string;
  test_scenarios: {
    happy_path: string[];
    negative: string[];
    edge_cases: string[];
  };
  test_data: {
    valid: Record<string, unknown>;
    invalid: Record<string, unknown>;
  };
  mock_setup: string[];
  assertions: string[];
}

export interface GeneratedStory {
  epic_title: string;
  user_story: string;
  acceptance_criteria: string[];
  dev_tasks: DevTask[];
  unit_test_tasks: UnitTestTask[];
}

export interface AdoTaskResult {
  id: string;
  url: string;
  type: string;
}

export interface AdoResult {
  epic_id: string;
  epic_url: string;
  story_id: string;
  story_url: string;
  tasks: AdoTaskResult[];
}

export interface NotionResult {
  task_title: string;
  page_id: string;
  page_url: string;
}

export interface RagChunk {
  content: string;
  metadata: {
    source: string;
    type: string;
    layer: string;
    module: string;
  };
}

export interface RetrievedContext {
  manuals: RagChunk[];
  codebase: RagChunk[];
  entities: RagChunk[];
}

export interface StoryForgeJobState {
  ppm_number: string;
  ppm_name: string;
  system_name: string;
  job_id: string;
  solution_doc_text: string;
  solution_doc_path: string;
  retrieved_context: RetrievedContext;
  clarification_needed: boolean;
  clarification_questions: string[];
  clarification_answers: Record<string, string>;
  generated_stories: GeneratedStory[];
  review_mode: boolean;
  human_approved: boolean;
  approved_stories: GeneratedStory[];
  output_mode: string;
  ado_results: AdoResult[];
  document_path: string;
  notion_results: NotionResult[];
  errors: string[];
  warnings: string[];
  status: string;
}

export interface IngestFileRecord {
  path: string;
  // Tier 2 (enrichment) reports a successful file as 'summarized', not
  // 'success' -- ingestion.component.ts's displayFiles() normalizes this to
  // 'success' for display (badges/counts/filter), so both raw values must be
  // accepted here.
  status: 'success' | 'summarized' | 'skipped' | 'error';
  reason?: string;
  chunks?: number;
}

export interface IngestResult {
  files_processed?: number;
  files_total?: number;
  chunks_indexed?: number;
  entity_chunks_indexed?: number;
  errors: string[];
  llm_summary_enabled?: boolean;
  files_summarized?: number;
  files_skipped_unchanged?: number;
  // Tier 1's own skip count -- distinct from files_skipped_unchanged above
  // (tier 2's), since a file can be skipped by one tier and not the other.
  chunking_files_skipped_unchanged?: number;
  // Tier 1 (mechanical chunking) per-file outcomes.
  files?: IngestFileRecord[];
  // Tier 2 (LLM-summary enrichment) per-file outcomes -- present for both
  // "code" and "documents" jobs.
  enrichment_files?: IngestFileRecord[];
}

export interface IngestStatus {
  status: string;
  kind: string;
  source_path: string;
  // Which tier is currently running. Only meaningful while status is
  // "running".
  phase: 'chunking' | 'enrichment' | null;
  progress: { done: number; total: number };
  errors: string[];
  result: IngestResult | null;
}

export interface IngestHistoryEntry {
  job_id: string;
  kind: string;
  status: string;
  result: IngestResult | null;
  errors: string[];
  finished_at: number;
  source_path: string;
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class StoryForgeService {
  constructor(private http: HttpClient) {}

  ingestDocuments(
    folderPath: string,
    enableLlmSummary?: boolean,
    maxConcurrency?: number
  ): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(`${API_BASE_URL}/ingest/documents`, {
      folder_path: folderPath,
      enable_llm_summary: enableLlmSummary ?? null,
      max_concurrency: maxConcurrency ?? null,
    });
  }

  ingestCode(
    repoPath: string,
    enableLlmSummary?: boolean,
    maxConcurrency?: number,
    forceFullRechunk?: boolean
  ): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(`${API_BASE_URL}/ingest/code`, {
      repo_path: repoPath,
      enable_llm_summary: enableLlmSummary ?? null,
      max_concurrency: maxConcurrency ?? null,
      force_full_rechunk: forceFullRechunk ?? false,
    });
  }

  getIngestStatus(jobId: string): Observable<IngestStatus> {
    return this.http.get<IngestStatus>(`${API_BASE_URL}/ingest/status/${jobId}`);
  }

  cancelIngestJob(jobId: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/ingest/${jobId}/cancel`, {});
  }

  getIngestHistory(): Observable<IngestHistoryEntry[]> {
    return this.http.get<IngestHistoryEntry[]>(`${API_BASE_URL}/ingest/history`);
  }

  clearIngestHistory(): Observable<void> {
    return this.http.delete<void>(`${API_BASE_URL}/ingest/history`);
  }

  submitAssessment(
    file: File,
    ppmNumber: string,
    ppmName: string,
    systemName: string,
    reviewMode: boolean,
    outputMode: string
  ): Observable<{ job_id: string }> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('ppm_number', ppmNumber);
    formData.append('ppm_name', ppmName);
    formData.append('system_name', systemName);
    formData.append('review_mode', String(reviewMode));
    formData.append('output_mode', outputMode);
    return this.http.post<{ job_id: string }>(`${API_BASE_URL}/assess`, formData);
  }

  rerunAssessment(jobId: string): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(`${API_BASE_URL}/assess/rerun/${jobId}`, {});
  }

  retryAssessment(jobId: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/assess/retry/${jobId}`, {});
  }

  recreateTasks(jobId: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/assess/recreate/${jobId}`, {});
  }

  updateTasks(jobId: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/assess/update/${jobId}`, {});
  }

  cancelAssessment(jobId: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/assess/cancel/${jobId}`, {});
  }

  deleteAssessment(jobId: string): Observable<void> {
    return this.http.delete<void>(`${API_BASE_URL}/assess/${jobId}`);
  }

  listJobs(): Observable<JobSummary[]> {
    return this.http.get<JobSummary[]>(`${API_BASE_URL}/assess/jobs`);
  }

  getAssessmentStatus(jobId: string): Observable<StoryForgeJobState> {
    return this.http.get<StoryForgeJobState>(`${API_BASE_URL}/assess/status/${jobId}`);
  }

  submitClarificationAnswers(
    jobId: string,
    answers: Record<string, string>
  ): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/clarify/answer/${jobId}`, {
      answers,
    });
  }

  approveReview(
    jobId: string,
    approvedStories: GeneratedStory[]
  ): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${API_BASE_URL}/review/approve/${jobId}`, {
      approved_stories: approvedStories,
    });
  }

  getAdoStatus(jobId: string): Observable<{ ado_results: AdoResult[]; errors: string[] }> {
    return this.http.get<{ ado_results: AdoResult[]; errors: string[] }>(
      `${API_BASE_URL}/ado/status/${jobId}`
    );
  }

  getDocumentDownloadUrl(jobId: string): string {
    return `${API_BASE_URL}/export/document/${jobId}`;
  }
}
