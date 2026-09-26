import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { PendingInput, ResumeRequest } from '../../core/api.models';
import { ActionPanelComponent } from './action-panel.component';

const approval: PendingInput = {
  interrupt_id: 'i1', kind: 'approval', title: 'Approve the plan', artifact: 'plan',
  allowed_actions: ['approve', 'reject', 'edit'], data: { plan: { summary: 's', tasks: [] } }, error: null,
};

describe('ActionPanelComponent', () => {
  let fixture: ComponentFixture<ActionPanelComponent>;
  let sent: ResumeRequest[];

  function setup(pending: PendingInput): HTMLElement {
    TestBed.configureTestingModule({ imports: [ActionPanelComponent], providers: [provideNoopAnimations()] });
    fixture = TestBed.createComponent(ActionPanelComponent);
    fixture.componentRef.setInput('pending', pending);
    sent = [];
    fixture.componentInstance.resumeRequested.subscribe((r) => sent.push(r));
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const buttons = (el: HTMLElement) => [...el.querySelectorAll('button')].map((b) => b.textContent?.trim());
  const click = (el: HTMLElement, label: string) => {
    [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)?.click();
    fixture.detectChanges();
  };

  it('shows only allowed actions and approves with the interrupt id', () => {
    const el = setup(approval);
    expect(buttons(el)).toEqual(['Approve', 'Edit', 'Reject']);
    click(el, 'Approve');
    expect(sent).toEqual([{ action: 'approve', interrupt_id: 'i1' }]);
  });

  it('requires feedback to reject', () => {
    const el = setup(approval);
    click(el, 'Reject');
    const send = [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Send');
    expect(send?.disabled).toBeTrue();
    fixture.componentInstance.text.set('Add due dates');
    fixture.detectChanges();
    click(el, 'Send');
    expect(sent).toEqual([{ interrupt_id: 'i1', action: 'reject', feedback: 'Add due dates' }]);
  });

  it('edits the artifact as JSON and rejects invalid JSON', () => {
    const el = setup(approval);
    click(el, 'Edit');
    expect(JSON.parse(fixture.componentInstance.json())).toEqual({ summary: 's', tasks: [] });
    fixture.componentInstance.json.set('{ not json');
    click(el, 'Send');
    expect(sent).toEqual([]);
    expect(el.textContent).toContain('Invalid JSON');
    fixture.componentInstance.json.set('{"summary": "edited", "tasks": []}');
    click(el, 'Send');
    expect(sent[0].action).toBe('edit');
    expect(sent[0].artifact).toEqual({ summary: 'edited', tasks: [] });
  });

  it('answers a question', () => {
    const el = setup({
      interrupt_id: 'q1', kind: 'question', title: 'The developer has a question', artifact: null,
      allowed_actions: ['answer'], data: { question: 'Int or UUID ids?', role: 'developer', task_id: 'T1' }, error: null,
    });
    expect(el.textContent).toContain('Int or UUID ids?');
    expect(el.textContent).toContain('from developer');
    click(el, 'Answer');
    fixture.componentInstance.text.set('Int');
    fixture.detectChanges();
    click(el, 'Send');
    expect(sent).toEqual([{ interrupt_id: 'q1', action: 'answer', answer: 'Int' }]);
  });

  it('labels escalation actions and shows the backend error', () => {
    const el = setup({
      interrupt_id: 'e1', kind: 'escalation', title: 'Task T1 needs a decision', artifact: null,
      allowed_actions: ['approve', 'answer', 'reject'], data: { reason: 'iteration limit', task_id: 'T1' },
      error: 'invalid resume payload',
    });
    expect(buttons(el)).toEqual(['Retry', 'Retry with guidance', 'Give up on task']);
    expect(el.textContent).toContain('iteration limit');
    expect(el.textContent).toContain('invalid resume payload');
  });

  it('words delivery failures as retry / finish without PR', () => {
    const el = setup({
      interrupt_id: 'd1', kind: 'escalation', title: 'GitHub delivery failed', artifact: null,
      allowed_actions: ['approve', 'reject'],
      data: { node: 'github_delivery', reason: 'GITHUB_TOKEN was rejected', question: 'Delivery failed' },
      error: null,
    });
    expect(buttons(el)).toEqual(['Retry delivery', 'Finish without PR']);
    expect(el.textContent).toContain('GITHUB_TOKEN was rejected');
  });

  it('stops watching a pull request with a reason (the poller sends the updates)', () => {
    const el = setup({
      interrupt_id: 'w1', kind: 'watch', title: 'Watching the pull request', artifact: null,
      allowed_actions: ['update', 'reject'], data: { round: 1, max_rounds: 3 }, error: null,
    });
    expect(buttons(el)).toEqual(['Stop watching']);
    expect(el.textContent).toContain('1 of 3 automatic rounds used');
    click(el, 'Stop watching');
    fixture.componentInstance.text.set('merged by hand');
    fixture.detectChanges();
    click(el, 'Send');
    expect(sent).toEqual([{ interrupt_id: 'w1', action: 'reject', feedback: 'merged by hand' }]);
  });

  it('lists follow-up changes that need approval', () => {
    const el = setup({
      interrupt_id: 'f1', kind: 'approval', title: 'Approve follow-up changes (round 1)', artifact: 'followup',
      allowed_actions: ['approve', 'reject'], error: null,
      data: {
        round: 1,
        items: [{
          task: { id: 'R1-1', title: 'Bump fastapi', description: '', target_files: [], depends_on: [], stack: 'python', story_ids: [] },
          reason: 'touches pyproject.toml',
          item: { kind: 'review', key: 'review:9', user: 'alice', body: 'Please bump fastapi', path: 'pyproject.toml' },
        }],
        small: ['Rename X'],
      },
    });
    expect(buttons(el)).toEqual(['Approve changes', 'Decline']);
    expect(el.textContent).toContain('R1-1 · Bump fastapi');
    expect(el.textContent).toContain('@alice on pyproject.toml: Please bump fastapi');
    expect(el.textContent).toContain('Also in this round (small, automatic): Rename X');
  });
});
