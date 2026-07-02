import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { SettingsService } from '../../services/settings.service';
import { StoryForgeService } from '../../services/storyforge.service';

@Component({
  selector: 'app-assess',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './assess.component.html',
  styleUrl: './assess.component.css',
})
export class AssessComponent implements OnInit {
  ppmNumber = '';
  ppmName = '';
  systemName = '';
  reviewMode = true;
  outputMode = 'document';

  selectedFile: File | null = null;
  isDragOver = false;

  submitting = false;
  submitError = '';

  constructor(
    private storyForgeService: StoryForgeService,
    private settingsService: SettingsService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.settingsService.getSettings().subscribe({
      next: (s) => (this.outputMode = s.output_mode),
      error: () => {
        // Keep the 'document' fallback -- settings just couldn't be loaded.
      },
    });
  }

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
        this.reviewMode,
        this.outputMode
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
