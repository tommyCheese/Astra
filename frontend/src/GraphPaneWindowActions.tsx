type GraphPaneWindowActionsProps = {
  expanded: boolean;
  expandLabel: string;
  restoreLabel: string;
  closeLabel: string;
  onExpandedChange: (expanded: boolean) => void;
  onClose: () => void;
};

export function GraphPaneWindowActions({
  expanded,
  expandLabel,
  restoreLabel,
  closeLabel,
  onExpandedChange,
  onClose,
}: GraphPaneWindowActionsProps) {
  const toggleLabel = expanded ? restoreLabel : expandLabel;
  return <div className="trusted-graph-pane-window-actions">
    <button
      className="trusted-graph-pane-expand"
      type="button"
      aria-label={toggleLabel}
      aria-pressed={expanded}
      title={toggleLabel}
      onClick={() => onExpandedChange(!expanded)}
    >
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        {expanded
          ? <><path d="M8 3v5H3M12 17v-5h5" /><path d="M3.5 8 8 3.5M16.5 12 12 16.5" /></>
          : <><path d="M7 3H3v4M13 17h4v-4" /><path d="m3.5 6.5 4-4M16.5 13.5l-4 4" /></>}
      </svg>
    </button>
    <button className="trusted-graph-pane-collapse" type="button" aria-label={closeLabel} title={closeLabel} onClick={onClose}>×</button>
  </div>;
}
