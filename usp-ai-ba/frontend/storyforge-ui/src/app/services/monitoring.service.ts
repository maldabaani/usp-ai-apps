import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { forkJoin, map, Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { environment } from '../../environments/environment';

export interface ErrorRecord {
  timestamp: number;
  logger: string;
  level: string;
  message: string;
  traceback: string | null;
}

export interface AppErrorRecord extends ErrorRecord {
  app: 'StoryForge' | 'CodeMind';
}

const API_BASE_URL = environment.apiBaseUrl;
const CODEMIND_API_BASE_URL = `${environment.codemindUrl}/api/v1`;

@Injectable({ providedIn: 'root' })
export class MonitoringService {
  constructor(private http: HttpClient) {}

  // Each origin is fetched independently and a failure on one (e.g. CodeMind
  // not running) doesn't blank out the other's errors -- merged and sorted
  // newest-first by timestamp so the page reads as a single combined feed.
  getErrors(): Observable<AppErrorRecord[]> {
    const storyForge = this.http.get<{ errors: ErrorRecord[] }>(`${API_BASE_URL}/monitoring/errors`).pipe(
      map((res) => res.errors.map((e) => ({ ...e, app: 'StoryForge' as const }))),
      catchError(() => of([] as AppErrorRecord[]))
    );
    const codeMind = this.http.get<{ errors: ErrorRecord[] }>(`${CODEMIND_API_BASE_URL}/monitoring/errors`).pipe(
      map((res) => res.errors.map((e) => ({ ...e, app: 'CodeMind' as const }))),
      catchError(() => of([] as AppErrorRecord[]))
    );

    return forkJoin([storyForge, codeMind]).pipe(
      map(([sfErrors, cmErrors]) => [...sfErrors, ...cmErrors].sort((a, b) => b.timestamp - a.timestamp))
    );
  }
}
