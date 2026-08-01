/**
 * Lightweight markdown → React for DocViewer. Headings, emphasis, code, lists, quotes, links.
 * Escapes raw HTML; not a full CommonMark port (inktui layoutDocument stays TUI-only).
 */

import type { ReactNode } from 'react';

function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // code | bold | italic | link — non-overlapping left-to-right
  const re =
    /(`+)([^`]+?)\1|\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      nodes.push(text.slice(last, m.index));
    }
    const key = `${keyPrefix}-${i++}`;
    if (m[2] !== undefined) {
      nodes.push(
        <code key={key} className="mds-md__code">
          {m[2]}
        </code>,
      );
    } else if (m[3] !== undefined) {
      nodes.push(
        <strong key={key} className="mds-md__strong">
          {m[3]}
        </strong>,
      );
    } else if (m[4] !== undefined) {
      nodes.push(
        <em key={key} className="mds-md__em">
          {m[4]}
        </em>,
      );
    } else if (m[5] !== undefined && m[6] !== undefined) {
      nodes.push(
        <a key={key} className="mds-md__link" href={m[6]} target="_blank" rel="noreferrer">
          {m[5]}
        </a>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Render a markdown document body as simple React block elements. */
export function SimpleMarkdown({ source }: { readonly source: string }): React.JSX.Element {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i] ?? '';

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    // Fenced code
    const fence = line.match(/^```(.*)$/);
    if (fence !== null) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? '').startsWith('```')) {
        body.push(lines[i] ?? '');
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push(
        <pre key={key++} className="mds-md__fence">
          <code>{body.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading !== null) {
      const level = heading[1]?.length ?? 1;
      const text = heading[2] ?? '';
      const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(
        <Tag key={key++} className={`mds-md__h mds-md__h${level}`}>
          {inline(text, `h${key}`)}
        </Tag>,
      );
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i] ?? '')) {
        quote.push((lines[i] ?? '').replace(/^>\s?/, ''));
        i += 1;
      }
      blocks.push(
        <blockquote key={key++} className="mds-md__quote">
          {quote.map((q, qi) => (
            <p key={qi}>{inline(q, `q${key}-${qi}`)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i] ?? '')) {
        items.push((lines[i] ?? '').replace(/^\s*([-*+]|\d+\.)\s+/, ''));
        i += 1;
      }
      const ListTag = ordered ? 'ol' : 'ul';
      blocks.push(
        <ListTag key={key++} className="mds-md__list">
          {items.map((item, ii) => (
            <li key={ii}>{inline(item, `li${key}-${ii}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
      blocks.push(<hr key={key++} className="mds-md__hr" />);
      i += 1;
      continue;
    }

    // Paragraph: consume until blank
    const para: string[] = [];
    while (i < lines.length && (lines[i] ?? '').trim() !== '') {
      const peek = lines[i] ?? '';
      if (
        peek.startsWith('#') ||
        peek.startsWith('```') ||
        /^>\s?/.test(peek) ||
        /^\s*([-*+]|\d+\.)\s+/.test(peek)
      ) {
        break;
      }
      para.push(peek);
      i += 1;
    }
    blocks.push(
      <p key={key++} className="mds-md__p">
        {inline(para.join(' '), `p${key}`)}
      </p>,
    );
  }

  return <div className="mds-md">{blocks}</div>;
}
