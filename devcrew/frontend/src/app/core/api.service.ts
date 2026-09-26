import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  ClientConfig,
  CreateRunRequest,
  DiffResponse,
  FileContent,
  FileList,
  HealthReport,
  PendingInput,
  ResumeRequest,
  RunDetail,
  RunSummary,
  Workflow,
} from './api.models';

/** Typed client for the DevCrew backend. The base URL comes from the environment files. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  readonly baseUrl = environment.apiUrl.replace(/\/$/, '');

  listRuns(): Observable<RunSummary[]> {
    return this.http.get<RunSummary[]>(`${this.baseUrl}/runs`);
  }

  getRun(runId: string): Observable<RunDetail> {
    return this.http.get<RunDetail>(`${this.baseUrl}/runs/${encodeURIComponent(runId)}`);
  }

  createRun(body: CreateRunRequest): Observable<RunSummary> {
    return this.http.post<RunSummary>(`${this.baseUrl}/runs`, body);
  }

  resume(runId: string, body: ResumeRequest): Observable<PendingInput> {
    return this.http.post<PendingInput>(
      `${this.baseUrl}/runs/${encodeURIComponent(runId)}/resume`,
      body,
    );
  }

  cancel(runId: string): Observable<RunSummary> {
    return this.http.post<RunSummary>(`${this.baseUrl}/runs/${encodeURIComponent(runId)}/cancel`, {});
  }

  listFiles(runId: string, ref?: string): Observable<FileList> {
    const params = ref ? new HttpParams().set('ref', ref) : undefined;
    return this.http.get<FileList>(`${this.baseUrl}/runs/${encodeURIComponent(runId)}/files`, {
      params,
    });
  }

  readFile(runId: string, path: string, ref?: string): Observable<FileContent> {
    const params = ref ? new HttpParams().set('ref', ref) : undefined;
    const encoded = path.split('/').map(encodeURIComponent).join('/');
    return this.http.get<FileContent>(
      `${this.baseUrl}/runs/${encodeURIComponent(runId)}/files/${encoded}`,
      { params },
    );
  }

  diff(runId: string, taskId?: string): Observable<DiffResponse> {
    const params = taskId ? new HttpParams().set('task_id', taskId) : undefined;
    return this.http.get<DiffResponse>(`${this.baseUrl}/runs/${encodeURIComponent(runId)}/diff`, {
      params,
    });
  }

  workflow(runId: string): Observable<Workflow> {
    return this.http.get<Workflow>(`${this.baseUrl}/runs/${encodeURIComponent(runId)}/workflow`);
  }

  config(): Observable<ClientConfig> {
    return this.http.get<ClientConfig>(`${this.baseUrl}/config`);
  }

  health(): Observable<HealthReport> {
    return this.http.get<HealthReport>(`${this.baseUrl}/health`);
  }

  eventsUrl(runId: string, lastEventId: number | null): string {
    const base = `${this.baseUrl}/runs/${encodeURIComponent(runId)}/events`;
    return lastEventId === null ? base : `${base}?last_event_id=${lastEventId}`;
  }
}
