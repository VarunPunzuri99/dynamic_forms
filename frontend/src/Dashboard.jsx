import { useCallback, useEffect, useMemo, useState } from "react";
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

function statusLabel(edition) {
  if (edition?.is_live) return "Live";
  const status = String(edition?.status || "unknown");
  return status.charAt(0).toUpperCase() + status.slice(1);
}

const FORM_ID_RE = /^[a-z0-9][a-z0-9_-]{1,63}$/;

export default function Dashboard() {
  const [forms, setForms] = useState([]);
  const [packs, setPacks] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyLabel, setBusyLabel] = useState("");
  const [newFormId, setNewFormId] = useState("");
  const [newEdition, setNewEdition] = useState("");
  const [newFile, setNewFile] = useState(null);

  const loadCatalog = useCallback(async () => {
    const response = await fetch("/api/forms");
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body?.errors?.[0]?.message || "Forms could not be loaded");
    }
    const list = Array.isArray(body) ? body : [];
    setForms(list);
    setBusyLabel("Loading editions…");
    const entries = await Promise.all(
      list.map(async (form) => {
        try {
          const edRes = await fetch(`/api/forms/${form.form_id}/editions`);
          const pack = await edRes.json();
          if (!edRes.ok) {
            return [
              form.form_id,
              { editions: [], live: form.live || null, error: pack?.errors?.[0]?.message },
            ];
          }
          return [form.form_id, pack];
        } catch (failure) {
          return [form.form_id, { editions: [], live: form.live || null, error: failure.message }];
        }
      })
    );
    setPacks(Object.fromEntries(entries));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    loadCatalog()
      .catch((failure) => {
        if (!cancelled) setError(failure.message || "Forms could not be loaded");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
          setBusyLabel("");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loadCatalog]);

  async function createEdition(event) {
    event.preventDefault();
    const formId = newFormId.trim().toLowerCase();
    const edition = newEdition.trim();
    if (!FORM_ID_RE.test(formId)) {
      setError("Form id must be lowercase letters, numbers, _ or - (2–64 chars).");
      return;
    }
    if (!edition) {
      setError("Edition / year is required (e.g. 2027).");
      return;
    }
    if (!newFile) {
      setError("Choose a blank fillable PDF.");
      return;
    }

    setBusyLabel("Uploading blank PDF…");
    setError("");
    setMessage("");
    try {
      const data = new FormData();
      data.append("edition", edition);
      data.append("file", newFile, newFile.name || `${formId}_${edition}.pdf`);
      const response = await fetch(`/api/forms/${encodeURIComponent(formId)}/editions`, {
        method: "POST",
        body: data,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body?.errors?.[0]?.message || "Could not create edition");
      }
      const editionId = body.editionId ?? body.EditionId ?? body.edition_id;
      setMessage(
        `Draft ${formId} / ${edition} created (id ${editionId}). Open the form and click Import fields from PDF.`
      );
      setNewFormId("");
      setNewEdition("");
      setNewFile(null);
      await loadCatalog();
      window.location.hash = `#workspace/${formId}`;
    } catch (failure) {
      setError(failure.message || "Could not create edition");
    } finally {
      setBusyLabel("");
    }
  }

  const stats = useMemo(() => {
    const liveCount = forms.filter((form) => form.live).length;
    const editionCount = Object.values(packs).reduce(
      (sum, pack) => sum + (pack?.editions?.length || 0),
      0
    );
    return {
      forms: forms.length,
      live: liveCount,
      editions: editionCount,
    };
  }, [forms, packs]);

  const rows = useMemo(
    () =>
      forms.map((form) => {
        const pack = packs[form.form_id] || {};
        const editions = Array.isArray(pack.editions) ? pack.editions : [];
        const live = pack.live || form.live || null;
        const sorted = [...editions].sort((a, b) =>
          String(b.edition || "").localeCompare(String(a.edition || ""))
        );
        const liveEdition = sorted.find((edition) => edition.is_live) || live;
        const approvedCount =
          liveEdition?.approved_count ??
          live?.approved_count ??
          live?.question_count ??
          null;
        return {
          form,
          pack,
          live,
          sorted,
          approvedCount,
          name: titleCaseForm(form.form_id, form.display_name),
        };
      }),
    [forms, packs]
  );

  if (loading && !forms.length) {
    return <PageLoader title="Loading forms" detail="Fetching live editions and year PDFs…" />;
  }

  return (
    <main className="page page-dashboard">
      {busyLabel ? <BusyOverlay label={busyLabel} /> : null}

      <div className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <p className="eyebrow">Forms catalog</p>
          <h1>All forms &amp; years</h1>
          <p className="lede">
            Open a form to fill the live edition, compare another year, or review fields.
            Add a new form or year by uploading a blank PDF (registers on first draft edition).
          </p>
        </div>
        <div className="dashboard-hero-stats" aria-label="Catalog summary">
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.forms}</span>
            <span className="dash-stat-label">Forms</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.live}</span>
            <span className="dash-stat-label">Live</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.editions}</span>
            <span className="dash-stat-label">Editions</span>
          </div>
        </div>
      </div>

      {error ? <StatusBanner tone="danger">{error}</StatusBanner> : null}
      {message ? <p className="note note-success">{message}</p> : null}

      <section className="panel-surface" style={{ marginBottom: 18 }}>
        <div className="panel-head">
          <h2>Add form / year</h2>
          <p className="panel-sub">
            Creates a draft edition + blank PDF in Blob. Does not extract fields — import next.
          </p>
        </div>
        <form className="upload-edition-form" onSubmit={createEdition}>
          <div className="upload-edition-fields">
            <label>
              Form id
              <input
                type="text"
                value={newFormId}
                onChange={(event) => setNewFormId(event.target.value)}
                placeholder="e.g. demo_tax"
                autoComplete="off"
                disabled={Boolean(busyLabel)}
              />
            </label>
            <label>
              Edition / year
              <input
                type="text"
                value={newEdition}
                onChange={(event) => setNewEdition(event.target.value)}
                placeholder="e.g. 2027"
                autoComplete="off"
                disabled={Boolean(busyLabel)}
              />
            </label>
            <label>
              Blank PDF
              <input
                type="file"
                accept="application/pdf,.pdf"
                onChange={(event) => setNewFile(event.target.files?.[0] || null)}
                disabled={Boolean(busyLabel)}
              />
            </label>
          </div>
          <div className="upload-edition-actions">
            <button type="submit" className="button-link primary" disabled={Boolean(busyLabel)}>
              <ButtonLabel busy={Boolean(busyLabel)} busyText="Uploading…">
                Upload draft edition
              </ButtonLabel>
            </button>
          </div>
        </form>
      </section>

      {!forms.length && !error ? (
        <div className="dashboard-empty panel-surface">
          <h2>No forms yet</h2>
          <p className="lede">
            Upload a blank PDF above to register the first form, or wait until FormsEngine lists
            existing forms (for example fw4 or colorado).
          </p>
        </div>
      ) : (
        <div className="catalog-table-wrap panel-surface">
          <div className="catalog-table-toolbar">
            <div>
              <h2>Forms</h2>
              <p className="panel-sub">Live editions and year history</p>
            </div>
          </div>
          <div className="catalog-table-scroll">
            <table className="catalog-table">
              <thead>
                <tr>
                  <th scope="col">Form</th>
                  <th scope="col">Live</th>
                  <th scope="col">Approved</th>
                  <th scope="col">Years</th>
                  <th scope="col">Editions</th>
                  <th scope="col" className="col-actions">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ form, pack, live, sorted, approvedCount, name }) => (
                  <tr key={form.form_id}>
                    <td>
                      <div className="catalog-form-cell">
                        <span className="form-card-mark" aria-hidden="true">
                          {(form.form_id || "?").slice(0, 2).toUpperCase()}
                        </span>
                        <div>
                          <a className="catalog-form-name" href={`#workspace/${form.form_id}`}>
                            {name}
                          </a>
                          <div className="catalog-form-id">{form.form_id}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      {live ? (
                        <span className="year-pill">{live.edition}</span>
                      ) : (
                        <span className="badge form-card-nolive">—</span>
                      )}
                    </td>
                    <td
                      className="catalog-num"
                      title="Approved questions on the live edition"
                    >
                      {approvedCount ?? "—"}
                    </td>
                    <td className="catalog-num">{sorted.length || "—"}</td>
                    <td>
                      {pack.error ? (
                        <span className="form-card-years-error">{pack.error}</span>
                      ) : sorted.length === 0 ? (
                        <span className="catalog-muted">—</span>
                      ) : (
                        <ul className="year-chip-list catalog-editions">
                          {sorted.map((edition) => (
                            <li key={edition.id}>
                              <span
                                className={`year-chip tone-${statusTone(edition)}`}
                                title={`${statusLabel(edition)} · ${edition.approved_count ?? edition.question_count ?? 0} questions`}
                              >
                                <span className="year-chip-year">{edition.edition || "—"}</span>
                                <span className="year-chip-status">{statusLabel(edition)}</span>
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="col-actions">
                      <div className="catalog-actions">
                        <a className="button-link primary" href={`#workspace/${form.form_id}`}>
                          Open
                        </a>
                        <a className="button-link secondary" href={`#compare/${form.form_id}`}>
                          Compare
                        </a>
                        <a className="button-link ghost" href={`#form/${form.form_id}`}>
                          Fill
                        </a>
                        <a className="button-link ghost" href={`#fields/${form.form_id}`}>
                          Review
                        </a>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}
