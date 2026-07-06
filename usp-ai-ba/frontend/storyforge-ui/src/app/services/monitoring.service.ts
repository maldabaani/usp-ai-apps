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

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class MonitoringService {
  constructor(private http: HttpClient) {}

  getErrors(): Observable<ErrorRecord[]> {
    return this.http
      .get<{ errors: ErrorRecord[] }>(`${API_BASE_URL}/monitoring/errors`)
      .pipe(map((res) => res.errors.sort((a, b) => b.timestamp - a.timestamp)));
  }
}
