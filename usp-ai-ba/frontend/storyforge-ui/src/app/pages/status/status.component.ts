import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { AuthService } from '../../services/auth.service';
import { extractErrorMessage } from '../../services/http-error.util';
import { isValidRepoTarget, normalizeRepoTarget } from '../../services/repo-target.util';
import {
  DevCrewDispatch,
  DevTask,
  GeneratedStory,
  RagChunk,
  RetrievedContext,
  StoryForgeJobState,
  StoryForgeService,
  UnitTestTask,
} from '../../services/storyforge.service';

const POLL_INTERVAL_MS = 3000;
const COPY_LABEL = 'Copy User Stories';

interface StepDef {
  key: string;
  label: string;
  activeDesc: string;
}

const STEP_DEFS: StepDef[] = [
  { key: 'analyzing',  label: 'Analyzing Document',      activeDesc: 'Reading and parsing your solution design document…' },
  { key: 'detecting_ambiguities', label: 'Detecting Ambiguities', activeDesc: 'Using the LLM to check for ambiguities before generating stories — this can take several minutes for large documents…' },
  { key: 'clarifying', label: 'Checking Clarifications', activeDesc: 'Identifying ambiguities before generating stories…' },
  { key: 'generating', label: 'Generating Stories',       activeDesc: 'Our AI LLM is writing user stories and tasks…' },
  { key: 'reviewing',  label: 'Awaiting Review',          activeDesc: 'Waiting for your approval of the generated stories…' },
  { key: 'creating',   label: 'Creating Tasks',           activeDesc: 'Pushing approved tasks to your workspace…' },
  { key: 'done',       label: 'Complete',                 activeDesc: '' },
];

const STATUS_ORDER = STEP_DEFS.map(s => s.key);
const TERMINAL_STATUSES = ['done', 'error', 'cancelled'];

export type StepState = 'pending' | 'active' | 'done' | 'error';

@Component({
  selector: 'app-status',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './status.component.html',
  styleUrl: './status.component.css',
})
export class StatusComponent implements OnInit, OnDestroy {
  jobId = '';
  state: StoryForgeJobState | null = null;
  loadError = '';
  storiesText = '';
  copyButtonLabel = COPY_LABEL;
  retrying = false;
  retryError = '';
  recreating = false;
  recreateError = '';
  updating = false;
  updateError = '';
  cancelling = false;
  cancelError = '';
  sendingToDevCrew: number | null = null;
  devCrewError = '';
  devCrewDispatches: Record<number, string> = {}; // epic index -> DevCrew run id
  // Index of the epic row whose inline "send to DevCrew" input is open, or
  // null if none is open. Only one row's form is open at a time.
  openDevCrewFormFor: number | null = null;
  devCrewRepoInput = '';
  // "Test Connection" state, scoped to the single open form.
  testingDevCrewRepo = false;
  devCrewTestStatus: 'idle' | 'success' | 'failed' = 'idle';
  devCrewTestedValue: string | null = null; // normalized value that passed

  readonly stepDefs = STEP_DEFS;
  readonly ragSections: { key: keyof RetrievedContext; label: string }[] = [
    { key: 'manuals',  label: 'User Manuals' },
    { key: 'codebase', label: 'Codebase' },
    { key: 'entities', label: 'JPA Entities' },
  ];

  showRagContext = false;
  expandedChunks: Record<string, boolean> = {};

  showUsage = false;
  private readonly usageNodeLabels: Record<string, string> = {
    clarify_node: 'Clarification',
    generate_node: 'Story Generation',
  };

  private lastActiveStep = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private redirected = false;
  // Set right after retry()/recreateTasks() triggers a background action.
  // The action runs in a FastAPI BackgroundTask scheduled *after* the POST
  // response, so the very next poll can still see the job's pre-action
  // status (e.g. still "done" right after clicking recreate) -- treating
  // that stale value as "finished" would stop polling before the action
  // even started. Consumed (cleared) after one poll cycle.
  private pendingAction = false;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private storyForgeService: StoryForgeService,
    private authService: AuthService,
    private changeDetectorRef: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.paramMap.get('jobId') ?? '';
    this.poll();
    this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
    this.loadDevCrewDispatches();
  }

  // Reflects what's already been dispatched, per the server's own dispatch
  // registry -- devCrewDispatches previously only ever got populated by a
  // successful dispatch made in the current browser session, so reloading
  // this page reset it to empty and silently allowed re-dispatching the
  // same epic to a brand-new DevCrew run with no warning.
  private loadDevCrewDispatches(): void {
    if (!this.jobId) return;
    this.storyForgeService.getDevCrewDispatches(this.jobId).subscribe({
      next: (dispatches: DevCrewDispatch[]) => {
        // Newest first (per the backend); keep only each epic's latest.
        for (const d of dispatches) {
          if (!(d.epic_index in this.devCrewDispatches)) {
            this.devCrewDispatches[d.epic_index] = d.run_id;
          }
        }
      },
      error: () => {}, // non-critical: the send form still works, just without the reload-safe guard
    });
  }

  ngOnDestroy(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
    }
  }

  private poll(): void {
    if (this.redirected) return;

    this.storyForgeService.getAssessmentStatus(this.jobId).subscribe({
      next: (state) => {
        this.state = state;

        if (state.status !== 'error' && state.status !== 'cancelled') {
          this.lastActiveStep = state.status;
        }

        if (state.status === 'clarifying') {
          this.redirectOnce(['/clarify', this.jobId]);
        } else if (state.status === 'reviewing' && state.review_mode) {
          this.redirectOnce(['/review', this.jobId]);
        } else if (TERMINAL_STATUSES.includes(state.status)) {
          if (this.pendingAction) {
            // Might just be the stale pre-action status -- give the
            // background task one more poll cycle to actually start before
            // trusting a terminal status this soon after triggering it.
            this.pendingAction = false;
            return;
          }
          if (state.status === 'done') {
            this.storiesText = this.formatStories(state.approved_stories);
          }
          this.retrying = false;
          this.recreating = false;
          this.updating = false;
          this.cancelling = false;
          this.stopPolling();
        }
      },
      error: () => {
        this.loadError = 'Unable to load job status.';
        this.stopPolling();
      },
    });
  }

  stepState(key: string): StepState {
    if (!this.state) return 'pending';
    const currentStatus = this.state.status;

    if (currentStatus === 'done') return 'done';

    if (currentStatus === 'error' || currentStatus === 'cancelled') {
      const refIdx = STATUS_ORDER.indexOf(this.lastActiveStep);
      const stepIdx = STATUS_ORDER.indexOf(key);
      if (stepIdx < refIdx) return 'done';
      // Only "error" gets the X icon on its step -- a cancelled job just
      // leaves the step it was on as a plain pending circle, relying on the
      // "Cancelled" banner (not this icon) to communicate what happened.
      if (stepIdx === refIdx && currentStatus === 'error') return 'error';
      return 'pending';
    }

    const currentIdx = STATUS_ORDER.indexOf(currentStatus);
    const stepIdx = STATUS_ORDER.indexOf(key);
    if (stepIdx < currentIdx) return 'done';
    if (stepIdx === currentIdx) return 'active';
    return 'pending';
  }

  lineComplete(key: string): boolean {
    return this.stepState(key) === 'done';
  }

  retry(): void {
    if (!this.jobId || this.retrying) return;
    this.retrying = true;
    this.retryError = '';

    this.storyForgeService.retryAssessment(this.jobId).subscribe({
      next: () => {
        // retrying stays true -- poll() clears it once it observes the job
        // actually reach done/error (see pendingAction above).
        this.pendingAction = true;
        this.redirected = false;
        this.poll();
        if (!this.pollHandle) {
          this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
        }
      },
      error: (err) => {
        this.retrying = false;
        this.retryError = extractErrorMessage(err, 'Retry failed. Submit a new assessment instead.');
      },
    });
  }

  get canRecreate(): boolean {
    if (!this.state || this.state.status !== 'done') return false;
    return this.state.output_mode === 'notion' || this.state.output_mode === 'ado';
  }

  recreateTasks(): void {
    if (!this.jobId || this.recreating || !this.state) return;
    const target = this.state.output_mode === 'notion' ? 'Notion' : 'ADO';
    if (!confirm(`Re-create tasks in ${target}? This will archive/replace what was already created.`)) {
      return;
    }

    this.recreating = true;
    this.recreateError = '';

    this.storyForgeService.recreateTasks(this.jobId).subscribe({
      next: () => {
        // recreating stays true -- poll() clears it once it observes the
        // job actually reach done/error (see pendingAction above).
        this.pendingAction = true;
        this.redirected = false;
        this.poll();
        if (!this.pollHandle) {
          this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
        }
      },
      error: (err) => {
        this.recreating = false;
        this.recreateError = extractErrorMessage(err, 'Re-create failed.');
      },
    });
  }

  get canUpdate(): boolean {
    if (!this.state || this.state.status !== 'done') return false;
    return this.state.output_mode === 'notion';
  }

  updateTasks(): void {
    if (!this.jobId || this.updating || !this.state) return;
    if (!confirm('Update tasks in Notion? This will overwrite the existing pages\' content in place.')) {
      return;
    }

    this.updating = true;
    this.updateError = '';

    this.storyForgeService.updateTasks(this.jobId).subscribe({
      next: () => {
        // updating stays true -- poll() clears it once it observes the
        // job actually reach done/error (see pendingAction above).
        this.pendingAction = true;
        this.redirected = false;
        this.poll();
        if (!this.pollHandle) {
          this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
        }
      },
      error: (err) => {
        this.updating = false;
        this.updateError = extractErrorMessage(err, 'Update failed.');
      },
    });
  }

  get canCancel(): boolean {
    if (!this.state) return false;
    return !TERMINAL_STATUSES.includes(this.state.status);
  }

  cancel(): void {
    if (!this.jobId || this.cancelling || !this.state) return;
    if (!confirm('Stop this assessment? This cannot be resumed -- you would need to start a new one.')) {
      return;
    }

    this.cancelling = true;
    this.cancelError = '';

    this.storyForgeService.cancelAssessment(this.jobId).subscribe({
      next: () => {
        // cancelling stays true -- poll() clears it once it observes the
        // job actually reach a terminal status (see pendingAction above).
        this.pendingAction = true;
        this.redirected = false;
        this.poll();
        if (!this.pollHandle) {
          this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
        }
      },
      error: (err) => {
        this.cancelling = false;
        this.cancelError = extractErrorMessage(err, 'Cancel failed.');
      },
    });
  }

  get devCrewCanSend(): boolean {
    return (
      this.devCrewTestStatus === 'success' &&
      normalizeRepoTarget(this.devCrewRepoInput) === this.devCrewTestedValue
    );
  }

  openDevCrewForm(epicIndex: number): void {
    if (this.sendingToDevCrew !== null) return;
    this.openDevCrewFormFor = epicIndex;
    this.devCrewRepoInput = '';
    this.devCrewError = '';
    this.testingDevCrewRepo = false;
    this.devCrewTestStatus = 'idle';
    this.devCrewTestedValue = null;
  }

  cancelDevCrewForm(): void {
    this.openDevCrewFormFor = null;
    this.devCrewRepoInput = '';
    this.devCrewError = '';
    this.testingDevCrewRepo = false;
    this.devCrewTestStatus = 'idle';
    this.devCrewTestedValue = null;
  }

  testDevCrewConnection(): void {
    const normalized = normalizeRepoTarget(this.devCrewRepoInput);
    if (!normalized || !isValidRepoTarget(normalized)) {
      this.devCrewError = 'Enter a GitHub repo as owner/repo (a pasted GitHub URL is fine too).';
      return;
    }
    this.testingDevCrewRepo = true;
    this.devCrewError = '';
    this.storyForgeService.checkDevCrewRepo(normalized).subscribe({
      next: () => {
        this.testingDevCrewRepo = false;
        this.devCrewTestStatus = 'success';
        this.devCrewTestedValue = normalized;
      },
      error: (err) => {
        this.testingDevCrewRepo = false;
        this.devCrewTestStatus = 'failed';
        this.devCrewError = extractErrorMessage(err, 'Could not verify this repo.');
      },
    });
  }

  submitDevCrewForm(epicIndex: number): void {
    if (!this.jobId || this.sendingToDevCrew !== null) return;

    const normalized = normalizeRepoTarget(this.devCrewRepoInput);
    if (!normalized || !isValidRepoTarget(normalized)) {
      this.devCrewError = 'Enter a GitHub repo as owner/repo (a pasted GitHub URL is fine too).';
      return;
    }
    if (!this.devCrewCanSend) {
      this.devCrewError = 'Test the connection to this repo before sending.';
      return;
    }

    this.openDevCrewFormFor = null;
    this.sendingToDevCrew = epicIndex;
    this.devCrewError = '';

    this.storyForgeService.sendEpicToDevCrew(this.jobId, epicIndex, normalized).subscribe({
      next: (run) => {
        this.sendingToDevCrew = null;
        this.devCrewDispatches[epicIndex] = run.id;
      },
      error: (err) => {
        this.sendingToDevCrew = null;
        this.devCrewError = extractErrorMessage(err, 'Failed to send this epic to DevCrew.');
      },
    });
  }

  private redirectOnce(commands: (string | number)[]): void {
    if (this.redirected) return;
    this.redirected = true;
    this.stopPolling();
    this.router.navigate(commands);
  }

  private stopPolling(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  get ragContext(): RetrievedContext | null {
    return this.state?.retrieved_context ?? null;
  }

  get totalChunks(): number {
    if (!this.ragContext) return 0;
    return (this.ragContext.manuals?.length ?? 0) +
           (this.ragContext.codebase?.length ?? 0) +
           (this.ragContext.entities?.length ?? 0);
  }

  getChunks(key: keyof RetrievedContext): RagChunk[] {
    return this.ragContext?.[key] ?? [];
  }

  toggleRagContext(): void {
    this.showRagContext = !this.showRagContext;
  }

  toggleUsage(): void {
    this.showUsage = !this.showUsage;
  }

  get usageByNode(): { label: string; calls: number; input_tokens: number; output_tokens: number; total_tokens: number; models: string[] }[] {
    const byNode = this.state?.usage?.by_node ?? {};
    return Object.entries(byNode).map(([key, node]) => ({
      label: this.usageNodeLabels[key] ?? key,
      ...node,
    }));
  }

  toggleChunk(chunkKey: string): void {
    this.expandedChunks[chunkKey] = !this.expandedChunks[chunkKey];
  }

  shortSource(source: string | undefined): string {
    if (!source) return 'unknown';
    return source.split('/').pop()?.split('\\').pop() ?? source;
  }

  previewContent(content: string): string {
    return content?.length > 300 ? content.slice(0, 300) + '…' : (content ?? '');
  }

  get documentDownloadUrl(): string {
    // A plain <a href> download isn't routed through HttpClient, so the
    // auth interceptor never sees it -- the token has to ride along as a
    // query param instead.
    const token = this.authService.getToken();
    const url = this.storyForgeService.getDocumentDownloadUrl(this.jobId);
    return token ? `${url}?token=${encodeURIComponent(token)}` : url;
  }

  copyToClipboard(): void {
    navigator.clipboard.writeText(this.storiesText).then(() => {
      this.copyButtonLabel = 'Copied!';
      this.changeDetectorRef.detectChanges();
      setTimeout(() => {
        this.copyButtonLabel = COPY_LABEL;
        this.changeDetectorRef.detectChanges();
      }, 2000);
    });
  }

  private formatStories(stories: GeneratedStory[]): string {
    if (!stories || !stories.length) return '(no stories)';
    return stories.map(s => this.formatStory(s)).join('\n\n');
  }

  private formatStory(story: GeneratedStory): string {
    const lines: string[] = [];
    lines.push(`=== ${story.epic_title} ===`, '');
    lines.push('User Story:', story.user_story, '');
    lines.push('Acceptance Criteria:', this.formatList(story.acceptance_criteria), '');
    for (const task of story.dev_tasks ?? []) lines.push(this.formatDevTask(task), '');
    for (const test of story.unit_test_tasks ?? []) lines.push(this.formatUnitTestTask(test), '');
    return lines.join('\n');
  }

  private formatDevTask(task: DevTask): string {
    const lines: string[] = [];
    lines.push(`--- Dev Task: ${task.title} ---`, '');
    lines.push('User Story:', task.user_story, '');
    lines.push('Acceptance Criteria:', this.formatList(task.acceptance_criteria), '');
    lines.push('Technical Approach:', this.formatList(task.technical_approach), '');
    lines.push('Affected Components:', this.formatDict(task.affected_components), '');
    lines.push('API Contract:', this.formatDict(task.api_contract), '');
    lines.push('Business Rules:', this.formatList(task.business_rules), '');
    lines.push('Error Handling:', this.formatList(task.error_handling));
    return lines.join('\n');
  }

  private formatUnitTestTask(test: UnitTestTask): string {
    const lines: string[] = [];
    lines.push(`--- Unit Test Task: ${test.title} ---`, '');
    lines.push('Test Objective:', test.test_objective, '');
    lines.push('Test Scenarios:');
    for (const [category, items] of Object.entries(test.test_scenarios ?? {})) {
      lines.push(`  ${category}:`, this.formatList(items as string[], '    '));
    }
    lines.push('', 'Test Data:', this.formatDict(test.test_data), '');
    lines.push('Mock Setup:', this.formatList(test.mock_setup), '');
    lines.push('Assertions:', this.formatList(test.assertions));
    return lines.join('\n');
  }

  private formatList(items: string[] | undefined, indent = '  '): string {
    if (!items || !items.length) return `${indent}(none)`;
    return items.map(item => `${indent}- ${item}`).join('\n');
  }

  private formatDict(value: object | undefined, indent = '  '): string {
    if (!value || !Object.keys(value).length) return `${indent}(none)`;
    return Object.entries(value)
      .map(([k, v]) => `${indent}${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join('\n');
  }
}
