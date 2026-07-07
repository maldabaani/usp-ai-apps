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
        if (!state.clarification_needed) {
          // The job has already moved past this step (e.g. a stale reload or
          // browser-back after answers were already submitted successfully).
          // Showing the form again would only lead to a guaranteed 409 on
          // submit, since the backend has nothing left to apply the answers
          // to -- go straight to the status page instead.
          this.router.navigate(['/status', this.jobId]);
          return;
        }
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
      error: (err) => {
        if (err?.status === 409) {
          // The job already moved past clarification (e.g. this request was
          // a duplicate of one that already succeeded) -- nothing is wrong,
          // there's just nothing left to submit here.
          this.router.navigate(['/status', this.jobId]);
          return;
        }
        this.submitting = false;
        this.submitError = 'Failed to submit answers. Please try again.';
      },
    });
  }
}
