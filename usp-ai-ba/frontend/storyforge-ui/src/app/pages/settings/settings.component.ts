import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AppSettings, AppSettingsUpdate, SettingsService } from '../../services/settings.service';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.css',
})
export class SettingsComponent implements OnInit {
  loading = true;
  loadError = '';
  saving = false;
  saveError = '';
  saved = false;

  // LLM
  ollamaBaseUrl = '';
  ollamaLlmModel = '';
  ollamaEmbedModel = '';
  promptVariant = 'production';

  // Task management
  outputMode = 'document';
  adoOrganization = '';
  adoProject = '';
  mcpServerPath = '';
  notionDatabaseId = '';
  notionParentPageId = '';
  notionTitleProperty = '';
  notionStatusProperty = '';
  notionStatusValue = '';
  notionApiKeyMasked = '';
  notionApiKeyInput = '';

  constructor(private settingsService: SettingsService) {}

  ngOnInit(): void {
    this.settingsService.getSettings().subscribe({
      next: (s) => {
        this.applySettings(s);
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load settings.';
        this.loading = false;
      },
    });
  }

  private applySettings(s: AppSettings): void {
    this.ollamaBaseUrl = s.ollama_base_url;
    this.ollamaLlmModel = s.ollama_llm_model;
    this.ollamaEmbedModel = s.ollama_embed_model;
    this.promptVariant = s.prompt_variant;

    this.outputMode = s.output_mode;
    this.adoOrganization = s.ado_organization;
    this.adoProject = s.ado_project;
    this.mcpServerPath = s.mcp_server_path;
    this.notionDatabaseId = s.notion_database_id;
    this.notionParentPageId = s.notion_parent_page_id;
    this.notionTitleProperty = s.notion_title_property;
    this.notionStatusProperty = s.notion_status_property;
    this.notionStatusValue = s.notion_status_value;
    this.notionApiKeyMasked = s.notion_api_key_masked;
    this.notionApiKeyInput = s.notion_api_key_masked;
  }

  save(): void {
    this.saving = true;
    this.saveError = '';
    this.saved = false;

    const update: AppSettingsUpdate = {
      ollama_base_url: this.ollamaBaseUrl,
      ollama_llm_model: this.ollamaLlmModel,
      ollama_embed_model: this.ollamaEmbedModel,
      prompt_variant: this.promptVariant,
      output_mode: this.outputMode,
      ado_organization: this.adoOrganization,
      ado_project: this.adoProject,
      mcp_server_path: this.mcpServerPath,
      notion_database_id: this.notionDatabaseId,
      notion_parent_page_id: this.notionParentPageId,
      notion_title_property: this.notionTitleProperty,
      notion_status_property: this.notionStatusProperty,
      notion_status_value: this.notionStatusValue,
    };
    // Leaving the key input untouched (still showing the mask) means "keep
    // the current secret" -- only send it if the user actually typed something new.
    if (this.notionApiKeyInput !== this.notionApiKeyMasked) {
      update.notion_api_key = this.notionApiKeyInput;
    }

    this.settingsService.updateSettings(update).subscribe({
      next: (s) => {
        this.applySettings(s);
        this.saving = false;
        this.saved = true;
      },
      error: (err) => {
        this.saving = false;
        this.saveError = err?.error?.detail || 'Failed to save settings.';
      },
    });
  }
}
