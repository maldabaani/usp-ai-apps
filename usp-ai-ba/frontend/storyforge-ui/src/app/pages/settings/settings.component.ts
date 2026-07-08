import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AskPrompts, PromptsService } from '../../services/prompts.service';
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
  ollamaNumCtx = 32768;
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

  // Anthropic (Claude) + ingestion's optional Ollama LLM-summary tier
  anthropicModel = '';
  anthropicApiKeyMasked = '';
  anthropicApiKeyInput = '';
  ingestOllamaEnabled = false;
  ingestOllamaModel = '';
  askQaModel = 'ollama';
  llmRequestTimeoutSeconds = 300;
  restartRequiredFields: string[] = [];

  // Ask Technical/Business prompt customization
  askPromptsLoading = true;
  technicalPromptTemplate = '';
  technicalPromptDefault = '';
  technicalPromptHasCustom = false;
  technicalPromptSaving = false;
  technicalPromptError = '';
  businessPromptTemplate = '';
  businessPromptDefault = '';
  businessPromptHasCustom = false;
  businessPromptSaving = false;
  businessPromptError = '';

  constructor(
    private settingsService: SettingsService,
    private promptsService: PromptsService
  ) {}

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

    this.promptsService.getAskPrompts().subscribe({
      next: (prompts) => {
        this.applyAskPrompts(prompts);
        this.askPromptsLoading = false;
      },
      error: () => {
        this.askPromptsLoading = false;
      },
    });
  }

  private applyAskPrompts(prompts: AskPrompts): void {
    this.technicalPromptTemplate = prompts.technical.effective;
    this.technicalPromptDefault = prompts.technical.default;
    this.technicalPromptHasCustom = prompts.technical.custom !== null;
    this.businessPromptTemplate = prompts.business.effective;
    this.businessPromptDefault = prompts.business.default;
    this.businessPromptHasCustom = prompts.business.custom !== null;
  }

  saveAskPrompt(kind: 'technical' | 'business'): void {
    const template = kind === 'technical' ? this.technicalPromptTemplate : this.businessPromptTemplate;
    if (kind === 'technical') {
      this.technicalPromptSaving = true;
      this.technicalPromptError = '';
    } else {
      this.businessPromptSaving = true;
      this.businessPromptError = '';
    }

    this.promptsService.updateAskPrompt(kind, template).subscribe({
      next: (info) => {
        if (kind === 'technical') {
          this.technicalPromptTemplate = info.effective;
          this.technicalPromptHasCustom = info.custom !== null;
          this.technicalPromptSaving = false;
        } else {
          this.businessPromptTemplate = info.effective;
          this.businessPromptHasCustom = info.custom !== null;
          this.businessPromptSaving = false;
        }
      },
      error: (err) => {
        const message = err?.error?.detail || 'Failed to save prompt.';
        if (kind === 'technical') {
          this.technicalPromptSaving = false;
          this.technicalPromptError = message;
        } else {
          this.businessPromptSaving = false;
          this.businessPromptError = message;
        }
      },
    });
  }

  resetAskPrompt(kind: 'technical' | 'business'): void {
    if (kind === 'technical') {
      this.technicalPromptSaving = true;
      this.technicalPromptError = '';
    } else {
      this.businessPromptSaving = true;
      this.businessPromptError = '';
    }

    this.promptsService.updateAskPrompt(kind, null).subscribe({
      next: (info) => {
        if (kind === 'technical') {
          this.technicalPromptTemplate = info.effective;
          this.technicalPromptHasCustom = false;
          this.technicalPromptSaving = false;
        } else {
          this.businessPromptTemplate = info.effective;
          this.businessPromptHasCustom = false;
          this.businessPromptSaving = false;
        }
      },
      error: (err) => {
        const message = err?.error?.detail || 'Failed to reset prompt.';
        if (kind === 'technical') {
          this.technicalPromptSaving = false;
          this.technicalPromptError = message;
        } else {
          this.businessPromptSaving = false;
          this.businessPromptError = message;
        }
      },
    });
  }

  private applySettings(s: AppSettings): void {
    this.ollamaBaseUrl = s.ollama_base_url;
    this.ollamaLlmModel = s.ollama_llm_model;
    this.ollamaEmbedModel = s.ollama_embed_model;
    this.ollamaNumCtx = s.ollama_num_ctx;
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

    this.anthropicModel = s.anthropic_model;
    this.anthropicApiKeyMasked = s.anthropic_api_key_masked;
    this.anthropicApiKeyInput = s.anthropic_api_key_masked;
    this.ingestOllamaEnabled = s.ingest_ollama_enabled;
    this.ingestOllamaModel = s.ingest_ollama_model;
    this.askQaModel = s.ask_qa_model;
    this.llmRequestTimeoutSeconds = s.llm_request_timeout_seconds;
    this.restartRequiredFields = s.restart_required_fields;
  }

  isRestartRequired(field: string): boolean {
    return this.restartRequiredFields.includes(field);
  }

  save(): void {
    this.saving = true;
    this.saveError = '';
    this.saved = false;

    const update: AppSettingsUpdate = {
      ollama_base_url: this.ollamaBaseUrl,
      ollama_llm_model: this.ollamaLlmModel,
      ollama_embed_model: this.ollamaEmbedModel,
      ollama_num_ctx: this.ollamaNumCtx,
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
      anthropic_model: this.anthropicModel,
      ingest_ollama_enabled: this.ingestOllamaEnabled,
      ingest_ollama_model: this.ingestOllamaModel,
      ask_qa_model: this.askQaModel,
      llm_request_timeout_seconds: this.llmRequestTimeoutSeconds,
    };
    // Leaving a key input untouched (still showing the mask) means "keep
    // the current secret" -- only send it if the user actually typed something new.
    if (this.notionApiKeyInput !== this.notionApiKeyMasked) {
      update.notion_api_key = this.notionApiKeyInput;
    }
    if (this.anthropicApiKeyInput !== this.anthropicApiKeyMasked) {
      update.anthropic_api_key = this.anthropicApiKeyInput;
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
