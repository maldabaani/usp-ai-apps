import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface WatchTarget {
  id: string;
  path: string;
  kind: 'documents' | 'code';
  enabled: boolean;
  created_at: number;
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class WatchService {
  constructor(private http: HttpClient) {}

  listTargets(): Observable<WatchTarget[]> {
    return this.http.get<WatchTarget[]>(`${API_BASE_URL}/watch/targets`);
  }

  addTarget(path: string, kind: 'documents' | 'code'): Observable<WatchTarget> {
    return this.http.post<WatchTarget>(`${API_BASE_URL}/watch/targets`, { path, kind });
  }

  setEnabled(targetId: string, enabled: boolean): Observable<WatchTarget> {
    return this.http.patch<WatchTarget>(`${API_BASE_URL}/watch/targets/${targetId}`, { enabled });
  }

  deleteTarget(targetId: string): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(`${API_BASE_URL}/watch/targets/${targetId}`);
  }
}
