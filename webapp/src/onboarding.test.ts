import { describe, expect, it } from "vitest";

import { orderedRows } from "./components/ScoresStep";
import { suggest } from "./components/SkillsStep";
import type { ScoreTable } from "./api";

const VOCABULARY = ["SQL", "PostgreSQL", "PyTorch", "Python", "TensorFlow", "NoSQL"];

describe("skill suggestions", () => {
  it("offers names that start with what you typed before names that merely contain it", () => {
    expect(suggest(VOCABULARY, "sql", [])).toEqual(["SQL", "PostgreSQL", "NoSQL"]);
  });

  it("ignores case and surrounding space", () => {
    expect(suggest(VOCABULARY, "  PyTo ", [])).toEqual(["PyTorch"]);
  });

  it("leaves out skills already on the list", () => {
    expect(suggest(VOCABULARY, "py", ["Python"])).toEqual(["PyTorch"]);
  });

  it("suggests nothing for an empty box, rather than the whole vocabulary", () => {
    expect(suggest(VOCABULARY, "   ", [])).toEqual([]);
  });

  it("suggests nothing for a name the matcher does not know", () => {
    expect(suggest(VOCABULARY, "cobol", [])).toEqual([]);
  });

  it("caps the list so the dropdown stays a dropdown", () => {
    const many = Array.from({ length: 40 }, (_, i) => `Skill${i}`);
    expect(suggest(many, "skill", []).length).toBe(8);
  });
});

describe("score table order", () => {
  const table = (scores: Partial<ScoreTable>): ScoreTable => ({
    intern: 0, entry: 0, mid_senior: 0, senior: 0, staff_principal: 0, not_a_fit: 0, unknown: 3, ...scores,
  });

  it("puts the best-fitting level first", () => {
    const rows = orderedRows(table({ intern: 0, entry: 4, mid_senior: 5, senior: 5, staff_principal: 4 }));
    expect(rows.map((row) => row.key)).toEqual([
      "mid_senior", "senior", "entry", "staff_principal", "intern", "not_a_fit", "unknown",
    ]);
  });

  it("keeps ladder order between levels scored the same", () => {
    const rows = orderedRows(table({ intern: 2, entry: 2, mid_senior: 2, senior: 2, staff_principal: 2 }));
    expect(rows.slice(0, 5).map((row) => row.key)).toEqual([
      "intern", "entry", "mid_senior", "senior", "staff_principal",
    ]);
  });

  it("pins 'not a fit' and 'level unclear' below the levels whatever they score", () => {
    const rows = orderedRows(table({ intern: 0, unknown: 5, not_a_fit: 5 }));
    expect(rows.slice(-2).map((row) => row.key)).toEqual(["not_a_fit", "unknown"]);
  });

  it("lists every row exactly once", () => {
    const rows = orderedRows(table({ senior: 5 }));
    expect(new Set(rows.map((row) => row.key)).size).toBe(7);
  });
});
