import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface CorpusSource {
  source: string;
  chunk_count: number;
  has_llm_summary: boolean;
  format: string | null;
  ingested_at: number | null;
}

export interface CorpusSources {
  manuals: CorpusSource[];
  codebase: CorpusSource[];
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class CorpusService {
  constructor(private http: HttpClient) {}

  getSources(): Observable<CorpusSources> {
    return this.http.get<CorpusSources>(`${API_BASE_URL}/corpus/sources`);
  }
}
