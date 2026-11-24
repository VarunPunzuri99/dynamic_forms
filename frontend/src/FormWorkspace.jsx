import { useEffect, useState } from "react";
import { BusyOverlay, ButtonLabel, PageLoader, StatusBanner } from "./LoadingUI";

function titleCaseForm(formId, displayName) {
  if (displayName && displayName !== formId) return displayName;
  if (formId === "fw4") return "Federal Form W-4";
  if (!formId) return "Form";
  return formId
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function statusTone(edition) {
  if (edition?.is_live) return "live";
  const status = String(edition?.status || "").toLowerCase();
  if (status === "approved") return "approved";
  if (status === "draft") return "draft";
  return "muted";
}

export default function FormWorkspace({ formId }) {
  const [pack, setPack] = useState(null);
  const [forms, setForms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyAction, setBusyAction] = useState("");
  /** editionId → last successful import count (editions list often lags / shows approved-only). */
  const [importedByEdition, setImportedByEdition] = useState({});

  async function reload() {
    const [formsResult, editionsResult] = await Promise.all([
      fetch("/api/forms").then((r) => r.json().then((body) => ({ ok: r.ok, body }))),
      fetch(`/api/forms/${formId}/editions`).then((r) =>
        r.json().then((body) => ({ ok: r.ok, body }))
      ),
    ]);
    if (formsResult.ok && Array.isArray(formsResult.body)) {
      setForms(formsResult.body);
    }
    if (!editionsResult.ok) {
      throw new Error(
        editionsResult.body?.errors?.[0]?.message || "Editions could not be loaded"
      );
    }
    setPack(editionsResult.body);
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    reload()
      .catch((failure) => {
        if (!cancelled) setError(failure.message || "Could not open form");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [formId]);

  async function importFields(editionId, replace = false) {
    setBusyAction("import");
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        `/api/forms/${formId}/editions/${editionId}/import-fields`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ replace }),
        }
      );
      const body = await response.json();
      // 409 = questions already exist (import already done). Not a hard failure.
      if (response.status === 409) {
        const raw = body?.errors?.[0]?.message || "";
        const match = raw.match(/(\d+)\s*question/i);
        const existing = match ? match[1] : "some";
        setImportedByEdition((prev) => ({ ...prev, [editionId]: existing }));
        setMessage(
          `Import already done for edition ${editionId}: ${existing} question(s) are already saved. You do not need to import again. Next step: review/approve those questions, then Publish to make the form live. Only use "Re-import (replace)" if you want to wipe and extract again from the PDF.`
        );
        await reload();
        return;
      }
      if (!response.ok) {
        throw new Error(body?.errors?.[0]?.message || "Import fields failed");
      }
      const count = body.importedCount ?? body.ImportedCount ?? "?";
      const edId = body.editionId ?? body.EditionId ?? editionId;
      setImportedByEdition((prev) => ({ ...prev, [edId]: count }));
      setMessage(
        `Success: ${count} fields were extracted from the PDF and saved on edition ${edId} as needs_review (still draft — not live yet). Next: approve questions, then Publish to make it live for the mobile app.`
      );
      await reload();
    } catch (failure) {
      setError(failure.message || "Import fields failed");
    } finally {
      setBusyAction("");
    }
  }

  if (loading) {
    return <PageLoader title="Opening form" detail={`Loading ${formId} editions…`} />;
  }

  const listed = forms.find((row) => row.form_id === formId);
  const name = titleCaseForm(formId, listed?.display_name);
  const live = pack?.live || listed?.live || null;
  const editions = [...(pack?.editions || [])].sort((a, b) =>
    String(b.edition || "").localeCompare(String(a.edition || ""))
  );
  const bootstrapEdition =
    !live && editions.length === 1
      ? editions[0]
      : editions.find((row) => !row.is_live && String(row.status).toLowerCase() === "draft") ||
        null;

  return (
    <main className="page page-workspace">
      {busyAction ? (
        <BusyOverlay
          label={
            busyAction === "import"
              ? "Importing fields from PDF…"
              : "Publishing edition…"
          }
        />
      ) : null}

      <div className="workspace-head">
        <a className="back-link" href="#home">
          ← All forms
        </a>
        <div className="page-head page-head-compact">
          <div className="page-head-copy">
            <p className="eyebrow">Form workspace</p>
            <h1>
              {name}
              {live ? <span className="year-pill">Live {live.edition}</span> : null}
            </h1>
            <p className="lede">
              {live
                ? `Choose an action for ${formId}. Compare another year, review fields, or fill the live form.`
                : `No live edition yet. Import fields from the first PDF, review them, then publish to make it live for RN.`}
            </p>
          </div>
        </div>
      </div>

      {error ? <StatusBanner tone="danger">{error}</StatusBanner> : null}
      {message ? <p className="note note-success">{message}</p> : null}

      {!live && bootstrapEdition ? (
        <div className="workspace-bootstrap panel-surface">
          <div className="panel-head">
            <h2>First-year bootstrap</h2>
            <p className="panel-sub">
              Edition {bootstrapEdition.edition} (id {bootstrapEdition.id}) ·{" "}
              {bootstrapEdition.status}
            </p>
          </div>
          <div className="catalog-actions" style={{ padding: "0 4px 12px" }}>
            <button
              type="button"
              className="button-link primary"
              disabled={Boolean(busyAction)}
              onClick={() => importFields(bootstrapEdition.id, false)}
            >
              <ButtonLabel busy={busyAction === "import"} busyText="Importing…">
                Import fields from PDF
              </ButtonLabel>
            </button>
            <button
              type="button"
              className="button-link secondary"
              disabled={Boolean(busyAction)}
              onClick={() => {
                if (
                  window.confirm(
                    "Replace existing questions on this edition with a fresh PDF extract?"
                  )
                ) {
                  importFields(bootstrapEdition.id, true);
                }
              }}
            >
              Re-import (replace)
            </button>
            <a className="button-link ghost" href={`#fields/${formId}`}>
              Review fields
            </a>
          </div>
        </div>
      ) : null}

      <div className="workspace-stats">
        <div className="stat">
          <span className="stat-label">Live edition</span>
          <strong>{live?.edition || "None"}</strong>
        </div>
        <div className="stat">
          <span className="stat-label">Live questions</span>
          <strong>{live?.question_count ?? "—"}</strong>
        </div>
        <div className="stat">
          <span className="stat-label">Years on file</span>
          <strong>{editions.length}</strong>
        </div>
      </div>

      <div className="workspace-actions">
        <a className="workspace-action primary" href={`#compare/${formId}`}>
          <span className="workspace-action-kicker">Reviewer</span>
          <strong>Compare years</strong>
          <span>Diff another edition against live, save draft, then publish.</span>
        </a>
        <a className="workspace-action" href={`#fields/${formId}`}>
          <span className="workspace-action-kicker">Reviewer</span>
          <strong>Review fields</strong>
          <span>
            {live
              ? "Approve wording and rules on the live edition."
              : "Approve imported draft questions, then publish to make them live."}
          </span>
        </a>
        <a className="workspace-action" href={`#form/${formId}`}>
          <span className="workspace-action-kicker">Employee path</span>
          <strong>Fill live form</strong>
          <span>Answer live questions and create a filled PDF preview.</span>
        </a>
      </div>

      <section className="workspace-editions panel-surface">
        <div className="panel-head">
          <h2>Editions / years</h2>
          <p className="panel-sub">Import fields on a draft, then publish when ready</p>
        </div>
        {editions.length === 0 ? (
          <p className="lede">No editions found for this form.</p>
        ) : (
          <ul className="edition-table">
            {editions.map((edition) => (
              <li key={edition.id} className={`edition-row tone-${statusTone(edition)}`}>
                <div>
                  <strong className="edition-year">{edition.edition || "—"}</strong>
                  <span className="edition-id">id {edition.id}</span>
                </div>
                <span className={`year-chip tone-${statusTone(edition)}`}>
                  {edition.is_live ? "Live" : edition.status || "—"}
                </span>
                <span className="edition-count">
                  {importedByEdition[edition.id] != null
                    ? `${importedByEdition[edition.id]} imported (needs review)`
                    : `${edition.approved_count ?? edition.question_count ?? 0} approved`}
                </span>
                <div className="catalog-actions">
                  <button
                    type="button"
                    className="button-link secondary"
                    disabled={Boolean(busyAction)}
                    onClick={() => importFields(edition.id, false)}
                  >
                    Import
                  </button>
                  {live ? (
                    <a className="edition-compare" href={`#compare/${formId}`}>
                      Compare
                    </a>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
