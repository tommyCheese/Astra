import { useState } from 'react';
import { useI18n } from '../../i18n';
import './floating-ad.css';

export type FloatingAdProps = {
  id: string;
  imageSrc: string;
  imageAlt: string;
  href?: string;
};

const storageKey = (id: string) => `astra.ad.dismissed.${id}`;

function wasDismissed(id: string) {
  try {
    return window.localStorage.getItem(storageKey(id)) === 'true';
  } catch {
    return false;
  }
}

export function FloatingAd({ id, imageSrc, imageAlt, href }: FloatingAdProps) {
  const { t } = useI18n();
  const [visible, setVisible] = useState(() => !wasDismissed(id));

  if (!visible) return null;

  const poster = <img className="floating-ad__poster" src={imageSrc} alt={imageAlt} />;

  function dismiss() {
    setVisible(false);
    try {
      window.localStorage.setItem(storageKey(id), 'true');
    } catch {
      // Closing still works when storage is unavailable.
    }
  }

  return (
    <aside className="floating-ad" aria-label={t('推广内容')}>
      <span className="floating-ad__label">{t('广告')}</span>
      <button className="floating-ad__close" type="button" onClick={dismiss} aria-label={t('关闭广告')} title={t('关闭广告')}>
        <span aria-hidden="true">×</span>
      </button>
      {href ? <a href={href} target="_blank" rel="noreferrer">{poster}</a> : poster}
    </aside>
  );
}
