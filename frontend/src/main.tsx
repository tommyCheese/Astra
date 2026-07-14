import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { SharedConversationPage } from './SharedConversationPage';
import './styles.css';

const shareMatch = window.location.pathname.match(/^\/share\/([^/]+)\/?$/);

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {shareMatch ? <SharedConversationPage token={decodeURIComponent(shareMatch[1])} /> : <App />}
  </React.StrictMode>,
);
