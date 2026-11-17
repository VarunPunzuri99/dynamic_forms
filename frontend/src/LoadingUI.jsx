export function Spinner({ size = "md", label }) {
  return (
    <span className={`spinner spinner-${size}`} role="status" aria-label={label || "Loading"}>
      <span className="spinner-ring" aria-hidden="true" />
      {label ? <span className="spinner-label">{label}</span> : null}
    </span>
  );
}

export function StatusBanner({ tone = "info", children }) {
  return (
    <div className={`status-banner status-${tone}`} role="status" aria-live="polite">
      <Spinner size="sm" />
      <div className="status-banner-copy">{children}</div>
    </div>
  );
}

export function PanelLoader({ title, detail }) {
  return (
    <div className="panel-loader" role="status" aria-live="polite">
      <Spinner size="lg" />
      <div>
        <p className="panel-loader-title">{title}</p>
        {detail ? <p className="panel-loader-detail">{detail}</p> : null}
      </div>
    </div>
  );
}

export function PageLoader({ title, detail }) {
  return (
    <div className="page-loader" role="status" aria-live="polite">
      <div className="page-loader-card">
        <Spinner size="lg" />
        <div>
          <p className="panel-loader-title">{title}</p>
          {detail ? <p className="panel-loader-detail">{detail}</p> : null}
        </div>
        <div className="progress-track" aria-hidden="true">
          <span className="progress-bar" />
        </div>
      </div>
    </div>
  );
}

export function BusyOverlay({ active, label }) {
  if (!active) {
    return null;
  }
  return (
    <div className="busy-overlay" role="status" aria-live="assertive">
      <div className="busy-overlay-card">
        <Spinner size="md" />
        <p>{label}</p>
      </div>
    </div>
  );
}

export function ButtonLabel({ busy, busyText, children }) {
  if (!busy) {
    return children;
  }
  return (
    <span className="btn-busy">
      <Spinner size="xs" />
      <span>{busyText || children}</span>
    </span>
  );
}
