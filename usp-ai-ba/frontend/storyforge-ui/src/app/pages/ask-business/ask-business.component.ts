import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { AskService, AskStatus } from '../../services/ask.service';
import { Conversation, ConversationsService, ConversationSummary } from '../../services/conversations.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  pending?: boolean;
  sources?: string[];
}

const KIND = 'business';

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

  conversations: ConversationSummary[] = [];
  conversationsLoading = true;
  activeConversationId: string | null = null;

  @ViewChild('chatLog') chatLogRef?: ElementRef<HTMLDivElement>;

  constructor(
    private askService: AskService,
    private conversationsService: ConversationsService
  ) {}

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

    this.loadConversations();
  }

  loadConversations(): void {
    this.conversationsLoading = true;
    this.conversationsService.list().subscribe({
      next: (conversations) => {
        this.conversations = conversations.filter((c) => c.kind === KIND);
        this.conversationsLoading = false;
      },
      error: () => {
        this.conversationsLoading = false;
      },
    });
  }

  startNewConversation(): void {
    this.activeConversationId = null;
    this.messages = [];
  }

  selectConversation(conversation: ConversationSummary): void {
    if (conversation.id === this.activeConversationId) return;
    this.conversationsService.get(conversation.id).subscribe({
      next: (full: Conversation) => {
        this.activeConversationId = full.id;
        this.messages = full.messages.map((m) => ({
          role: m.role,
          text: m.text,
          sources: m.sources.length ? m.sources : undefined,
        }));
        this.scrollToBottom();
      },
    });
  }

  deleteConversation(conversation: ConversationSummary, event: Event): void {
    event.stopPropagation();
    if (!confirm(`Delete conversation "${conversation.title}"?`)) return;

    this.conversationsService.delete(conversation.id).subscribe({
      next: () => {
        if (this.activeConversationId === conversation.id) {
          this.startNewConversation();
        }
        this.loadConversations();
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
    let isNewConversation = false;

    await this.askService.askBusiness(
      question,
      {
        onConversationId: (conversationId) => {
          isNewConversation = this.activeConversationId !== conversationId;
          this.activeConversationId = conversationId;
        },
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
          if (isNewConversation) {
            this.loadConversations();
          }
        },
      },
      this.activeConversationId ?? undefined
    );

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
