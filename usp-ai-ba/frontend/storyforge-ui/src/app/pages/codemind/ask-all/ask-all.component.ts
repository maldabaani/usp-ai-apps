import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { CodeMindService, ExtractionJob } from '../../../services/codemind.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  pending?: boolean;
  sources?: string[];
}

@Component({
  selector: 'app-codemind-ask-all',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './ask-all.component.html',
  styleUrl: './ask-all.component.css',
})
export class AskAllComponent implements OnInit {
  completedJobs: ExtractionJob[] = [];
  loading = true;
  question = '';
  asking = false;
  messages: ChatMessage[] = [];

  @ViewChild('chatLog') chatLogRef?: ElementRef<HTMLDivElement>;

  constructor(private codeMindService: CodeMindService) {}

  ngOnInit(): void {
    this.codeMindService.listJobs().subscribe({
      next: (jobs) => {
        this.completedJobs = jobs.filter((j) => j.phase === 'COMPLETED');
        this.loading = false;
      },
      error: () => (this.loading = false),
    });
  }

  shortId(jobId: string): string {
    return jobId.slice(0, 8);
  }

  async ask(): Promise<void> {
    const question = this.question.trim();
    if (!question || this.asking) {
      return;
    }

    this.messages.push({ role: 'user', text: question });
    this.question = '';
    this.asking = true;

    const assistantMessage: ChatMessage = { role: 'assistant', text: 'Thinking…', pending: true };
    this.messages.push(assistantMessage);
    this.scrollToBottom();

    let fullText = '';

    await this.codeMindService.askAllStream(question, {
      onSources: (sources) => (assistantMessage.sources = sources),
      onChunk: (chunk) => {
        if (assistantMessage.pending) {
          assistantMessage.pending = false;
        }
        fullText += chunk;
        assistantMessage.text = fullText;
        this.scrollToBottom();
      },
      onError: (message) => {
        assistantMessage.pending = false;
        assistantMessage.text = message;
      },
      onComplete: () => {
        assistantMessage.pending = false;
        if (!fullText) {
          assistantMessage.text = '(No response)';
        }
      },
    });

    this.asking = false;
  }

  private scrollToBottom(): void {
    setTimeout(() => {
      const el = this.chatLogRef?.nativeElement;
      if (el) {
        el.scrollTop = el.scrollHeight;
      }
    });
  }
}
