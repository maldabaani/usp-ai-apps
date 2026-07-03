import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';

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

// codemind.* / api.routers.codemind_* modules log through the exact same
// root logger StoryForge's own modules do (see monitoring/log_capture.py's
// install()), so a single /monitoring/errors fetch already captures both --
// this just recovers the per-app tag the UI shows from the logger name.
function taggedApp(loggerName: string): 'StoryForge' | 'CodeMind' {
  return loggerName.startsWith('codemind') || loggerName.startsWith('api.routers.codemind')
    ? 'CodeMind'
    : 'StoryForge';
}

@Injectable({ providedIn: 'root' })
export class MonitoringService {
  constructor(private http: HttpClient) {}

  getErrors(): Observable<AppErrorRecord[]> {
    return this.http.get<{ errors: ErrorRecord[] }>(`${API_BASE_URL}/monitoring/errors`).pipe(
      map((res) =>
        res.errors
          .map((e) => ({ ...e, app: taggedApp(e.logger) }))
          .sort((a, b) => b.timestamp - a.timestamp)
      )
    );
  }
}
