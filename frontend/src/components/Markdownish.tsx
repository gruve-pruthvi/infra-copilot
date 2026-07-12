// Tiny markdown-ish renderer: fenced code blocks -> <pre>, inline `code` ->
// <code>, newlines preserved. Deliberately no full markdown dependency.

import { Fragment } from "react";

function InlineText({ text }: { text: string }) {
  const segs = text.split(/(`[^`\n]+`)/g);
  return (
    <p className="whitespace-pre-wrap break-words leading-relaxed">
      {segs.map((seg, i) =>
        seg.startsWith("`") && seg.endsWith("`") && seg.length > 2 ? (
          <code key={i} className="rounded bg-raise px-1 py-px font-mono text-[12.5px] text-accent">
            {seg.slice(1, -1)}
          </code>
        ) : (
          <Fragment key={i}>{seg}</Fragment>
        ),
      )}
    </p>
  );
}

export function Markdownish({ text }: { text: string }) {
  const parts = text.split("```");
  return (
    <div className="space-y-2">
      {parts.map((part, i) => {
        if (i % 2 === 1) {
          // fenced block; drop an optional language token on the first line
          let body = part;
          const nl = part.indexOf("\n");
          if (nl !== -1 && /^[\w+-]*$/.test(part.slice(0, nl).trim())) {
            body = part.slice(nl + 1);
          }
          return (
            <pre
              key={i}
              className="overflow-x-auto rounded border border-edge bg-ink p-2.5 font-mono text-[12.5px] leading-relaxed text-fg"
            >
              {body.replace(/\n$/, "")}
            </pre>
          );
        }
        if (!part) return null;
        return <InlineText key={i} text={part} />;
      })}
    </div>
  );
}
