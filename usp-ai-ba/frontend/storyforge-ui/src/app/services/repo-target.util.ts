// Normalizes and validates a user-supplied GitHub "owner/repo" target for
// DevCrew. Mirrors the backend's REPO_RE in
// usp-ai-ba/backend/devcrew/api/schemas.py exactly -- must be kept in sync
// by hand if the backend pattern ever changes.
export const REPO_TARGET_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/;

// Strips common GitHub URL wrapping so a pasted full URL "just works":
// - leading/trailing whitespace
// - "https://github.com/" or "http://github.com/"
// - "git@github.com:" (SSH form)
// - trailing ".git"
// - surrounding/trailing slashes
export function normalizeRepoTarget(raw: string): string {
  let value = (raw ?? '').trim();
  value = value.replace(/^https?:\/\/github\.com\//i, '');
  value = value.replace(/^git@github\.com:/i, '');
  value = value.replace(/\.git$/i, '');
  value = value.replace(/^\/+|\/+$/g, '');
  return value.trim();
}

export function isValidRepoTarget(value: string): boolean {
  return REPO_TARGET_RE.test(value);
}
