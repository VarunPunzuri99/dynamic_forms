/**
 * Frontend runner for shared rule parity vectors (Phase 3B–3D).
 * Loads backend/rule_parity_vectors.json — same fixtures as backend.test_rule_parity.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  conditionMet,
  evaluateEffects,
  rulesAreValid,
} from "../src/formRules.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const payload = JSON.parse(readFileSync(join(root, "backend", "rule_parity_vectors.json"), "utf8"));
const questions = payload.questions;
const byId = Object.fromEntries(questions.map((question) => [question.id, question]));

let failed = 0;
let passed = 0;

function assert(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    console.error(`FAIL ${name}: got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
    failed += 1;
  } else {
    passed += 1;
  }
}

for (const caseItem of payload.cases) {
  const id = caseItem.id;
  if (caseItem.kind === "condition") {
    const question = byId[caseItem.question_id];
    for (const sample of caseItem.samples) {
      const actual = conditionMet(question, sample.value, caseItem.condition, sample.expected);
      assert(`${id} value=${JSON.stringify(sample.value)} expected=${JSON.stringify(sample.expected)}`, actual, sample.expect);
    }
    continue;
  }

  if (caseItem.kind === "effects") {
    const qs = caseItem.questions_override || questions;
    const effects = evaluateEffects(qs, caseItem.rules || [], caseItem.answers || {});
    for (const [questionId, expected] of Object.entries(caseItem.expect_effects)) {
      assert(`${id} ${questionId}.disabled`, effects[questionId].disabled, expected.disabled);
      assert(`${id} ${questionId}.required`, effects[questionId].required, expected.required);
    }
    continue;
  }

  if (caseItem.kind === "validate") {
    const actual = rulesAreValid(caseItem.rules, questions);
    assert(`${id} valid`, actual, caseItem.expect_valid);
    continue;
  }

  if (caseItem.kind === "normalize") {
    console.log(`SKIP ${id} (backend-only normalize/planned_values)`);
    continue;
  }

  console.error(`FAIL unknown kind ${caseItem.kind} in ${id}`);
  failed += 1;
}

console.log(`\nPassed ${passed} assertions; skipped backend-only normalize cases`);
if (failed) {
  console.error(`${failed} assertion(s) failed`);
  process.exit(1);
}
console.log("All frontend-shared rule parity checks passed");
