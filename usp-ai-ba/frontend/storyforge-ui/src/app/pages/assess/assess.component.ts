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

  inputMode: 'file' | 'text' = 'file';
  selectedFile: File | null = null;
  sddText = '';
  isDragOver = false;

  submitting = false;
  submitError = '';

  private static readonly ALLOWED_EXTENSIONS = ['.pdf', '.docx'];

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
    // Filename extension, not file.type -- MIME-type reporting for .docx is
    // inconsistent across browsers/OSes, while the extension is reliable and
    // matches the backend's own check (api/routers/assess.py's
    // _ALLOWED_SDD_EXTENSIONS).
    const name = file.name.toLowerCase();
    const isAllowed = AssessComponent.ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
    if (!isAllowed) {
      this.submitError = 'Only .pdf and .docx files are accepted.';
      return;
    }
    this.submitError = '';
    this.selectedFile = file;
  }

  get canSubmit(): boolean {
    const hasInput = this.inputMode === 'file' ? !!this.selectedFile : !!this.sddText.trim();
    return (
      hasInput &&
      !!this.ppmNumber.trim() &&
      !!this.ppmName.trim() &&
      !!this.systemName.trim() &&
      !this.submitting
    );
  }

  runAssessment(): void {
    if (!this.canSubmit) {
      return;
    }

    this.submitting = true;
    this.submitError = '';

    this.storyForgeService
      .submitAssessment(
        this.inputMode === 'file' ? this.selectedFile : null,
        this.ppmNumber.trim(),
        this.ppmName.trim(),
        this.systemName.trim(),
        this.reviewMode,
        this.outputMode,
        this.inputMode === 'text' ? this.sddText.trim() : undefined
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
