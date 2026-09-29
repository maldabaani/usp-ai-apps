import { ComponentFixture, TestBed } from '@angular/core/testing';

import { TodoListComponent } from '../app/todo-list/todo-list.component';

describe('hidden: TodoListComponent', () => {
  let fixture: ComponentFixture<TodoListComponent>;
  let el: HTMLElement;

  const q = <T extends HTMLElement = HTMLElement>(id: string, root: ParentNode = el) =>
    root.querySelector<T>(`[data-testid="${id}"]`);
  const items = () => [...el.querySelectorAll<HTMLElement>('[data-testid="todo-item"]')];
  const titles = () => items().map((i) => i.textContent?.trim() ?? '');
  const remaining = () => q('remaining')?.textContent?.trim();

  function click(target: HTMLElement | null): void {
    target?.click();
    fixture.detectChanges();
  }

  function add(text: string): void {
    const input = q<HTMLInputElement>('new-todo')!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    click(q('add'));
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [TodoListComponent] });
    fixture = TestBed.createComponent(TodoListComponent);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  });

  it('adds trimmed todos and clears the input', async () => {
    add('  write specs  ');
    expect(items().length).toBe(1);
    expect(titles()[0]).toContain('write specs');
    await fixture.whenStable(); // ngModel writes to the DOM asynchronously
    fixture.detectChanges();
    expect(q<HTMLInputElement>('new-todo')!.value).toBe('');
  });

  it('ignores blank input', () => {
    add('   ');
    expect(items().length).toBe(0);
  });

  it('adds on Enter', () => {
    const input = q<HTMLInputElement>('new-todo')!;
    input.value = 'via enter';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter' }));
    fixture.detectChanges();
    expect(items().length).toBe(1);
  });

  it('toggles, marks done and counts remaining', () => {
    add('a');
    add('b');
    expect(remaining()).toBe('2 items left');
    click(q('toggle', items()[0]));
    expect(items()[0].classList).toContain('done');
    expect(q<HTMLInputElement>('toggle', items()[0])!.checked).toBeTrue();
    expect(remaining()).toBe('1 item left');
  });

  it('removes a todo', () => {
    add('keep');
    add('drop');
    click(q('remove', items()[1]));
    expect(items().length).toBe(1);
    expect(titles()[0]).toContain('keep');
  });

  it('filters active and done', () => {
    add('open');
    add('finished');
    click(q('toggle', items()[1]));
    click(q('filter-active'));
    expect(titles().length).toBe(1);
    expect(titles()[0]).toContain('open');
    click(q('filter-done'));
    expect(titles().length).toBe(1);
    expect(titles()[0]).toContain('finished');
    click(q('filter-all'));
    expect(items().length).toBe(2);
  });

  it('clears done todos', () => {
    add('x');
    add('y');
    click(q('toggle', items()[0]));
    click(q('clear-done'));
    expect(items().length).toBe(1);
    expect(titles()[0]).toContain('y');
  });
});
