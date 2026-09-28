import { combineRequirements, isAcceptedFile, requestTitle } from './requirements';

describe('requirements', () => {
  it('accepts Markdown and text files only', () => {
    expect(isAcceptedFile('Spec.MD')).toBeTrue();
    expect(isAcceptedFile('notes.txt')).toBeTrue();
    expect(isAcceptedFile('guide.markdown')).toBeTrue();
    expect(isAcceptedFile('spec.pdf')).toBeFalse();
    expect(isAcceptedFile('spec.docx')).toBeFalse();
  });

  it('uses one file as is and heads several with their names', () => {
    expect(combineRequirements([{ name: 'a.md', text: '  # A\r\nbody \n' }])).toBe('# A\nbody');
    expect(combineRequirements([{ name: 'a.md', text: 'A' }, { name: 'b.txt', text: 'B' }]))
      .toBe('# a.md\n\nA\n\n# b.txt\n\nB');
  });

  it('derives a title from the first non-empty line', () => {
    expect(requestTitle('\n\n## Shop backend\n\nDetails')).toBe('Shop backend');
    expect(requestTitle('Build a TODO API')).toBe('Build a TODO API');
    expect(requestTitle('')).toBe('');
  });
});
