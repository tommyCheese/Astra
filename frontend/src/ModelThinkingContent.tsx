import { useLayoutEffect, useRef, useState } from 'react';
import { useI18n } from './i18n';
import type { ProcessStreamItem } from './processStream';

export function ModelThinkingContent({ item }: { item: ProcessStreamItem }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(item.status === 'running');
  const contentRef = useRef<HTMLPreElement>(null);
  const scrollFrameRef = useRef<number>();
  const metadata = [item.provider, item.operation].filter(Boolean).join(' · ');
  const unavailableText = item.unavailableReason === 'model_request_failed'
    ? t('模型调用失败，未获得可展示的思考内容。')
    : t('该模型未公开可展示的思考内容。');

  useLayoutEffect(() => {
    if (!expanded || item.contentLevel === 'unavailable') return undefined;
    if (scrollFrameRef.current !== undefined) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      const content = contentRef.current;
      if (content) content.scrollTop = content.scrollHeight;
      scrollFrameRef.current = undefined;
    });
    return () => {
      if (scrollFrameRef.current !== undefined) window.cancelAnimationFrame(scrollFrameRef.current);
    };
  }, [expanded, item.contentLevel, item.detail]);

  const statusLabel = item.status === 'running' ? t('运行中') : t('已完成');
  return <details
    className={`model-thinking-content status-${item.status}`}
    open={expanded}
    onToggle={(event) => setExpanded(event.currentTarget.open)}
  >
    <summary aria-expanded={expanded}>
      <span className="model-thinking-summary-main">
        <span className="model-thinking-signal" aria-hidden="true"><i /></span>
        <span className="model-thinking-meta">{metadata || t('供应商返回内容')}</span>
      </span>
      <span className="model-thinking-state">{statusLabel}</span>
      <span className="model-thinking-chevron" aria-hidden="true" />
    </summary>
    <div className="model-thinking-body">
      {item.contentLevel === 'unavailable'
        ? <p className="model-thinking-unavailable">{unavailableText}</p>
        : <pre ref={contentRef} data-follow-latest={expanded ? 'true' : 'false'}>{item.detail}{item.status === 'running' && <span className="model-thinking-caret" aria-hidden="true" />}</pre>}
      {item.truncated && <p className="model-thinking-warning">{t('内容超过保存上限，以下记录已被截断。')}</p>}
    </div>
  </details>;
}
