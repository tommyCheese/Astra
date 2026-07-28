import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import './styles.css';

const shareMatch = window.location.pathname.match(/^\/share\/([^/]+)\/?$/);
const SharedConversationPage = shareMatch
  ? React.lazy(() => import('./SharedConversationPage').then((module) => ({
    default: module.SharedConversationPage,
  })))
  : null;
const graphVerification = import.meta.env.DEV && window.location.pathname === '/__dev/complex-dag';
const graphPaneVerification = import.meta.env.DEV && window.location.pathname === '/__dev/complex-dag-pane';
const ComplexDagVerificationPage = graphVerification
  ? React.lazy(() => import('./dev/ComplexDagVerificationPage'))
  : null;
const ComplexDagPaneVerificationPage = graphPaneVerification
  ? React.lazy(() => import('./dev/ComplexDagPaneVerificationPage'))
  : null;

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {ComplexDagPaneVerificationPage
      ? <React.Suspense fallback={null}><ComplexDagPaneVerificationPage /></React.Suspense>
      : ComplexDagVerificationPage
      ? <React.Suspense fallback={null}><ComplexDagVerificationPage /></React.Suspense>
      : shareMatch && SharedConversationPage
        ? <React.Suspense fallback={null}><SharedConversationPage token={decodeURIComponent(shareMatch[1])} /></React.Suspense>
        : <App />}
  </React.StrictMode>,
);
