import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';
import { streamSse, SseStreamHandlers } from './sse.util';

export type AskStreamHandlers = SseStreamHandlers;

export interface AskStatus {
  counts: { manuals: number; codebase: number; entities: number };
  has_content: boolean;
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class AskService {
  constructor(
    private http: HttpClient,
    private authService: AuthService
  ) {}

  getStatus(): Observable<AskStatus> {
    return this.http.get<AskStatus>(`${API_BASE_URL}/ask/status`);
  }

  askTechnical(
    question: string,
    handlers: AskStreamHandlers,
    conversationId?: string
  ): Promise<void> {
    return streamSse(
      `${API_BASE_URL}/ask/technical`,
      { question, conversation_id: conversationId ?? null },
      handlers,
      this.authService.getToken()
    );
  }

  askBusiness(
    question: string,
    handlers: AskStreamHandlers,
    conversationId?: string
  ): Promise<void> {
    return streamSse(
      `${API_BASE_URL}/ask/business`,
      { question, conversation_id: conversationId ?? null },
      handlers,
      this.authService.getToken()
    );
  }
}
