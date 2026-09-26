import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { MessageIn, MessageStatus, RunMessage } from '../../core/api.models';

const STATUS_TEXT: Record<MessageStatus, string> = {
  pending: 'waiting for the next safe point',
  delivered: 'given to the developer',
  applied: 'applied',
  answered: 'answered',
  expired: 'not applied (the run ended)',
  withdrawn: 'withdrawn',
};

/**
 * Chat with the running crew. Messages are applied at the next safe point: the Planner or
 * Architect start, the scheduler before each wave (the Coordinator decides: note, new task,
 * cancel a task, answer), or a task's next developer turn for messages to one task.
 */
@Component({
  selector: 'app-chat',
  imports: [DatePipe, FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (shown().length) {
      <ol class="messages">
        @for (m of shown(); track m.id) {
          <li [class]="m.status">
            <div class="head">
              <span class="who">You{{ m.task_id && !taskId() ? ' → ' + m.task_id : '' }}</span>
              <span class="state">{{ statusText[m.status] }}</span>
              <time>{{ m.created_at | date: 'HH:mm:ss' }}</time>
            </div>
            @if (editingId() === m.id) {
              <div class="edit">
                <textarea [ngModel]="editText()" (ngModelChange)="editText.set($event)" [name]="'edit' + m.id" rows="2"
                          aria-label="Edit message"></textarea>
                <button mat-button type="button" (click)="saveEdit(m)" [disabled]="busy() || !editText().trim()">Save</button>
                <button mat-button type="button" (click)="editingId.set(null)">Back</button>
              </div>
            } @else {
              <p class="text">{{ m.text }}</p>
            }
            @if (editable() && m.status === 'pending' && editingId() !== m.id) {
              <div class="row-actions">
                <button mat-button type="button" (click)="startEdit(m)" [disabled]="busy()">Edit</button>
                <button mat-button type="button" (click)="withdraw.emit(m.id)" [disabled]="busy()">Withdraw</button>
              </div>
            }
            @if (m.reply) { <p class="reply"><b>DevCrew:</b> {{ m.reply }}</p> }
          </li>
        }
      </ol>
    } @else {
      <p class="empty">{{ taskId() ? 'No messages to this task yet.' : 'No messages yet.' }}</p>
    }
    @if (!disabled()) {
      <form class="composer" (ngSubmit)="submit()">
        <mat-form-field appearance="outline" class="text">
          <mat-label>{{ taskId() ? 'Message to ' + taskId() : 'Message to the crew' }}</mat-label>
          <textarea matInput rows="2" name="text" [ngModel]="text()" (ngModelChange)="text.set($event)"
                    (keydown.control.enter)="submit()"
                    placeholder="e.g. use UUIDs for ids · also add a /health endpoint · drop T3"></textarea>
        </mat-form-field>
        @if (!taskId()) {
          <mat-form-field appearance="outline" class="target">
            <mat-label>To</mat-label>
            <mat-select name="target" [ngModel]="target()" (ngModelChange)="target.set($event)">
              <mat-option [value]="null">Whole run</mat-option>
              @for (t of tasks(); track t.id) { <mat-option [value]="t.id">{{ t.id }} · {{ t.title }}</mat-option> }
            </mat-select>
          </mat-form-field>
        }
        <button mat-flat-button type="submit" [disabled]="busy() || !text().trim()">Send</button>
      </form>
      <p class="hint">Applied at the next safe point: before the next wave of tasks (the Coordinator
        adds or cancels tasks, notes guidance or answers), or at the task's next developer turn.</p>
    }
  `,
  styles: `
    :host { display: block; }
    .messages { list-style: none; padding: 0; margin: 0 0 10px; display: flex; flex-direction: column; gap: 8px; }
    .messages li { padding: 8px 10px; border-radius: 8px; background: rgba(8, 17, 34, 0.7);
                   border-left: 3px solid var(--dc-cyan); font-size: 13px; }
    .messages li.pending { border-left-color: var(--dc-amber); }
    .messages li.expired, .messages li.withdrawn { border-left-color: var(--dc-text-faint); opacity: 0.75; }
    .messages li.withdrawn .text { text-decoration: line-through; }
    .edit { display: flex; gap: 6px; align-items: flex-start; margin-top: 4px; }
    .edit textarea { flex: 1; font: inherit; background: var(--dc-code-bg); color: inherit;
                     border: 1px solid var(--dc-border-strong); border-radius: 6px; padding: 4px 6px; }
    .row-actions { display: flex; gap: 4px; margin-top: 2px; }
    .row-actions button { min-width: 0; padding: 0 8px; height: 26px; font-size: 12px; }
    .messages li.answered, .messages li.applied, .messages li.delivered { border-left-color: var(--dc-teal); }
    .head { display: flex; gap: 10px; align-items: baseline; font-size: 12px; }
    .who { font-weight: 700; color: var(--dc-cyan); }
    .state { color: var(--dc-text-dim); }
    time { margin-left: auto; color: var(--dc-text-faint); font-family: var(--dc-mono); }
    .text { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word; }
    .reply { margin: 6px 0 0; color: var(--dc-text-dim); white-space: pre-wrap; }
    .reply b { color: #ff7ad9; }
    .composer { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }
    .composer .text { flex: 1 1 260px; }
    .composer .target { width: 200px; }
    .composer button { margin-top: 8px; }
    .hint, .empty { color: var(--dc-text-faint); font-size: 12px; margin: 0; }
  `,
})
export class ChatComponent {
  readonly messages = input<RunMessage[]>([]);
  readonly tasks = input<{ id: string; title: string }[]>([]);
  /** Fixed target (the task panel); null for the run-level chat. */
  readonly taskId = input<string | null>(null);
  readonly disabled = input(false);
  readonly busy = input(false);
  /** Waiting messages can be edited or withdrawn (the run-level chat). */
  readonly editable = input(false);
  readonly send = output<MessageIn>();
  readonly edited = output<{ id: number; text: string }>();
  readonly withdraw = output<number>();
  readonly editingId = signal<number | null>(null);
  readonly editText = signal('');

  readonly text = signal('');
  readonly target = signal<string | null>(null);
  readonly statusText = STATUS_TEXT;
  readonly shown = computed(() => {
    const task = this.taskId();
    return task ? this.messages().filter((m) => m.task_id === task) : this.messages();
  });

  startEdit(m: RunMessage): void {
    this.editText.set(m.text);
    this.editingId.set(m.id);
  }

  saveEdit(m: RunMessage): void {
    const text = this.editText().trim();
    if (text) {
      this.edited.emit({ id: m.id, text });
      this.editingId.set(null);
    }
  }

  submit(): void {
    const text = this.text().trim();
    if (!text || this.busy()) {
      return;
    }
    this.send.emit({ text, task_id: this.taskId() ?? this.target() });
    this.text.set('');
  }
}
