import { useEffect, useState } from "react";
import { PanelLoader } from "./LoadingUI";

function errorMessage(body, fallback) {
  return body?.errors?.[0]?.message || fallback;
}

export function fieldIdentity(row) {
  return row.field_leaf || row.field_name || row.pdf_field || row.question_key || "unknown";
}

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.5;
const ZOOM_STEP = 0.25;

export default function FieldPreview({ formId, editionId, row }) {
  const [imageUrl, setImageUrl] = useState("");
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    if (!formId || !editionId || !row?.question_key) {
      setImageUrl("");
      setMeta(null);
      setError("");
      setZoom(1);
      return undefined;
    }
    let cancelled = false;
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setImageUrl("");
    setMeta(null);
    setZoom(1);
    const params = new URLSearchParams({ question_key: row.question_key });
    fetch(`/api/forms/${formId}/editions/${editionId}/field-highlight?${params}`, {
      signal: controller.signal,
    })
      .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
      .then(({ ok, body }) => {
        if (cancelled) {
          return;
        }
        if (!ok) {
          throw new Error(errorMessage(body, "That PDF box could not be highlighted"));
        }
        setMeta(body);
        setImageUrl(`data:image/png;base64,${body.png_base64}`);
      })
      .catch((failure) => {
        if (!cancelled && failure.name !== "AbortError") {
          setError(failure.message || "The PDF preview could not be loaded");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBusy(false);
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [formId, editionId, row?.question_key]);

  function bumpZoom(delta) {
    setZoom((current) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number((current + delta).toFixed(2)))));
  }

  return (
    <aside className="field-preview">
      <div className="field-preview-head">
        <p className="rules-eyebrow">PDF location</p>
        <h2>Confirm this box</h2>
        {row ? (
          <p className="lede">
            Selected row maps to <strong>{fieldIdentity(row)}</strong>
            {row.position_label ? ` · ${row.position_label}` : ""}. Rewrite the sentence to match what this highlighted box means on the form.
          </p>
        ) : (
          <p className="lede">Click a question row to see its box highlighted on the blank PDF.</p>
        )}
      </div>
      {!row ? <div className="field-preview-empty">No row selected</div> : null}
      {busy ? (
        <PanelLoader title="Loading highlight" detail="Rendering the selected PDF box…" />
      ) : null}
      {error ? <p className="error-banner">{error}</p> : null}
      {meta ? (
        <p className="note">
          {meta.storage_path} · page {meta.page_number} · field <code>{meta.field_name}</code>
        </p>
      ) : null}
      {imageUrl ? (
        <div className="field-preview-viewer">
          <div className="field-preview-toolbar" role="group" aria-label="Preview zoom">
            <button type="button" className="secondary zoom-btn" onClick={() => bumpZoom(-ZOOM_STEP)} disabled={zoom <= ZOOM_MIN}>
              Zoom out
            </button>
            <span className="zoom-label">{Math.round(zoom * 100)}%</span>
            <button type="button" className="secondary zoom-btn" onClick={() => bumpZoom(ZOOM_STEP)} disabled={zoom >= ZOOM_MAX}>
              Zoom in
            </button>
            <button type="button" className="secondary zoom-btn" onClick={() => setZoom(1)}>
              Reset
            </button>
          </div>
          <div className="field-preview-stage">
            <img
              className="field-preview-image"
              src={imageUrl}
              alt={`Highlighted PDF field ${fieldIdentity(row)}`}
              style={{ width: `${zoom * 100}%` }}
            />
          </div>
          <p className="field-preview-hint">Scroll inside the preview to pan. Use Zoom out if the box feels too close.</p>
        </div>
      ) : null}
    </aside>
  );
}

export function ReviewFieldRow({
  row,
  selected,
  onSelect,
  onIncludeChange,
  onLabelChange,
  includeLabel,
}) {
  return (
    <div
      className={selected ? "review-row-stack is-selected" : "review-row-stack"}
      onClick={() => onSelect(row)}
      onFocusCapture={() => onSelect(row)}
    >
      <div className="review-row">
        <input
          type="checkbox"
          checked={row.include}
          aria-label={includeLabel || `Include ${row.question_key}`}
          onChange={(event) => onIncludeChange(row.question_key, event.target.checked)}
          onClick={(event) => event.stopPropagation()}
        />
        <span className="badge">{row.change_type}</span>
        <div className="review-meta-block">
          <span className="review-meta">
            {row.position_label || `p${row.page_number} ${row.input_type}`}
            {row.label_source === "carried" ? " · reused wording" : ""}
          </span>
          <code className="field-id" title={row.field_name || row.question_key}>
            {fieldIdentity(row)}
          </code>
        </div>
        <textarea
          value={row.label}
          aria-label={`Wording for ${row.question_key}`}
          onChange={(event) => onLabelChange(row.question_key, event.target.value)}
          onFocus={() => onSelect(row)}
        />
      </div>
      {row.field_name ? (
        <p className="note field-path-note">
          PDF field: <code>{row.field_name}</code>
        </p>
      ) : null}
      {row.label_source === "carried" && row.prior_label ? (
        <p className={row.label_conflict ? "note warning-note" : "note"}>
          {row.label_conflict
            ? "This field name matched last year, but the new PDF nearby text looks different. Confirm the wording."
            : "Reused from the live form. Nearby PDF text was:"}{" "}
          <span className="prior-label">{row.prior_label}</span>
        </p>
      ) : null}
    </div>
  );
}
