/** Requirements documents: which files are accepted and how several are combined. */

export const ACCEPTED_EXTENSIONS = ['.md', '.markdown', '.txt'] as const;

export interface RequirementFile {
  name: string;
  text: string;
}

export function isAcceptedFile(name: string): boolean {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** One file is used as is; several are joined, each under a `# <file name>` heading. */
export function combineRequirements(files: readonly RequirementFile[]): string {
  const cleaned = files.map((f) => ({ name: f.name, text: f.text.replace(/\r\n/g, '\n').trim() }));
  if (cleaned.length === 1) {
    return cleaned[0].text;
  }
  return cleaned.map((f) => `# ${f.name}\n\n${f.text}`).join('\n\n');
}

/** A short title for lists: the first non-empty line, without Markdown heading marks. */
export function requestTitle(request: string): string {
  const line = request.split('\n').find((l) => l.trim()) ?? '';
  return line.replace(/^#+\s*/, '').trim();
}
