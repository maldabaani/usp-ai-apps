import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';
import { streamSse, SseStreamHandlers } from './sse.util';

export interface ExtractionJob {
  jobId: string;
  phase: string;
  repositoryRoot: string;
  outputDirectory: string;
  executionMode: string;
  incremental: boolean;
  totalFiles: number;
  processedFiles: number;
  succeededFiles: number;
  failedFiles: number;
  skippedFiles: number;
  failureReason: string | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface OutputFile {
  relativePath: string;
  sizeBytes: number;
  modifiedAt: string;
}

export interface FailedFile {
  relativePath: string;
  errorMessage: string;
  durationMillis: number;
}

export interface StartJobRequest {
  repositoryPath: string;
  outputDirectory?: string;
  maxConcurrency?: number;
  executionMode?: string;
}

export interface ExtractionResult {
  relativePath: string;
  agentName: string;
  success: boolean;
  skipped: boolean;
  content: string | null;
  errorMessage: string | null;
  durationMillis: number;
  promptTokens: number | null;
  completionTokens: number | null;
}

export interface QaAnswer {
  answer: string;
  sourceFiles: string[];
}

export type QaStreamHandlers = SseStreamHandlers;

const API_BASE_URL = `${environment.apiBaseUrl}/v1`;

@Injectable({ providedIn: 'root' })
export class CodeMindService {
  constructor(
    private http: HttpClient,
    private authService: AuthService
  ) {}

  startJob(request: StartJobRequest): Observable<ExtractionJob> {
    return this.http.post<ExtractionJob>(`${API_BASE_URL}/extraction-jobs`, request);
  }

  listJobs(): Observable<ExtractionJob[]> {
    return this.http.get<ExtractionJob[]>(`${API_BASE_URL}/extraction-jobs`);
  }

  getJob(jobId: string): Observable<ExtractionJob> {
    return this.http.get<ExtractionJob>(`${API_BASE_URL}/extraction-jobs/${jobId}`);
  }

  cancelJob(jobId: string): Observable<void> {
    return this.http.post<void>(`${API_BASE_URL}/extraction-jobs/${jobId}/cancel`, {});
  }

  deleteJob(jobId: string): Observable<void> {
    return this.http.delete<void>(`${API_BASE_URL}/extraction-jobs/${jobId}`);
  }

  clearAllJobs(): Observable<void> {
    return this.http.delete<void>(`${API_BASE_URL}/extraction-jobs`);
  }

  listOutputFiles(jobId: string): Observable<OutputFile[]> {
    return this.http.get<OutputFile[]>(`${API_BASE_URL}/extraction-jobs/${jobId}/output-files`);
  }

  readOutputFile(jobId: string, relativePath: string): Observable<ExtractionResult> {
    return this.http.get<ExtractionResult>(`${API_BASE_URL}/extraction-jobs/${jobId}/output-file`, {
      params: { relativePath },
    });
  }

  listFailedFiles(jobId: string): Observable<FailedFile[]> {
    return this.http.get<FailedFile[]>(`${API_BASE_URL}/extraction-jobs/${jobId}/failed-files`);
  }

  getExportUrl(jobId: string): string {
    return `${API_BASE_URL}/extraction-jobs/${jobId}/export`;
  }

  ask(jobId: string, question: string): Observable<QaAnswer> {
    return this.http.post<QaAnswer>(`${API_BASE_URL}/extraction-jobs/${jobId}/qa`, { question });
  }

  askStream(
    jobId: string,
    question: string,
    mode: 'deep' | 'comprehensive',
    handlers: QaStreamHandlers
  ): Promise<void> {
    return streamSse(
      `${API_BASE_URL}/extraction-jobs/${jobId}/qa/stream`,
      { question, mode },
      handlers,
      this.authService.getToken()
    );
  }

  askAllStream(question: string, handlers: QaStreamHandlers): Promise<void> {
    // No generic/stats mode for cross-job Ask All -- always the deep LLM path.
    return streamSse(`${API_BASE_URL}/ask/stream`, { question }, handlers, this.authService.getToken());
  }
}
