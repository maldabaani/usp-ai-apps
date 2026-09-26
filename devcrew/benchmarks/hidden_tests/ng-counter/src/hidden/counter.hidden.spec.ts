import { ComponentFixture, TestBed } from '@angular/core/testing';

import { CounterComponent } from '../app/counter/counter.component';

describe('hidden: CounterComponent', () => {
  let fixture: ComponentFixture<CounterComponent>;
  let el: HTMLElement;

  const q = (id: string) => el.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const count = () => q('count')?.textContent?.trim();
  const click = (id: string) => {
    q(id)?.click();
    fixture.detectChanges();
  };

  function setup(start?: number): void {
    TestBed.configureTestingModule({ imports: [CounterComponent] });
    fixture = TestBed.createComponent(CounterComponent);
    if (start !== undefined) {
      fixture.componentRef.setInput('start', start);
    }
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  }

  it('starts at 0 by default', () => {
    setup();
    expect(count()).toBe('0');
  });

  it('starts at the given input', () => {
    setup(5);
    expect(count()).toBe('5');
  });

  it('increments and decrements', () => {
    setup(1);
    click('increment');
    click('increment');
    expect(count()).toBe('3');
    click('decrement');
    expect(count()).toBe('2');
  });

  it('never goes below zero and disables decrement at zero', () => {
    setup(1);
    click('decrement');
    expect(count()).toBe('0');
    expect((q('decrement') as HTMLButtonElement).disabled).toBeTrue();
    click('decrement');
    expect(count()).toBe('0');
  });

  it('resets to start', () => {
    setup(4);
    click('increment');
    click('reset');
    expect(count()).toBe('4');
  });

  it('emits changed with the new count', () => {
    setup(2);
    const seen: number[] = [];
    fixture.componentInstance.changed.subscribe((v: number) => seen.push(v));
    click('increment');
    click('decrement');
    click('decrement');
    expect(seen).toEqual([3, 2, 1]);
  });
});
