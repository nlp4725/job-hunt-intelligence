"""normalize() is the shared cleanup that runs on both JD and resume text
before extract_skills(). It only changes how characters are encoded, never
which words are there, so every test asserts on the skills found afterwards.
"""

from analysis.skills_extractor import extract_skills
from analysis.text_normalize import normalize


class TestLookalikeCharacters:
    def test_non_breaking_hyphen_does_not_hide_a_skill(self):
        """158 of 4,393 September JDs use U+2011, which looks like "-" but
        is a different character, so "fine-tuning" never matched."""
        text = "Experience with parameter‑efficient fine‑tuning and scikit‑learn."
        assert {"Fine-tuning", "scikit-learn"} <= set(extract_skills(normalize(text)))

    def test_pdf_ligature_does_not_hide_a_skill(self):
        """PDF fonts draw "fi" as one glyph (U+FB01), and text extraction
        copies that single character out."""
        assert "Fine-tuning" in extract_skills(normalize("ﬁne-tuning of LLMs"))

    def test_non_breaking_space_does_not_hide_a_multi_word_skill(self):
        assert "Prompt Engineering" in extract_skills(normalize("Prompt Engineering, RAG"))

    def test_soft_hyphen_is_removed(self):
        """U+00AD is an invisible "you may break here" marker inside a word."""
        assert "Kubernetes" in extract_skills(normalize("Docker and Kuber­netes"))


class TestWhitespace:
    def test_hyphenated_skill_wrapped_at_a_line_break_is_found(self):
        assert "scikit-learn" in extract_skills(normalize("NumPy, Pandas, scikit-\nlearn, SQL"))

    def test_line_break_after_a_hyphen_keeps_the_hyphen(self):
        """Dropping the hyphen would turn a real compound into "end-toend".
        Whether a taxonomy variant tolerates a missing hyphen is the
        taxonomy's decision, not normalize's."""
        assert "built end-to-end ML systems" in normalize("built end-to-\nend ML systems")

    def test_runs_of_spaces_collapse_but_newlines_stay(self):
        """Newlines are kept for anything that later splits a resume into
        sections or lines."""
        assert normalize("Python   \t SQL\nDocker") == "Python SQL\nDocker"
