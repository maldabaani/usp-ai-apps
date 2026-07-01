import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { StoryForgeService } from '../../services/storyforge.service';

@Component({
  selector: 'app-assess',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './assess.component.html',
  styleUrl: './assess.component.css',
})
export class AssessComponent {
  ppmNumber = '';
  ppmName = '';
  systemName = '';
  reviewMode = true;

  selectedFile: File | null = null;
  isDragOver = false;

  submitting = false;
  submitError = '';

  constructor(private storyForgeService: StoryForgeService, private router: Router) {}

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver = false;
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      this.setFile(file);
    }
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) {
      this.setFile(file);
    }
  }

  private setFile(file: File): void {
    if (file.type !== 'application/pdf') {
      this.submitError = 'Only PDF files are accepted.';
      return;
    }
    this.submitError = '';
    this.selectedFile = file;
  }

  get canSubmit(): boolean {
    return (
      !!this.selectedFile &&
      !!this.ppmNumber.trim() &&
      !!this.ppmName.trim() &&
      !!this.systemName.trim() &&
      !this.submitting
    );
  }

  runAssessment(): void {
    if (!this.selectedFile || !this.canSubmit) {
      return;
    }

    this.submitting = true;
    this.submitError = '';

    this.storyForgeService
      .submitAssessment(
        this.selectedFile,
        this.ppmNumber.trim(),
        this.ppmName.trim(),
        this.systemName.trim(),
        this.reviewMode
      )
      .subscribe({
        next: ({ job_id }) => {
          this.router.navigate(['/status', job_id]);
        },
        error: () => {
          this.submitting = false;
          this.submitError = 'Failed to submit the assessment. Please try again.';
        },
      });
  }
}
