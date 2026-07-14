import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getSharedConversation } from './api';
import type { SharedConversation } from './types';

export function SharedConversationPage({ token }: { token: string }) {
  const [conversation, setConversation] = useState<SharedConversation | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    void getSharedConversation(token).then(setConversation).catch(() => setMissing(true));
  }, [token]);
  return <main className="shared-page">
    <header><div className="shared-brand"><img src="/astra.svg" alt="Astra" /><strong>Astra</strong></div><span>共享的对话</span></header>
    {missing ? <section className="shared-empty"><h1>分享链接不可用</h1><p>该链接不存在、已停止分享，或原对话已被删除。</p></section> : conversation ? <article className="shared-conversation"><h1>{conversation.title}</h1><p className="shared-meta">只读快照 · 更新于 {new Date(conversation.updated_at).toLocaleString()}</p>{conversation.messages.map((message, index) => <section className={`shared-message ${message.role}`} key={`${message.role}-${index}`}><span>{message.role === 'user' ? '用户' : 'Astra'}</span><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></section>)}</article> : <section className="shared-empty"><p>正在加载共享对话…</p></section>}
  </main>;
}
