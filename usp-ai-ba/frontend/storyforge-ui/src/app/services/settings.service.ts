import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface AppSettings {
  ollama_base_url: string;
  ollama_llm_model: string;
  ollama_embed_model: string;
  ollama_num_ctx: number;
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
  anthropic_api_key_masked: string;
  anthropic_model: string;
  ingest_ollama_enabled: boolean;
  ingest_ollama_model: string;
  ask_qa_model: string;
  restart_required_fields: string[];
}

export type AppSettingsUpdate = Partial<
  Omit<AppSettings, 'notion_api_key_masked' | 'anthropic_api_key_masked' | 'restart_required_fields'>
> & {
  notion_api_key?: string;
  anthropic_api_key?: string;
};

const API_BASE_URL = environment.apiBaseUrl;

@Injectable({ providedIn: 'root' })
export class SettingsService {
  constructor(private http: HttpClient) {}

  getSettings(): Observable<AppSettings> {
    return this.http.get<AppSettings>(`${API_BASE_URL}/settings`);
  }

  updateSettings(update: AppSettingsUpdate): Observable<AppSettings> {
    return this.http.put<AppSettings>(`${API_BASE_URL}/settings`, update);
  }
}
