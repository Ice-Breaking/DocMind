import { memo, useMemo, type AnchorHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

function citationToMarkdown(text: string): string {
  const regex =
    /\[来源: ([^\]\n]+?\.(?:md|txt|pdf|docx|xlsx|png|jpg|jpeg|webp))(?: · 第(\d+)页)?\]/g;
  return text.replace(regex, (_m, filename: string, page?: string) => {
    const label = `来源: ${filename}${page ? ` · 第${page}页` : ''}`;
    const target = `#source-${encodeURIComponent(filename)}${page ? `@${page}` : ''}`;
    return `[${label}](${target})`;
  });
}

/** Memoized markdown 渲染器，避免每次渲染重复解析 markdown */
/** 判定 mermaid：语言标记或内容特征（兼容 AI 漏写语言标记） */
function isMermaidLike(cls: string, text: string): boolean {
  if (/language-mermaid/.test(cls)) return true;
  return /^\s*(flowchart|graph|sequenceDiagram|gantt|pie|mindmap|classDiagram|stateDiagram)\b/.test(
    text,
  );
}

const MarkdownContent = memo(function MarkdownContent({
  content,
  onLocate,
}: {
  content: string;
  onLocate: (filename: string, page: string | undefined) => void;
}) {
  const components = useMemo<Components>(
    () => ({
      pre: (props: any) => {
        const cls =
          props?.node?.children?.[0]?.properties?.className?.join(' ') || '';
        const txt = String(props?.node?.children?.[0]?.children?.[0]?.value || '');
        if (isMermaidLike(cls, txt)) return null;
        return <pre>{props.children}</pre>;
      },
      code: ({ className, children }: any) => {
        const txt = String(children);
        if (isMermaidLike(className || '', txt)) return null;
        return <code className={className}>{children}</code>;
      },
      a: ({ href, children }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (href?.startsWith('#source-')) {
          return (
            <a
              className="dm-source-link"
              href={href}
              onClick={(e) => {
                e.preventDefault();
                const payload = href.slice('#source-'.length);
                const atIdx = payload.lastIndexOf('@');
                const filename = decodeURIComponent(
                  atIdx >= 0 ? payload.slice(0, atIdx) : payload,
                );
                const page = atIdx >= 0 ? payload.slice(atIdx + 1) : undefined;
                onLocate(filename, page);
              }}
            >
              {children}
            </a>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [onLocate],
  );
  return (
    <div className="react-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {citationToMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
});

export default MarkdownContent;
