import React from 'react';

/** Render the small Markdown subset used by approved answers ("- " bullets and
 * **bold**) as React elements. Never injects HTML; anything else stays text. */
const BOLD = /\*\*([^*]+)\*\*/g;

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(BOLD)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    parts.push(<strong key={`${keyPrefix}-${index}`}>{match[1]}</strong>);
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export default function MarkdownText({text, className}: {text: string; className?: string}) {
  const lines = text.split('\n');
  return <div className={className}>
    {lines.map((line, index) => {
      const bullet = /^\s*[-*•]\s+/.exec(line);
      const body = bullet ? line.slice(bullet[0].length) : line;
      return <p key={index} className={bullet ? 'pl-3 -indent-3' : undefined}>
        {bullet ? '• ' : null}{inline(body, String(index))}
      </p>;
    })}
  </div>;
}
