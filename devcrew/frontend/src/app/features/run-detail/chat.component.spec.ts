import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { MessageIn, RunMessage } from '../../core/api.models';
import { ChatComponent } from './chat.component';

const msg = (id: number, patch: Partial<RunMessage> = {}): RunMessage => ({
  id, task_id: null, text: `message ${id}`, status: 'pending', action: null, reply: null,
  created_at: '2026-09-26T10:00:00Z', updated_at: null, ...patch,
});

describe('ChatComponent', () => {
  let fixture: ComponentFixture<ChatComponent>;
  let sent: MessageIn[];

  function setup(inputs: Record<string, unknown>): HTMLElement {
    TestBed.configureTestingModule({ imports: [ChatComponent], providers: [provideNoopAnimations()] });
    fixture = TestBed.createComponent(ChatComponent);
    for (const [k, v] of Object.entries(inputs)) {
      fixture.componentRef.setInput(k, v);
    }
    sent = [];
    fixture.componentInstance.send.subscribe((m) => sent.push(m));
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows messages with their state and the Coordinator reply', () => {
    const el = setup({
      messages: [
        msg(1, { status: 'answered', action: 'answer', reply: 'Two tasks are left.' }),
        msg(2, { task_id: 'T2' }),
      ],
      tasks: [{ id: 'T2', title: 'Router' }],
    });
    const items = [...el.querySelectorAll('.messages li')];
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('DevCrew: Two tasks are left.');
    expect(items[1].textContent).toContain('You → T2');
    expect(items[1].textContent).toContain('waiting for the next safe point');
  });

  it('sends to the whole run or to a chosen task', () => {
    setup({ tasks: [{ id: 'T2', title: 'Router' }] });
    const c = fixture.componentInstance;
    c.text.set('   ');
    c.submit();
    expect(sent).toEqual([]);
    c.text.set(' use UUIDs ');
    c.submit();
    c.target.set('T2');
    c.text.set('return 404');
    c.submit();
    expect(sent).toEqual([{ text: 'use UUIDs', task_id: null }, { text: 'return 404', task_id: 'T2' }]);
    expect(c.text()).toBe('');
  });

  it('in a task panel shows and sends only that task', () => {
    const el = setup({ messages: [msg(1), msg(2, { task_id: 'T1' })], taskId: 'T1' });
    expect(el.querySelectorAll('.messages li').length).toBe(1);
    expect(el.querySelector('mat-select')).toBeNull();
    fixture.componentInstance.text.set('smaller functions');
    fixture.componentInstance.submit();
    expect(sent).toEqual([{ text: 'smaller functions', task_id: 'T1' }]);
  });

  it('hides the composer when disabled', () => {
    const el = setup({ disabled: true });
    expect(el.querySelector('form')).toBeNull();
  });

  it('edits and withdraws waiting messages when editable', () => {
    const el = setup({ messages: [msg(1), msg(2, { status: 'applied' })], editable: true });
    const edits: { id: number; text: string }[] = [];
    const withdrawn: number[] = [];
    fixture.componentInstance.edited.subscribe((e) => edits.push(e));
    fixture.componentInstance.withdraw.subscribe((id) => withdrawn.push(id));
    const items = [...el.querySelectorAll('.messages li')];
    expect(items[0].querySelectorAll('.row-actions button').length).toBe(2);
    expect(items[1].querySelector('.row-actions')).toBeNull(); // already applied
    const c = fixture.componentInstance;
    c.startEdit(msg(1));
    c.editText.set('message 1, clearer');
    c.saveEdit(msg(1));
    expect(edits).toEqual([{ id: 1, text: 'message 1, clearer' }]);
    ([...items[0].querySelectorAll('.row-actions button')].find((b) => b.textContent?.trim() === 'Withdraw') as HTMLButtonElement).click();
    expect(withdrawn).toEqual([1]);
  });
});
