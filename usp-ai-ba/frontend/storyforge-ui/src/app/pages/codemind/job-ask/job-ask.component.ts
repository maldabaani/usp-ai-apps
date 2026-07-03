import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { CodeMindService } from '../../../services/codemind.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  pending?: boolean;
  sources?: string[];
}

@Component({
  selector: 'app-codemind-job-ask',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './job-ask.component.html',
  styleUrl: './job-ask.component.css',
})
export class JobAskComponent implements OnInit {
  jobId = '';
  question = '';
  asking = false;
  messages: ChatMessage[] = [];

  @ViewChild('chatLog') chatLogRef?: ElementRef<HTMLDivElement>;

  constructor(
    private route: ActivatedRoute,
    private codeMindService: CodeMindService
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.paramMap.get('jobId') ?? '';
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

    await this.codeMindService.askStream(this.jobId, question, {
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
