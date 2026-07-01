import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import {
  AffectedComponents,
  ApiContract,
  GeneratedStory,
  StoryForgeService,
  UnitTestTask,
} from '../../services/storyforge.service';

@Component({
  selector: 'app-review',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './review.component.html',
  styleUrl: './review.component.css',
})
export class ReviewComponent implements OnInit {
  jobId = '';
  stories: GeneratedStory[] = [];
  expandedStory: number | null = 0;
  expandedDevTasks: Record<string, boolean> = {};

  loading = true;
  submitting = false;
  loadError = '';
  submitError = '';

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private storyForgeService: StoryForgeService
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.paramMap.get('jobId') ?? '';
    this.loadStories();
  }

  loadStories(): void {
    this.loading = true;
    this.storyForgeService.getAssessmentStatus(this.jobId).subscribe({
      next: (state) => {
        this.stories = structuredClone(state.generated_stories ?? []);
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load generated stories.';
        this.loading = false;
      },
    });
  }

  toggleStory(index: number): void {
    this.expandedStory = this.expandedStory === index ? null : index;
  }

  toggleDevTask(si: number, ti: number): void {
    const key = `${si}_${ti}`;
    this.expandedDevTasks[key] = !this.expandedDevTasks[key];
  }

  isDevTaskExpanded(si: number, ti: number): boolean {
    return !!this.expandedDevTasks[`${si}_${ti}`];
  }

  addCriterion(list: string[]): void { list.push(''); }
  removeCriterion(list: string[], i: number): void { list.splice(i, 1); }

  addItem(list: string[]): void { list.push(''); }
  removeItem(list: string[], i: number): void { list.splice(i, 1); }

  addDevTask(story: GeneratedStory): void {
    story.dev_tasks.push({
      title: 'New Dev Task',
      user_story: '',
      acceptance_criteria: [],
      technical_approach: [],
      affected_components: {} as AffectedComponents,
      api_contract: {} as ApiContract,
      business_rules: [],
      error_handling: [],
    });
  }

  removeDevTask(story: GeneratedStory, i: number): void {
    story.dev_tasks.splice(i, 1);
  }

  addUnitTest(story: GeneratedStory): void {
    story.unit_test_tasks.push({
      title: 'New Unit Test',
      test_objective: '',
      test_scenarios: { happy_path: [], negative: [], edge_cases: [] },
      test_data: { valid: {}, invalid: {} },
      mock_setup: [],
      assertions: [],
    } as UnitTestTask);
  }

  removeUnitTest(story: GeneratedStory, i: number): void {
    story.unit_test_tasks.splice(i, 1);
  }

  removeStory(i: number): void {
    this.stories.splice(i, 1);
    if (this.expandedStory === i) {
      this.expandedStory = null;
    } else if (this.expandedStory !== null && this.expandedStory > i) {
      this.expandedStory--;
    }
  }

  approve(): void {
    this.submitting = true;
    this.submitError = '';
    this.storyForgeService.approveReview(this.jobId, this.stories).subscribe({
      next: () => this.router.navigate(['/status', this.jobId]),
      error: () => {
        this.submitting = false;
        this.submitError = 'Failed to submit. Please try again.';
      },
    });
  }
}
