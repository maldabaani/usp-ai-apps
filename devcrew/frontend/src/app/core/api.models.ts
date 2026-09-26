/** Types mirroring the backend API (backend/app/api/schemas.py and graph/state.py). */

export type RunStatus =
  | 'pending'
  | 'planning'
  | 'awaiting_plan_approval'
  | 'designing'
  | 'awaiting_design_approval'
  | 'scaffolding'
  | 'executing'
  | 'integrating'
  | 'awaiting_final_approval'
  | 'delivering'
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
  | 'split';

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

export interface Design {
  stack: Stack;
  template_id: string;
  components: { template_id: string; path: string }[];
  project_structure: string[];
  modules: ModuleContract[];
  key_decisions: string[];
  design_doc: string;
  plan_assessment?: PlanAssessment | null;
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

export type ResumeAction = 'approve' | 'reject' | 'edit' | 'answer';
export type InterruptKind = 'approval' | 'question' | 'escalation';

export interface PendingInput {
  interrupt_id: string;
  kind: InterruptKind;
  title: string;
  artifact: 'plan' | 'design' | 'final' | null;
  allowed_actions: ResumeAction[];
  data: Record<string, unknown>;
  error: string | null;
}

export interface IntegrationSummary {
  merged: string[];
  failed: string[];
  blocked: string[];
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

export interface RunDetail extends RunSummary {
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
