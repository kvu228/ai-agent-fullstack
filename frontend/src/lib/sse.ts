/**
 * Minimal Server-Sent Events parser for `fetch` streams.
 *
 * Why not the browser's `EventSource`? It only supports GET requests, and our
 * chat endpoint is a POST with a JSON body.
 *
 * Wire format (one event = lines separated by "\n", events separated by a blank line):
 *   event: token
 *   data: {"content": "Hel"}
 */

export interface RawSseEvent {
  event: string;
  data: string;
}

/** Parse one SSE block (text between blank lines). Returns null for comment-only blocks. */
export function parseSseBlock(block: string): RawSseEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue; // blank line or comment (keep-alive ping)
    const sep = line.indexOf(":");
    const field = sep === -1 ? line : line.slice(0, sep);
    // Per spec, a single space after the colon is not part of the value.
    const value = sep === -1 ? "" : line.slice(sep + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }

  return dataLines.length ? { event, data: dataLines.join("\n") } : null;
}

/** Turn a byte stream into an async iterator of SSE events. */
export async function* readSseStream(
  stream: ReadableStream<Uint8Array<ArrayBuffer>>,
): AsyncGenerator<RawSseEvent> {
  // TextDecoderStream handles multi-byte UTF-8 characters split across chunks.
  const reader = stream.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;

      // A network chunk may contain several events, or half of one: keep the remainder.
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const parsed = parseSseBlock(block);
        if (parsed) yield parsed;
      }
    }
    const last = parseSseBlock(buffer);
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}
