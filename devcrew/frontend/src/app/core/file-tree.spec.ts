import { initialExpanded, treeRows } from './file-tree';

const FILES = [
  { path: 'README.md', size: 10 },
  { path: 'app/main.py', size: 20 },
  { path: 'app/routers/todos.py', size: 30 },
  { path: 'tests/test_todos.py', size: 40 },
];

describe('file tree', () => {
  const shape = (rows: ReturnType<typeof treeRows>) => rows.map((r) => `${'  '.repeat(r.depth)}${r.name}${r.kind === 'dir' ? '/' : ''}`);

  it('lists folders first and only opens expanded folders', () => {
    expect(shape(treeRows(FILES, new Set()))).toEqual(['app/', 'tests/', 'README.md']);
    expect(shape(treeRows(FILES, new Set(['app'])))).toEqual([
      'app/', '  routers/', '  main.py', 'tests/', 'README.md',
    ]);
    expect(treeRows(FILES, new Set())[0].files).toBe(2);
  });

  it('shows matches with their folders open when filtering', () => {
    expect(shape(treeRows(FILES, new Set(), 'todos'))).toEqual([
      'app/', '  routers/', '    todos.py', 'tests/', '  test_todos.py',
    ]);
  });

  it('opens every folder of small projects only', () => {
    expect([...initialExpanded(FILES)].sort()).toEqual(['app', 'app/routers', 'tests']);
    expect(initialExpanded(FILES, 2).size).toBe(0);
  });
});
