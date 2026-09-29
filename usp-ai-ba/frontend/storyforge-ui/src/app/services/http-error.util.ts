// Shared helper for turning an HttpClient error response into a readable
// string. FastAPI's error body shape varies by failure kind:
//  - HTTPException(detail="...") -> { detail: "some string" }
//  - Pydantic v2 422 validation error -> { detail: [{ type, loc, msg, input, ctx }, ...] }
//  - Network failure / CORS block / unparseable body -> no usable .error.detail at all
// Every call site in this app that does
// `err?.error?.detail || 'fallback'` assumes the first shape only, which
// renders the array case as the literal text "[object Object]". This
// helper makes that assumption safe for all three.

interface PydanticValidationItem {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

export function extractErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { error?: { detail?: unknown } } | null | undefined)?.error?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  if (Array.isArray(detail) && detail.length) {
    const messages = detail
      .map((item) => formatValidationItem(item as PydanticValidationItem))
      .filter((msg): msg is string => !!msg);
    if (messages.length) {
      return messages.join('; ');
    }
  }

  return fallback;
}

function formatValidationItem(item: PydanticValidationItem): string | null {
  if (!item || typeof item.msg !== 'string' || !item.msg.trim()) return null;
  // loc is typically ["body", "repo_target"] -- drop the leading "body"/
  // "query"/"path" noise and use the field name as a prefix when present.
  const field = (item.loc ?? [])
    .filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
    .join('.');
  return field ? `${field}: ${item.msg}` : item.msg;
}
