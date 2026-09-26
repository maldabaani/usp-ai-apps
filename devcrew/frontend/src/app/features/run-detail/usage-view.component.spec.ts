import { TestBed } from '@angular/core/testing';

import { RunUsage } from '../../core/api.models';
import { UsageViewComponent } from './usage-view.component';

const USAGE: RunUsage = {
  calls: 12, input_tokens: 40000, output_tokens: 5000, total_tokens: 45000, model_seconds: 95,
  elapsed_s: 1800, waiting_s: 600, active_s: 1200,
  by_role: [
    { key: 'developer', calls: 8, input_tokens: 30000, output_tokens: 4000, model_seconds: 70 },
    { key: 'planner', calls: 4, input_tokens: 10000, output_tokens: 1000, model_seconds: 25 },
  ],
  by_task: [{ key: 'T1', calls: 8, input_tokens: 30000, output_tokens: 4000, model_seconds: 70 }],
  budget: { tokens: 40000, minutes: 60 },
};

describe('UsageViewComponent', () => {
  it('shows totals, the budget and the split per role and task', () => {
    TestBed.configureTestingModule({ imports: [UsageViewComponent] });
    const fixture = TestBed.createComponent(UsageViewComponent);
    fixture.componentRef.setInput('usage', USAGE);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.card b')?.textContent).toBe('45,000');
    expect(el.textContent).toContain('20m 00s'); // working time
    expect(el.textContent).toContain('Tokens 45,000 / 40,000');
    expect(el.querySelector('.bar div.over')).not.toBeNull(); // tokens over budget
    expect(el.textContent).toContain('Working minutes 20 / 60');
    const rows = [...el.querySelectorAll('tbody tr')].map((r) =>
      [...r.querySelectorAll('td')].map((td) => td.textContent?.replace(/\s+/g, ' ').trim()),
    );
    expect(rows[0].slice(1, 3)).toEqual(['8', '34,000 76%']);
    expect(rows[0][0]).toContain('developer');
    expect(rows[2].slice(0, 3)).toEqual(['T1', '8', '34,000']);
  });

  it('says when there is no budget', () => {
    TestBed.configureTestingModule({ imports: [UsageViewComponent] });
    const fixture = TestBed.createComponent(UsageViewComponent);
    fixture.componentRef.setInput('usage', { ...USAGE, budget: null });
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No budget for this run');
  });
});
