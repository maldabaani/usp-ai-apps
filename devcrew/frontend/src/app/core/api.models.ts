/** Types mirroring the backend API (backend/app/api/schemas.py and graph/state.py). */

export type RunStatus =
  | 'pending'
  | 'preparing'
  | 'planning'
  | 'awaiting_plan_approval'
  | 'designing'
  | 'awaiting_design_approval'
  | 'scaffolding'
  | 'executing'
  | 'integrating'
  | 'checking'
  | 'awaiting_final_approval'
  | 'delivering'
  | 'watching_pr'
  | 'paused'
  | 'needs_human'
  | 'completed'
  | 'failed'
  | 'cancelled';

export const TERMINAL_STATUSES: readonly RunStatus[] = ['completed', 'failed', 'cancelled'];

export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'in_review'
  | 'testing'
  | 'merged'
  | 'needs_human'
  | 'failed'
  | 'blocked'
  | 'split'
  | 'cancelled';

export type Stack = 'python' | 'java' | 'angular' | 'mixed';
export type TaskStack = Exclude<Stack, 'mixed'>;

export interface UserStory {
  id: string;
  story: string;
  acceptance_criteria: string[];
}

export interface PlanTask {
  id: string;
  title: string;
  description: string;
  target_files: string[];
  depends_on: string[];
  stack: TaskStack;
  story_ids: string[];
}

export interface Plan {
  summary: string;
  user_stories: UserStory[];
  tasks: PlanTask[];
}

export interface ModuleContract {
  name: string;
  path: string;
  responsibility: string;
  interface: string;
}

export interface PlanAssessment {
  concerns: string[];
  assumptions: string[];
  suggested_changes: string[];
}

export interface ExistingProject {
  stack: TaskStack;
  path: string;
  install_cmd: string;
  build_cmd: string;
  test_cmd: string;
  coverage_cmd: string | null;
}

export interface Design {
  stack: Stack;
  template_id: string;
  components: { template_id: string; path: string }[];
  project_structure: string[];
  modules: ModuleContract[];
  key_decisions: string[];
  design_doc: string;
  plan_assessment?: PlanAssessment | null;
  existing_projects?: ExistingProject[];
}

export interface ReviewIssue {
  file: string;
  line: number | null;
  severity: 'blocker' | 'major' | 'minor' | 'info';
  message: string;
  rule_ref: string | null;
}

export interface ReviewResult {
  decision: 'approve' | 'changes_requested';
  summary: string;
  issues: ReviewIssue[];
}

export interface TestResult {
  ran: boolean;
  passed: boolean;
  failed: string[];
  logs_excerpt: string;
  command: string | null;
}

export interface TaskState {
  id: string;
  status: TaskStatus;
  branch: string | null;
  iterations: number;
  review: ReviewResult | null;
  test_results: TestResult | null;
  feedback: string | null;
  commit: string | null;
  error: string | null;
  worktree: string | null;
  wave: number | null;
  lane: number | null;
  conflict_files: string[];
  conflict_rounds: number;
  coordinator_actions: number;
}

export interface QAEntry {
  id: string;
  task_id: string | null;
  asker: string;
  target: string;
  question: string;
  answer: string;
}

export type ResumeAction = 'approve' | 'reject' | 'edit' | 'answer' | 'update';
export type InterruptKind = 'approval' | 'question' | 'escalation' | 'watch' | 'pause' | 'budget';

export interface PendingInput {
  interrupt_id: string;
  kind: InterruptKind;
  title: string;
  artifact: 'plan' | 'design' | 'final' | 'followup' | null;
  allowed_actions: ResumeAction[];
  data: Record<string, unknown>;
  error: string | null;
}

export interface IntegrationSummary {
  merged: string[];
  failed: string[];
  blocked: string[];
  cancelled?: string[];
  tests: Record<string, TestResult>;
}

export interface RunSummary {
  id: string;
  request: string;
  repo_target: string;
  create_repo: boolean;
  status: RunStatus;
  pr_url: string | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
  busy: boolean;
}

export type RunTarget = 'new' | 'existing';
export type RunMode = 'full' | 'quick';

export interface RepoInfo {
  base_branch: string;
  projects: ExistingProject[];
  source: string;
  notes: string[];
  file_count: number;
  tree: string;
  readme: string;
  commit: string;
}

export interface GateResult {
  name: 'secrets' | 'dependencies' | 'coverage';
  status: 'passed' | 'failed' | 'skipped' | 'error';
  summary: string;
  details: string[];
  allowable: boolean;
}

export interface GateReport {
  results: GateResult[];
  round: number;
}

/** The GitHub issue a run was started from (label or manual import). */
export interface RunIssue {
  repo: string;
  number: number;
  url: string | null;
  title: string | null;
}

/** PR follow-up state: rounds of review comments / CI / conflicts handled after the PR. */
export interface FollowupState {
  round?: number;
  handled?: string[];
  ignored?: string[];
  closed_as?: 'merged' | 'closed' | 'stopped';
}

/** An item of PR activity (review comment, CI failure, conflict) as triaged by the Coordinator. */
export interface FollowupItem {
  kind: 'review' | 'comment' | 'review_body' | 'ci' | 'conflict' | 'ignored';
  key: string;
  user?: string;
  body?: string;
  path?: string | null;
  line?: number | null;
  name?: string;
}

export interface FollowupProposal {
  task: PlanTask;
  reason: string;
  item: FollowupItem;
}

export interface RunDetail extends RunSummary {
  target?: RunTarget;
  mode?: RunMode;
  base_branch?: string | null;
  repo_info?: RepoInfo | null;
  gates?: GateReport | null;
  issue?: RunIssue | null;
  followup?: FollowupState | null;
  pause_requested?: boolean;
  human_notes?: { id: number | null; text: string; merged?: boolean }[];
  plan: Plan | null;
  design: Design | null;
  tasks: Record<string, TaskState>;
  qa_log: QAEntry[];
  integration: IntegrationSummary | null;
  integration_branch: string | null;
  wave: number;
  errors: string[];
  pending: PendingInput[];
}

export interface CreateRunRequest {
  request: string;
  repo_target: string;
  create_repo: boolean;
  target?: RunTarget;
  mode?: RunMode;
  token_budget?: number | null;
  time_budget_min?: number | null;
}

export interface ResumeRequest {
  action: ResumeAction;
  feedback?: string;
  artifact?: Record<string, unknown>;
  answer?: string;
  interrupt_id?: string;
}

export const EVENT_TYPES = [
  'node_started',
  'node_finished',
  'tool_call',
  'tool_result',
  'question',
  'answer',
  'merge',
  'error',
  'status',
  'awaiting_input',
  'message',
  'llm_usage',
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface RunEvent {
  id: number;
  run_id: string;
  type: EventType;
  node: string | null;
  task_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface FileEntry {
  path: string;
  size: number;
}

export interface FileList {
  ref: string | null;
  files: FileEntry[];
}

export interface FileContent {
  ref: string;
  path: string;
  content: string;
  truncated: boolean;
  binary: boolean;
}

export interface DiffResponse {
  task_id: string | null;
  base: string;
  head: string;
  diff: string;
  truncated: boolean;
}

export interface HealthCheck {
  name: string;
  ok: boolean;
  critical: boolean;
  detail: string;
}

export interface HealthReport {
  ok: boolean;
  checks: HealthCheck[];
}

export interface ClientConfig {
  max_request_chars: number;
  max_dev_iterations: number;
  max_parallel_devs: number;
  github_enabled: boolean;
  max_pr_rounds: number;
  run_token_budget: number;
  run_time_budget_min: number;
}

export interface WatchedRepo {
  id: number;
  repo: string;
  enabled: boolean;
  poll_interval_s: number;
  extra_reviewers: string[];
  last_polled_at: string | null;
  last_error: string | null;
}

export interface WatchedRepoCreate {
  repo: string;
  poll_interval_s?: number | null;
  extra_reviewers?: string[];
}

export interface WatchedRepoUpdate {
  enabled?: boolean;
  poll_interval_s?: number;
  extra_reviewers?: string[];
}

export interface IssueRun {
  id: number;
  repo: string;
  issue_number: number;
  trigger: string;
  run_id: string;
  run_status: RunStatus | null;
  comment_id: number | null;
  created_at: string | null;
}

export interface ImportIssueRequest {
  repo: string;
  number: number;
  mode: RunMode;
}

export type WorkflowNodeStatus = 'pending' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped';
export type WorkflowNodeKind = 'input' | 'agent' | 'approval' | 'system' | 'task' | 'output';

export interface WorkflowActivity {
  at: string;
  kind: string;
  text: string;
  ok: boolean | null;
}

export interface WorkflowNode {
  id: string;
  kind: WorkflowNodeKind;
  label: string;
  status: WorkflowNodeStatus;
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
  status: RunStatus;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  attention: string[];
}

/** A chat message from the human to the run (task_id null) or to one task (Phase 13). */
export type MessageStatus = 'pending' | 'delivered' | 'applied' | 'answered' | 'expired' | 'withdrawn';

export interface RunMessage {
  id: number;
  task_id: string | null;
  text: string;
  status: MessageStatus;
  action: 'note' | 'add_task' | 'cancel_task' | 'answer' | null;
  reply: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MessageIn {
  text: string;
  task_id: string | null;
}

export interface PauseState {
  status: RunStatus;
  pause_requested: boolean;
}

/** Tokens and time of a run (GET /runs/{id}/usage). */
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
  budget: { tokens: number; minutes: number } | null;
}
