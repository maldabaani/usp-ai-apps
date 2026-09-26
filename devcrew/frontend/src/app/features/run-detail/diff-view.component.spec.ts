import { TestBed } from '@angular/core/testing';

import { LineComment } from '../../core/diff';
import { DiffViewComponent } from './diff-view.component';

const DIFF = `diff --git a/app/main.py b/app/main.py
--- a/app/main.py
+++ b/app/main.py
@@ -1,2 +1,2 @@
 import x
-old = 1
+new = 2
`;

describe('DiffViewComponent', () => {
  it('adds comments on clicked lines and shows them under the line', () => {
    TestBed.configureTestingModule({ imports: [DiffViewComponent] });
    const fixture = TestBed.createComponent(DiffViewComponent);
    fixture.componentRef.setInput('diff', DIFF);
    fixture.componentRef.setInput('commentable', true);
    const added: Omit<LineComment, 'id'>[] = [];
    fixture.componentInstance.added.subscribe((c) => added.push(c));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const rows = [...el.querySelectorAll('tr.commentable')] as HTMLElement[];
    expect(rows.length).toBe(3);
    rows[2].click(); // "+new = 2"
    fixture.detectChanges();
    fixture.componentInstance.draft.set('use a constant');
    fixture.detectChanges();
    ([...el.querySelectorAll('.draft button')].find((b) => b.textContent === 'Add') as HTMLButtonElement).click();
    expect(added).toEqual([{ path: 'app/main.py', line: 2, side: 'new', code: 'new = 2', text: 'use a constant' }]);

    fixture.componentRef.setInput('comments', [{ id: 1, ...added[0] }]);
    fixture.detectChanges();
    expect(el.querySelector('.comment')?.textContent).toContain('use a constant');
    rows[1].click(); // "-old = 1": the removed line is addressed by its old number
    fixture.componentInstance.draft.set('why?');
    fixture.componentInstance.save('app/main.py', { kind: 'del', text: 'old = 1', oldNo: 2, newNo: null });
    expect(added[1]).toEqual({ path: 'app/main.py', line: 2, side: 'old', code: 'old = 1', text: 'why?' });
  });

  it('is read-only by default', () => {
    TestBed.configureTestingModule({ imports: [DiffViewComponent] });
    const fixture = TestBed.createComponent(DiffViewComponent);
    fixture.componentRef.setInput('diff', DIFF);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('tr.commentable')).toBeNull();
    (el.querySelectorAll('tr')[2] as HTMLElement).click();
    fixture.detectChanges();
    expect(el.querySelector('.draft')).toBeNull();
  });
});
