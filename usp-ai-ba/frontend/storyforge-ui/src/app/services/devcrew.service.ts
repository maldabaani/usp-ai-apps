import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

const API_BASE_URL = `${environment.apiBaseUrl}/devcrew`;

export interface RunSummary {
  id: string;
  request: string;
  repo_target: string;
  create_repo: boolean;
  status: string;
  pr_url: string | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
  busy: boolean;
}

export interface CodeSearchStatus {
  status: string;
  detail: string;
}

export interface PlanTaskOut {
  id: string;
  title: string;
  description: string;
  target_files: string[];
  depends_on: string[];
  stack: string;
  story_ids: string[];
}

export interface PlanOut {
  summary: string;
  user_stories: { id: string; story: string; acceptance_criteria: string[] }[];
  tasks: PlanTaskOut[];
}

export interface DesignOut {
  design_doc: string;
  modules: { name: string; path: string }[];
  key_decisions: string[];
}

export interface TaskStateOut {
  id: string;
  status: string;
  branch: string | null;
  iterations: number;
  feedback: string | null;
  commit: string | null;
  error: string | null;
  wave: number | null;
  lane: number | null;
}

export interface RunDetail extends RunSummary {
  target: string;
  mode: string;
  base_branch: string | null;
  repo_info: Record<string, unknown> | null;
  gates: Record<string, unknown> | null;
  issue: Record<string, unknown> | null;
  followup: Record<string, unknown> | null;
  pause_requested: boolean;
  request_digest: string | null;
  models: Record<string, string> | null;
  code_search: CodeSearchStatus | null;
  human_notes: Record<string, unknown>[];
  plan: PlanOut | null;
  design: DesignOut | null;
  tasks: Record<string, TaskStateOut>;
  qa_log: Record<string, unknown>[];
  integration: Record<string, unknown> | null;
  integration_branch: string | null;
  wave: number;
  errors: string[];
  pending: PendingInput[];
}

export interface PendingInput {
  interrupt_id: string;
  kind: string;
  title: string;
  artifact: string | null;
  allowed_actions: string[];
  data: Record<string, unknown>;
  error: string | null;
}

export type NodeKind = 'input' | 'agent' | 'approval' | 'system' | 'task' | 'output';
export type NodeStatus = 'pending' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped';

export interface WorkflowActivity {
  at: string;
  kind: string;
  text: string;
  ok: boolean | null;
}

export interface WorkflowNode {
  id: string;
  kind: NodeKind;
  label: string;
  status: NodeStatus;
  detail: string | null;
  step: string | null;
  task_id: string | null;
  stack: string | null;
  started_at: string | null;
  finished_at: string | null;
  runs: number;
  counters: Record<string, number>;
  pending_interrupt_ids: string[];
  activity: WorkflowActivity[];
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  kind: 'flow' | 'loop';
  active: boolean;
  label: string | null;
}

export interface Workflow {
  run_id: string;
  status: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  attention: string[];
}

export interface MessageOut {
  id: number;
  task_id: string | null;
  text: string;
  status: string;
  action: string | null;
  reply: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface UsageLine {
  key: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  model_seconds: number;
}

export interface RunUsage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  model_seconds: number;
  elapsed_s: number;
  waiting_s: number;
  active_s: number;
  by_role: UsageLine[];
  by_task: UsageLine[];
  budget: Record<string, number> | null;
}

@Injectable({ providedIn: 'root' })
export class DevCrewService {
  constructor(private http: HttpClient) {}

  listRuns(): Observable<RunSummary[]> {
    return this.http.get<RunSummary[]>(`${API_BASE_URL}/runs`);
  }

  getRun(runId: string): Observable<RunDetail> {
    return this.http.get<RunDetail>(`${API_BASE_URL}/runs/${runId}`);
  }

  getWorkflow(runId: string): Observable<Workflow> {
    return this.http.get<Workflow>(`${API_BASE_URL}/runs/${runId}/workflow`);
  }

  resumeRun(
    runId: string,
    action: 'approve' | 'reject' | 'edit' | 'answer' | 'update',
    extra: Record<string, unknown> = {}
  ): Observable<PendingInput> {
    return this.http.post<PendingInput>(`${API_BASE_URL}/runs/${runId}/resume`, {
      action,
      ...extra,
    });
  }

  retryRun(runId: string): Observable<RunSummary> {
    return this.http.post<RunSummary>(`${API_BASE_URL}/runs/${runId}/retry`, {});
  }

  cancelRun(runId: string): Observable<RunSummary> {
    return this.http.post<RunSummary>(`${API_BASE_URL}/runs/${runId}/cancel`, {});
  }

  listMessages(runId: string): Observable<MessageOut[]> {
    return this.http.get<MessageOut[]>(`${API_BASE_URL}/runs/${runId}/messages`);
  }

  postMessage(runId: string, text: string, taskId?: string): Observable<MessageOut> {
    return this.http.post<MessageOut>(`${API_BASE_URL}/runs/${runId}/messages`, {
      text,
      task_id: taskId ?? null,
    });
  }

  getUsage(runId: string): Observable<RunUsage> {
    return this.http.get<RunUsage>(`${API_BASE_URL}/runs/${runId}/usage`);
  }
}
