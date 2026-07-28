import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

function externalHref(value: string) {
  const href = value.trim();
  if (!href || href.startsWith('#') || /^(https?:|mailto:|tel:)/i.test(href)) return href;
  const embeddedUrl = href.match(/https?:\/\/[^\s，。；、）)\]]+/i)?.[0];
  if (embeddedUrl) return embeddedUrl;
  if (href.startsWith('//')) return `https:${href}`;
  const embeddedDomain = href.match(/(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/[^\s，。；、）)\]]*)?/i)?.[0];
  return `https://${(embeddedDomain ?? href).replace(/^\/+/, '')}`;
}

export default function MarkdownRenderer({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ node: _node, href, ...props }) => <a {...props} href={externalHref(href ?? '')} target="_blank" rel="noreferrer" />,
  }}>{content}</ReactMarkdown>;
}
