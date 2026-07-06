import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface ConversationSummary {
  id: string;
  kind: 'technical' | 'business';
  title: string;
  created_at: number;
  updated_at: number;
}

export interface ConversationMessage {
  role: 'user' | 'assistant';
  text: string;
  sources: string[];
  created_at: number;
}

export interface Conversation extends ConversationSummary {
  owner: string;
  messages: ConversationMessage[];
}

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class ConversationsService {
  constructor(private http: HttpClient) {}

  list(): Observable<ConversationSummary[]> {
    return this.http.get<ConversationSummary[]>(`${API_BASE_URL}/conversations`);
  }

  get(conversationId: string): Observable<Conversation> {
    return this.http.get<Conversation>(`${API_BASE_URL}/conversations/${conversationId}`);
  }

  delete(conversationId: string): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(`${API_BASE_URL}/conversations/${conversationId}`);
  }
}
