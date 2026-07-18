import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getSharedConversation } from './api';
import { I18nProvider, useI18n } from './i18n';
import type { SharedConversation } from './types';

export function SharedConversationPage({ token }: { token: string }) {
  return <I18nProvider><SharedConversationContent token={token} /></I18nProvider>;
}

function SharedConversationContent({ token }: { token: string }) {
  const { language, t } = useI18n();
  const [conversation, setConversation] = useState<SharedConversation | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setConversation(null);
    setMissing(false);
    void getSharedConversation(token, controller.signal)
      .then(setConversation)
      .catch((error) => {
        if (error?.name !== 'AbortError') setMissing(true);
      });
    return () => controller.abort();
  }, [token]);
  return <main className="shared-page">
    <header><div className="shared-brand"><img src="/astra.svg" alt="Astra" /><strong>Astra</strong></div><span>{t('共享的对话')}</span></header>
    {missing ? <section className="shared-empty"><h1>{t('分享链接不可用')}</h1><p>{t('该链接不存在、已停止分享，或原对话已被删除。')}</p></section> : conversation ? <article className="shared-conversation"><h1>{conversation.title}</h1><p className="shared-meta">{t('只读快照 · 更新于 {time}').replace('{time}', new Date(conversation.updated_at).toLocaleString(language))}</p>{conversation.messages.map((message, index) => message.role === 'process' ? <details className="shared-process" key={`process-${index}`}><summary><span className="shared-process-icon">✦</span><strong>{t('思考过程')}</strong><small>{t('{count} 个步骤').replace('{count}', String(message.items.length))}</small></summary><div className="shared-process-timeline">{message.items.map((item, itemIndex) => <div className={`shared-process-item ${item.kind} ${item.status}`} key={`${item.kind}-${itemIndex}`}><span className="shared-process-dot" /><div><strong>{t(item.title)}</strong>{item.detail && <p>{item.detail}</p>}<small>{t(item.status === 'failed' ? '失败' : item.status === 'cancelled' ? '已终止' : '已完成')}</small></div></div>)}</div></details> : <section className={`shared-message ${message.role}`} key={`${message.role}-${index}`}><span>{message.role === 'user' ? t('用户') : 'Astra'}</span><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></section>)}</article> : <section className="shared-empty"><p>{t('正在加载共享对话…')}</p></section>}
  </main>;
}
