import { memo, useMemo, type AnchorHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { Image } from 'antd';

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

// 图片加载失败占位：附件文件被清理/丢失时（如历史 e2e 会话），以灰底提示
// 代替浏览器破图图标。data URI SVG 规避额外网络请求，CSP img-src 允许 data:
const IMG_FALLBACK =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="280" height="120">` +
      `<rect width="100%" height="100%" fill="#f5f5f5" rx="8"/>` +
      `<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" ` +
      `fill="#999" font-size="13" font-family="system-ui,sans-serif">` +
      `图片附件不存在或已清理</text></svg>`,
  );

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
      img: ({ src, alt }: any) => (
        <Image
          src={src}
          alt={alt || ''}
          fallback={IMG_FALLBACK}
          style={{ maxWidth: 280, borderRadius: 8, margin: '4px 0' }}
          preview={{ mask: '点击预览' }}
        />
      ),
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
