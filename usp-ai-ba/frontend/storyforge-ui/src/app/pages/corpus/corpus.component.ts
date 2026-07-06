import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { RouterLink } from '@angular/router';

import { CorpusService, CorpusSource } from '../../services/corpus.service';

@Component({
  selector: 'app-corpus',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './corpus.component.html',
  styleUrl: './corpus.component.css',
})
export class CorpusComponent implements OnInit {
  loading = true;
  loadError = '';
  manuals: CorpusSource[] = [];
  codebase: CorpusSource[] = [];

  constructor(private corpusService: CorpusService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.loadError = '';
    this.corpusService.getSources().subscribe({
      next: (sources) => {
        this.manuals = sources.manuals;
        this.codebase = sources.codebase;
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load corpus data.';
        this.loading = false;
      },
    });
  }

  formatIngestedAt(epochSeconds: number | null): string {
    if (epochSeconds === null) return 'unknown';
    return new Date(epochSeconds * 1000).toLocaleString();
  }
}
