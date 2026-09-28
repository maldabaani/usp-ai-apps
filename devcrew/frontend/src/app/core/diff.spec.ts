import { commentsAsFeedback, parseUnifiedDiff } from './diff';

const DIFF = `diff --git a/app/main.py b/app/main.py
index 1..2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,3 +1,4 @@
 import x
-old = 1
+new = 2
+more = 3
 end
diff --git a/tests/test_a.py b/tests/test_a.py
new file mode 100644
--- /dev/null
+++ b/tests/test_a.py
@@ -0,0 +1 @@
+def test_a(): pass
`;

describe('parseUnifiedDiff', () => {
  it('parses files, hunks, counts and line numbers', () => {
    const files = parseUnifiedDiff(DIFF);
    expect(files.map((f) => f.path)).toEqual(['app/main.py', 'tests/test_a.py']);
    const [main, test] = files;
    expect([main.added, main.removed]).toEqual([2, 1]);
    const lines = main.hunks[0].lines;
    expect(lines.map((l) => l.kind)).toEqual(['ctx', 'del', 'add', 'add', 'ctx']);
    expect(lines[1]).toEqual({ kind: 'del', text: 'old = 1', oldNo: 2, newNo: null });
    expect(lines[3]).toEqual({ kind: 'add', text: 'more = 3', oldNo: null, newNo: 3 });
    expect(lines[4].newNo).toBe(4);
    expect(test.added).toBe(1);
    expect(test.hunks[0].lines.length).toBe(1); // the diff's trailing newline is not a line
  });

  it('returns nothing for an empty diff', () => {
    expect(parseUnifiedDiff('')).toEqual([]);
  });
});

describe('commentsAsFeedback', () => {
  it('lists line comments by file and line, with the code', () => {
    const text = commentsAsFeedback([
      { id: 2, path: 'b.py', line: 3, side: 'new', code: 'x = 1', text: 'rename x' },
      { id: 1, path: 'a.py', line: 9, side: 'old', code: '', text: 'keep this\nplease' },
    ]);
    expect(text).toBe(
      'Review comments on the changes:\n- a.py:9 (removed line)\n  keep this\n  please\n- b.py:3\n  `x = 1`\n  rename x',
    );
    expect(commentsAsFeedback([])).toBe('');
  });
});
