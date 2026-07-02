import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface AppSettings {
  ollama_base_url: string;
  ollama_llm_model: string;
  ollama_embed_model: string;
  prompt_variant: string;
  output_mode: string;
  ado_organization: string;
  ado_project: string;
  mcp_server_path: string;
  notion_database_id: string;
  notion_parent_page_id: string;
  notion_title_property: string;
  notion_status_property: string;
  notion_status_value: string;
  notion_api_key_masked: string;
}

export type AppSettingsUpdate = Partial<Omit<AppSettings, 'notion_api_key_masked'>> & {
  notion_api_key?: string;
};

export interface CodeMindSettings {
  anthropicModel: string;
  anthropicApiKeyMasked: string;
  executionMode: string;
  qaModel: string;
  ollamaEnabled: boolean;
  ollamaBaseUrl: string;
  ollamaModel: string;
  restartRequiredFields: string[];
}

export type CodeMindSettingsUpdate = Partial<Omit<CodeMindSettings, 'anthropicApiKeyMasked' | 'restartRequiredFields'>> & {
  anthropicApiKey?: string;
};

const API_BASE_URL = environment.apiBaseUrl;
const CODEMIND_API_BASE_URL = `${environment.codemindUrl}/api/v1`;

@Injectable({ providedIn: 'root' })
export class SettingsService {
  constructor(private http: HttpClient) {}

  getSettings(): Observable<AppSettings> {
    return this.http.get<AppSettings>(`${API_BASE_URL}/settings`);
  }

  updateSettings(update: AppSettingsUpdate): Observable<AppSettings> {
    return this.http.put<AppSettings>(`${API_BASE_URL}/settings`, update);
  }

  getCodeMindSettings(): Observable<CodeMindSettings> {
    return this.http.get<CodeMindSettings>(`${CODEMIND_API_BASE_URL}/settings`);
  }

  updateCodeMindSettings(update: CodeMindSettingsUpdate): Observable<CodeMindSettings> {
    return this.http.put<CodeMindSettings>(`${CODEMIND_API_BASE_URL}/settings`, update);
  }
}
