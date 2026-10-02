import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { RouterLink } from '@angular/router';

import { DevCrewService, Lesson } from '../../../services/devcrew.service';
import { extractErrorMessage } from '../../../services/http-error.util';

type StatusFilter = 'pending' | 'approved' | 'rejected' | 'all';

const SOURCE_LABELS: Record<string, string> = {
  coordinator_escalation: 'Coordinator escalation',
  review_cycle: 'Review cycle',
  pr_comment: 'PR comment',
};

@Component({
  selector: 'app-devcrew-lessons',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './lessons.component.html',
  styleUrl: './lessons.component.css',
})
export class LessonsComponent implements OnInit {
  readonly filters: StatusFilter[] = ['pending', 'approved', 'rejected', 'all'];

  lessons: Lesson[] = [];
  loading = true;
  loadError = '';
  filter: StatusFilter = 'pending';
  decidingId: number | null = null;
  decideError = '';
  expandedId: number | null = null;

  constructor(private devCrewService: DevCrewService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.loadError = '';
    this.devCrewService.listLessons(this.filter === 'all' ? undefined : this.filter).subscribe({
      next: (lessons) => {
        this.lessons = lessons;
        this.loading = false;
      },
      error: (err) => {
        this.loadError = extractErrorMessage(err, 'Unable to load lessons.');
        this.loading = false;
      },
    });
  }

  setFilter(filter: StatusFilter): void {
    if (this.filter === filter) return;
    this.filter = filter;
    this.load();
  }

  toggleEvidence(lesson: Lesson): void {
    this.expandedId = this.expandedId === lesson.id ? null : lesson.id;
  }

  approve(lesson: Lesson): void {
    this.decidingId = lesson.id;
    this.decideError = '';
    this.devCrewService.approveLesson(lesson.id).subscribe({
      next: () => {
        this.decidingId = null;
        this.load();
      },
      error: (err) => {
        this.decidingId = null;
        this.decideError = extractErrorMessage(err, 'Failed to approve the lesson.');
      },
    });
  }

  reject(lesson: Lesson): void {
    if (!confirm("Reject this lesson? It will not be added to the stack's rules file.")) return;
    this.decidingId = lesson.id;
    this.decideError = '';
    this.devCrewService.rejectLesson(lesson.id).subscribe({
      next: () => {
        this.decidingId = null;
        this.load();
      },
      error: (err) => {
        this.decidingId = null;
        this.decideError = extractErrorMessage(err, 'Failed to reject the lesson.');
      },
    });
  }

  sourceLabel(source: string): string {
    return SOURCE_LABELS[source] ?? source;
  }
}
