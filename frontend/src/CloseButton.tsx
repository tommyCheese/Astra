type CloseButtonProps = {
  label: string;
  onClick: () => void;
  className?: string;
  title?: string;
};

export function CloseButton({ label, onClick, className = '', title }: CloseButtonProps) {
  return <button
    className={`ui-close-button ${className}`.trim()}
    type="button"
    aria-label={label}
    title={title}
    onClick={onClick}
  >
    <span aria-hidden="true" />
  </button>;
}
