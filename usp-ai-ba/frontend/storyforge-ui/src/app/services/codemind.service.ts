import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

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

export interface QaStreamHandlers {
  onSources: (sources: string[]) => void;
  onChunk: (chunk: string) => void;
  onError: (message: string) => void;
  onComplete: () => void;
}

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
    return this.streamSse(`${API_BASE_URL}/extraction-jobs/${jobId}/qa/stream`, { question, mode }, handlers);
  }

  askAllStream(question: string, handlers: QaStreamHandlers): Promise<void> {
    // No generic/stats mode for cross-job Ask All -- always the deep LLM path.
    return this.streamSse(`${API_BASE_URL}/ask/stream`, { question }, handlers);
  }

  // Raw fetch() rather than HttpClient: reading a streamed response body
  // chunk by chunk needs the Fetch Response's ReadableStream, which
  // HttpClient doesn't expose directly. The Authorization header is
  // attached manually here (HttpClient requests get it for free from
  // auth.interceptor.ts) since this bypasses HttpClient entirely.
  private async streamSse(
    url: string,
    body: Record<string, unknown>,
    handlers: QaStreamHandlers
  ): Promise<void> {
    const token = this.authService.getToken();
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      });

      if (!response.ok || !response.body) {
        handlers.onError(`Error: ${response.statusText}`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary: number;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          let eventName = 'message';
          const dataLines: string[] = [];
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              const rest = line.slice(5);
              dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
            }
          }
          const data = dataLines.join('\n');

          if (eventName === 'sources') {
            try {
              handlers.onSources(JSON.parse(data));
            } catch {
              // malformed sources frame -- ignore, chunks still stream
            }
          } else if (eventName === 'chunk') {
            try {
              handlers.onChunk(JSON.parse(data));
            } catch {
              handlers.onChunk(data);
            }
          }
        }
      }
      handlers.onComplete();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      handlers.onError(`Network error: ${message}`);
    }
  }
}
