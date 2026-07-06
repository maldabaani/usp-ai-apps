// Shared SSE (Server-Sent Events) client for the "sources" + "chunk" frame
// contract used by both codemind.service.ts's job-scoped Ask streams and
// ask.service.ts's standing Ask Technical/Business streams. Raw fetch()
// rather than HttpClient: reading a streamed response body chunk by chunk
// needs the Fetch Response's ReadableStream, which HttpClient doesn't
// expose directly.

export interface SseStreamHandlers {
  onSources: (sources: string[]) => void;
  onChunk: (chunk: string) => void;
  onError: (message: string) => void;
  onComplete: () => void;
}

export async function streamSse(
  url: string,
  body: Record<string, unknown>,
  handlers: SseStreamHandlers,
  token: string | null
): Promise<void> {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });

    if (!response.ok || !response.body) {
      handlers.onError(`Error: ${response.statusText}`);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        let eventName = 'message';
        const dataLines: string[] = [];
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            const rest = line.slice(5);
            dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
          }
        }
        const data = dataLines.join('\n');

        if (eventName === 'sources') {
          try {
            handlers.onSources(JSON.parse(data));
          } catch {
            // malformed sources frame -- ignore, chunks still stream
          }
        } else if (eventName === 'chunk') {
          try {
            handlers.onChunk(JSON.parse(data));
          } catch {
            handlers.onChunk(data);
          }
        }
      }
    }
    handlers.onComplete();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    handlers.onError(`Network error: ${message}`);
  }
}
