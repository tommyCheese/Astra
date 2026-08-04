import { useI18n } from './i18n';
import type { ProcessStreamItem } from './processStream';

export function ModelThinkingContent({ item }: { item: ProcessStreamItem }) {
  const { t } = useI18n();
  const metadata = [item.provider, item.operation].filter(Boolean).join(' · ');
  const unavailableText = item.unavailableReason === 'model_request_failed'
    ? t('模型调用失败，未获得可展示的思考内容。')
    : t('该模型未公开可展示的思考内容。');
  return <details className="model-thinking-content" open={item.status === 'running'}>
    <summary>{metadata || t('供应商返回内容')}</summary>
    {item.contentLevel === 'unavailable'
      ? <p>{unavailableText}</p>
      : <pre>{item.detail}</pre>}
    {item.truncated && <p className="model-thinking-warning">{t('内容超过保存上限，以下记录已被截断。')}</p>}
  </details>;
}
