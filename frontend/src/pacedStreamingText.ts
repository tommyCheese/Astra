import { useEffect, useRef, useState } from 'react';

function safeStreamingSlice(value: string, end: number) {
  let boundary = Math.min(value.length, end);
  const previous = value.charCodeAt(boundary - 1);
  if (previous >= 0xD800 && previous <= 0xDBFF) boundary += 1;
  return value.slice(0, boundary);
}

/** Keeps transport chunk size from becoming the visual update cadence. */
export function usePacedStreamingText(
  target: string,
  streamId: string | undefined,
  active = true,
  maximumCharactersPerFrame = 160,
) {
  const [visible, setVisible] = useState(target);
  const targetRef = useRef(target);
  const visibleRef = useRef(target);
  const frameRef = useRef<number>();
  const lastPaintRef = useRef(performance.now());
  const characterCreditRef = useRef(1);
  targetRef.current = target;

  useEffect(() => {
    if (!target || !active) {
      if (frameRef.current !== undefined) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = undefined;
      }
      visibleRef.current = target;
      setVisible(target);
      characterCreditRef.current = 1;
      return;
    }
    const reduceMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (reduceMotion) {
      if (frameRef.current !== undefined) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = undefined;
      }
      visibleRef.current = target;
      setVisible(target);
      return;
    }
    if (!target.startsWith(visibleRef.current)) {
      visibleRef.current = '';
      setVisible('');
      characterCreditRef.current = 1;
    }

    const paint = (now: number) => {
      frameRef.current = undefined;
      const nextTarget = targetRef.current;
      let current = visibleRef.current;
      if (!nextTarget.startsWith(current)) {
        current = '';
        visibleRef.current = '';
        setVisible('');
        characterCreditRef.current = 1;
      }

      const backlog = nextTarget.length - current.length;
      if (backlog > 0) {
        const elapsed = Math.min(80, Math.max(0, now - lastPaintRef.current));
        const charactersPerSecond = backlog > 600 ? 9000
          : backlog > 240 ? 4800
            : backlog > 80 ? 2400
              : backlog > 24 ? 1200
                : 720;
        characterCreditRef.current += elapsed * charactersPerSecond / 1000;
        const characterCount = Math.min(
          backlog,
          maximumCharactersPerFrame,
          Math.max(1, Math.floor(characterCreditRef.current)),
        );
        characterCreditRef.current = Math.max(0, characterCreditRef.current - characterCount);
        const nextVisible = safeStreamingSlice(nextTarget, current.length + characterCount);
        visibleRef.current = nextVisible;
        setVisible(nextVisible);
      }
      lastPaintRef.current = now;
      if (visibleRef.current !== targetRef.current) {
        frameRef.current = window.requestAnimationFrame(paint);
      }
    };

    if (frameRef.current === undefined) {
      lastPaintRef.current = performance.now();
      frameRef.current = window.requestAnimationFrame(paint);
    }
  }, [active, maximumCharactersPerFrame, streamId, target]);

  useEffect(() => () => {
    if (frameRef.current !== undefined) window.cancelAnimationFrame(frameRef.current);
  }, []);

  if (!active) return target;
  if (!target) return '';
  return target.startsWith(visible) && visible
    ? visible
    : safeStreamingSlice(target, 1);
}
