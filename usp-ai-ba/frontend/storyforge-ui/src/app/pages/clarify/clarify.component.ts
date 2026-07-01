import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { StoryForgeService } from '../../services/storyforge.service';

@Component({
  selector: 'app-clarify',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './clarify.component.html',
  styleUrl: './clarify.component.css',
})
export class ClarifyComponent implements OnInit {
  jobId = '';
  questions: string[] = [];
  answers: Record<string, string> = {};

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
    this.loadQuestions();
  }

  loadQuestions(): void {
    this.loading = true;
    this.storyForgeService.getAssessmentStatus(this.jobId).subscribe({
      next: (state) => {
        this.questions = state.clarification_questions;
        for (const question of this.questions) {
          this.answers[question] = this.answers[question] ?? '';
        }
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load clarification questions.';
        this.loading = false;
      },
    });
  }

  get canSubmit(): boolean {
    return (
      !this.submitting &&
      this.questions.length > 0 &&
      this.questions.every((question) => !!this.answers[question]?.trim())
    );
  }

  submitAnswers(): void {
    if (!this.canSubmit) {
      return;
    }

    this.submitting = true;
    this.submitError = '';

    this.storyForgeService.submitClarificationAnswers(this.jobId, this.answers).subscribe({
      next: () => {
        this.router.navigate(['/status', this.jobId]);
      },
      error: () => {
        this.submitting = false;
        this.submitError = 'Failed to submit answers. Please try again.';
      },
    });
  }
}
