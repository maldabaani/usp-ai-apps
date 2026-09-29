// Turns a raw backend status string (e.g. "in_progress", "needs_human")
// into readable text ("In progress", "Needs human"). No formatting
// function existed anywhere in this app before the DevCrew run-detail
// canvas needed one for its node/task labels.

export function humanizeStatus(status: string): string {
  return status
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word, i) => (i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ');
}
