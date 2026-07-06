import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { AskService, AskStatus } from '../../services/ask.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  pending?: boolean;
  sources?: string[];
}

@Component({
  selector: 'app-ask-business',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './ask-business.component.html',
  styleUrl: './ask-business.component.css',
})
export class AskBusinessComponent implements OnInit {
  question = '';
  asking = false;
  messages: ChatMessage[] = [];
  status: AskStatus | null = null;
  statusLoading = true;

  @ViewChild('chatLog') chatLogRef?: ElementRef<HTMLDivElement>;

  constructor(private askService: AskService) {}

  ngOnInit(): void {
    this.askService.getStatus().subscribe({
      next: (status) => {
        this.status = status;
        this.statusLoading = false;
      },
      error: () => {
        this.statusLoading = false;
      },
    });
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

    await this.askService.askBusiness(question, {
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
