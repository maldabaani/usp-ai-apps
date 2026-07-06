import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface AskPromptInfo {
  custom: string | null;
  default: string;
  effective: string;
}

export interface AskPrompts {
  technical: AskPromptInfo;
  business: AskPromptInfo;
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class PromptsService {
  constructor(private http: HttpClient) {}

  getAskPrompts(): Observable<AskPrompts> {
    return this.http.get<AskPrompts>(`${API_BASE_URL}/prompts/ask`);
  }

  updateAskPrompt(kind: 'technical' | 'business', template: string | null): Observable<AskPromptInfo> {
    return this.http.put<AskPromptInfo>(`${API_BASE_URL}/prompts/ask/${kind}`, { template });
  }
}
