import { useEffect, useState } from "react";
import Compare from "./Compare";
import EmployeeForm from "./EmployeeForm";
import Fields from "./Fields";
import { Spinner } from "./LoadingUI";

function readRoute() {
  const raw = window.location.hash.replace(/^#/, "");
  const [screen, formId] = raw.split("/");
  const id = formId || "fw4";

  // Old W-4-only bookmark (#review) → dynamic Review fields for every form.
  if (screen === "review") {
    return { screen: "fields", formId: formId || "fw4", redirectTo: `#fields/${formId || "fw4"}` };
  }
  if (screen === "compare") {
    return { screen: "compare", formId: id };
  }
  if (screen === "fields") {
    return { screen: "fields", formId: id };
  }
  if (screen === "form" && formId) {
    return { screen: "employee", formId };
  }
  return { screen: "employee", formId: "fw4" };
}

export default function App() {
  const [route, setRoute] = useState(readRoute);
  const [forms, setForms] = useState([]);
  const [formsLoading, setFormsLoading] = useState(true);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (route.redirectTo && window.location.hash !== route.redirectTo) {
      window.location.hash = route.redirectTo;
    }
  }, [route.redirectTo]);

  useEffect(() => {
    setFormsLoading(true);
    fetch("/api/forms")
      .then((response) => response.json())
      .then((body) => {
        if (Array.isArray(body)) {
          setForms(body);
        }
      })
      .catch(() => setForms([]))
      .finally(() => setFormsLoading(false));
  }, []);

  const formId = route.formId || "fw4";

  function chooseForm(nextFormId) {
    const screen =
      route.screen === "compare" ? "compare" : route.screen === "fields" ? "fields" : "form";
    window.location.hash = `${screen}/${nextFormId}`;
  }

  const screen = route.screen === "employee" ? "form" : route.screen;
  const selectedForm = forms.find((form) => form.form_id === formId);

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <div className="brand-block">
            <span className="brand-mark" aria-hidden="true" />
            <div className="brand-copy">
              <span className="brand">Forms Studio</span>
              <span className="brand-sub">Review · Approve · Fill</span>
            </div>
          </div>

          <nav className="topnav" aria-label="Primary">
            <a className={screen === "form" ? "active" : ""} href={`#form/${formId}`}>
              Fill form
            </a>
            <a className={screen === "compare" ? "active" : ""} href={`#compare/${formId}`}>
              Compare years
            </a>
            <a className={screen === "fields" ? "active" : ""} href={`#fields/${formId}`}>
              Review fields
            </a>
          </nav>

          <label className="form-picker">
            <span className="form-picker-label">Active form</span>
            {formsLoading ? <Spinner size="xs" /> : null}
            <select
              className="compare-select"
              value={formId}
              disabled={formsLoading}
              onChange={(event) => chooseForm(event.target.value)}
            >
              {forms.length === 0 ? <option value={formId}>{formId}</option> : null}
              {forms.map((form) => (
                <option key={form.form_id} value={form.form_id}>
                  {form.display_name || form.form_id}
                  {form.live ? ` · ${form.live.edition} · ${form.live.question_count} questions` : ""}
                </option>
              ))}
            </select>
            {selectedForm?.live ? (
              <span className="form-picker-meta" title={`Live ${selectedForm.live.edition}`}>
                Live {selectedForm.live.edition}
              </span>
            ) : null}
          </label>
        </div>
      </header>

      <div className="app-body">
        {route.screen === "compare" ? (
          <Compare key={formId} formId={formId} />
        ) : route.screen === "fields" ? (
          <Fields key={formId} formId={formId} />
        ) : (
          <EmployeeForm key={formId} formId={formId} />
        )}
      </div>
    </div>
  );
}
