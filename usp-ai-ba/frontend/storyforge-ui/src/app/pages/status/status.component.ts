import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import {
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
  { key: 'clarifying', label: 'Checking Clarifications', activeDesc: 'Identifying ambiguities before generating stories…' },
  { key: 'generating', label: 'Generating Stories',       activeDesc: 'Claude is writing user stories and tasks…' },
  { key: 'reviewing',  label: 'Awaiting Review',          activeDesc: 'Waiting for your approval of the generated stories…' },
  { key: 'creating',   label: 'Creating Tasks',           activeDesc: 'Pushing approved tasks to your workspace…' },
  { key: 'done',       label: 'Complete',                 activeDesc: '' },
];

const STATUS_ORDER = STEP_DEFS.map(s => s.key);

export type StepState = 'pending' | 'active' | 'done' | 'error';

@Component({
  selector: 'app-status',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './status.component.html',
  styleUrl: './status.component.css',
})
export class StatusComponent implements OnInit, OnDestroy {
  jobId = '';
  state: StoryForgeJobState | null = null;
  loadError = '';
  storiesText = '';
  copyButtonLabel = COPY_LABEL;

  readonly stepDefs = STEP_DEFS;
  readonly ragSections: { key: keyof RetrievedContext; label: string }[] = [
    { key: 'manuals',  label: 'User Manuals' },
    { key: 'codebase', label: 'Codebase' },
    { key: 'entities', label: 'JPA Entities' },
  ];

  showRagContext = false;
  expandedChunks: Record<string, boolean> = {};

  private lastActiveStep = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private redirected = false;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private storyForgeService: StoryForgeService,
    private changeDetectorRef: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.paramMap.get('jobId') ?? '';
    this.poll();
    this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
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

        if (state.status !== 'error') {
          this.lastActiveStep = state.status;
        }

        if (state.status === 'clarifying') {
          this.redirectOnce(['/clarify', this.jobId]);
        } else if (state.status === 'reviewing' && state.review_mode) {
          this.redirectOnce(['/review', this.jobId]);
        } else if (state.status === 'done' || state.status === 'error') {
          if (state.status === 'done') {
            this.storiesText = this.formatStories(state.approved_stories);
          }
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

    if (currentStatus === 'error') {
      const errorIdx = STATUS_ORDER.indexOf(this.lastActiveStep);
      const stepIdx = STATUS_ORDER.indexOf(key);
      if (stepIdx < errorIdx) return 'done';
      if (stepIdx === errorIdx) return 'error';
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
    return this.storyForgeService.getDocumentDownloadUrl(this.jobId);
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
