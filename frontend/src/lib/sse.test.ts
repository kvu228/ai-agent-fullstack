import { describe, expect, it } from "vitest";

import { parseSseBlock, readSseStream } from "@/lib/sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array<ArrayBuffer>> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array<ArrayBuffer>>) {
  const events = [];
  for await (const event of readSseStream(stream)) events.push(event);
  return events;
}

describe("parseSseBlock", () => {
  it("reads event and data fields", () => {
    expect(parseSseBlock('event: token\ndata: {"content":"hi"}')).toEqual({
      event: "token",
      data: '{"content":"hi"}',
    });
  });

  it("ignores comment-only blocks (keep-alive pings)", () => {
    expect(parseSseBlock(": ping")).toBeNull();
  });
});

describe("readSseStream", () => {
  it("handles events split across network chunks", async () => {
    const events = await collect(
      streamOf(
        "event: token\nda",
        'ta: {"content":"Hel"}\n\nevent: tok',
        'en\ndata: {"content":"lo"}\n\n',
      ),
    );
    expect(events.map((e) => JSON.parse(e.data).content)).toEqual(["Hel", "lo"]);
  });
});
