import { ComponentFixture, TestBed } from '@angular/core/testing';

import { SignupFormComponent } from '../app/signup/signup-form.component';

describe('hidden: SignupFormComponent', () => {
  let fixture: ComponentFixture<SignupFormComponent>;
  let el: HTMLElement;

  const q = <T extends HTMLElement = HTMLElement>(id: string) =>
    el.querySelector<T>(`[data-testid="${id}"]`);
  const submit = () => q<HTMLButtonElement>('submit')!;

  function type(id: string, value: string, blur = true): void {
    const input = q<HTMLInputElement>(id)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    if (blur) {
      input.dispatchEvent(new Event('blur'));
    }
    fixture.detectChanges();
  }

  function fillValid(): void {
    type('email', 'ada@example.com');
    type('password', 'secret123');
    type('confirm', 'secret123');
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [SignupFormComponent] });
    fixture = TestBed.createComponent(SignupFormComponent);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  });

  it('starts invalid without showing errors', () => {
    expect(submit().disabled).toBeTrue();
    expect(q('email-error')).toBeNull();
    expect(q('password-error')).toBeNull();
    expect(q('confirm-error')).toBeNull();
  });

  it('shows an email error only after blur', () => {
    type('email', 'nope', false);
    expect(q('email-error')).toBeNull();
    type('email', 'nope');
    expect(q('email-error')).not.toBeNull();
    type('email', 'ok@example.com');
    expect(q('email-error')).toBeNull();
  });

  it('requires 8+ characters with a digit', () => {
    type('password', 'short1');
    expect(q('password-error')).not.toBeNull();
    type('password', 'longenough');
    expect(q('password-error')).not.toBeNull();
    type('password', 'longenough1');
    expect(q('password-error')).toBeNull();
  });

  it('requires matching confirmation', () => {
    type('password', 'secret123');
    type('confirm', 'secret124');
    expect(q('confirm-error')).not.toBeNull();
    type('confirm', 'secret123');
    expect(q('confirm-error')).toBeNull();
  });

  it('enables submit when valid and emits then resets', () => {
    const seen: unknown[] = [];
    fixture.componentInstance.submitted.subscribe((v: unknown) => seen.push(v));
    fillValid();
    expect(submit().disabled).toBeFalse();
    submit().click();
    fixture.detectChanges();
    expect(seen).toEqual([{ email: 'ada@example.com', password: 'secret123' }]);
    expect(q<HTMLInputElement>('email')!.value).toBe('');
  });

  it('stays disabled when only the confirmation is wrong', () => {
    type('email', 'ada@example.com');
    type('password', 'secret123');
    type('confirm', 'different1');
    expect(submit().disabled).toBeTrue();
  });
});
