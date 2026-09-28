import { ScrollingModule } from '@angular/cdk/scrolling';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { RunEvent } from '../../core/api.models';
import { EventTimelineComponent } from './event-timeline.component';

const ev = (id: number, type: RunEvent['type'], payload: Record<string, unknown>, task_id: string | null = null): RunEvent =>
  ({ id, run_id: 'r1', type, node: 'developer', task_id, payload, created_at: '2026-01-01T10:00:00Z' });

describe('EventTimelineComponent', () => {
  it('renders only the visible rows of a long timeline, filters and shows details', async () => {
    TestBed.configureTestingModule({ imports: [EventTimelineComponent, ScrollingModule], providers: [provideNoopAnimations()] });
    const fixture = TestBed.createComponent(EventTimelineComponent);
    const events = Array.from({ length: 5000 }, (_, i) => ev(i + 1, 'error', { message: `problem ${i + 1}` }, i % 2 ? 'T1' : 'T2'));
    fixture.componentRef.setInput('events', events);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const rows = el.querySelectorAll('.row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(200); // virtualized
    expect(rows[0].textContent).toContain('problem 5000'); // newest first
    const c = fixture.componentInstance;
    c.filter.set('problem 4999');
    expect(c.visible().map((e) => e.id)).toEqual([4999]);
    c.filter.set('t2');
    expect(c.visible().length).toBe(2500);
    c.toggle(events[0]);
    fixture.detectChanges();
    expect(el.querySelector('.detail pre')?.textContent).toContain('problem 1');
  });

  it('hides tool calls unless asked, but keeps failed ones', () => {
    TestBed.configureTestingModule({ imports: [EventTimelineComponent], providers: [provideNoopAnimations()] });
    const fixture = TestBed.createComponent(EventTimelineComponent);
    fixture.componentRef.setInput('events', [
      ev(1, 'tool_call', { tool: 'read_file', args: {} }),
      ev(2, 'tool_result', { tool: 'run_tests', ok: false, result: 'boom' }),
      ev(3, 'status', { status: 'executing' }),
    ]);
    const c = fixture.componentInstance;
    expect(c.visible().map((e) => e.id)).toEqual([3, 2]);
    c.showAll.set(true);
    expect(c.visible().map((e) => e.id)).toEqual([3, 2, 1]);
  });
});
