"""Unit tests for the skill taxonomy's regex fixes and for Skill Match.

The taxonomy is deliberately flat keyword matching (see skills_extractor's
module docstring), which makes it fast and free but leaves it exposed to
variants that match inside a longer word. Each case below is a false positive
that was measured in the real corpus.
"""

from analysis.skill_match import skill_match_score
from analysis.skills_extractor import extract_skills


class TestUnanchoredVariantRegressions:
    """Variants that used to match the middle of an unrelated word."""

    def test_pair_programming_is_not_the_R_language(self):
        """The "r programming" variant had no word boundary, so it fired on
        "pair programming". 255 of 1,360 R tags in the corpus came from this
        path alone."""
        assert "R" not in extract_skills("You will do pair programming interviews.")

    def test_real_R_mentions_still_match(self):
        assert "R" in extract_skills("Proficiency in Python, R, SQL.")
        assert "R" in extract_skills("Experience with R programming is required.")

    def test_fragile_is_not_agile(self):
        """38 corpus JDs matched Agile via the word "fragile"."""
        assert "Agile" not in extract_skills("The legacy system is fragile.")
        assert "Agile" in extract_skills("We work in an Agile environment.")

    def test_lab_testing_is_not_ab_testing(self):
        """11 corpus JDs matched A/B Testing via "lab testing"."""
        assert "A/B Testing" not in extract_skills("Our lab testing process is rigorous.")
        assert "A/B Testing" in extract_skills("You will run A/B tests on ranking.")


class TestLineBreaksInsideSkills:
    """103 of 261 variants contain a literal space. Resume PDFs wrap lines in
    the middle of skill lists (Nasi's own resume PDF extracts as
    "Function\\nCalling"), so a space in a variant has to match any
    whitespace, including a line break."""

    def test_multi_word_skill_split_across_lines_is_found(self):
        found = set(extract_skills("Git, Docker, Prompt\nEngineering, A/B\nTesting, Vector\nDatabase"))
        assert {"Prompt Engineering", "A/B Testing", "Vector Database"} <= found

    def test_word_boundary_fixes_still_hold_across_a_line_break(self):
        """Matching a space as any whitespace must not undo the \\b fixes
        above: "pair\\nprogramming" is still not the R language."""
        assert "R" not in extract_skills("We do pair\nprogramming daily.")
        assert "A/B Testing" not in extract_skills("Our lab\ntesting process is rigorous.")


class TestSkillMatchScoring:
    """Skill Match scores the whole JD on purpose — see skill_match_score's
    comment for why section-aware scoring was measured and rejected."""

    RESUME = "Python, SQL, Snowflake, dbt, Airflow, LangGraph, RAG, LLM evaluation."

    def test_requirements_skills_are_counted(self):
        jd = "Responsibilities " + "Build RAG systems and agentic workflows with LangGraph. " * 10
        r = skill_match_score(self.RESUME, jd)
        every = set(r["matched_skills"] + r["missing_skills"] + r["group_matched_skills"])
        assert {"RAG", "Agents", "LangGraph"} <= every
        assert "RAG" in r["matched_skills"]
        assert "Agents" in r["missing_skills"]

    def test_jd_with_no_taxonomy_skills_scores_zero(self):
        r = skill_match_score(self.RESUME, "Responsibilities " + "Be a good teammate. " * 20)
        assert r["score"] == 0
        assert r["note"] is not None

    def test_group_substitution_still_credits(self):
        """A JD wanting GCP is substantially satisfied by AWS on the resume."""
        jd = "Responsibilities " + "Deploy services on GCP at scale. " * 12
        r = skill_match_score("I have deep AWS experience.", jd)
        assert "GCP" in r["group_matched_skills"]

    def test_score_is_banded_0_to_5(self):
        jd = "Responsibilities " + "Use Python and SQL daily. " * 12
        assert 0 <= skill_match_score(self.RESUME, jd)["score"] <= 5


class TestTaxonomyRefresh20260915:
    """Taxonomy refresh 2026-09-15 (backfill batch 1): skills an LLM found in
    500 ml_ai JDs that the taxonomy missed, plus false matches it exposed.
    Counts are from check_candidate.py over the 13,662-JD corpus."""

    def test_new_software_and_data_engineering_skills(self):
        """Distributed Systems 1,538 JDs · Data Pipelines 2,210 · Data Modeling 795
        · API Design & Development 773 · Software Testing 996."""
        jd = ("Experience building distributed systems and data processing pipelines. "
              "Strong data modeling. Deep understanding of API design. Write unit tests.")
        found = set(extract_skills(jd))
        assert {"Distributed Systems", "Data Pipelines", "Data Modeling",
                "API Design & Development", "Software Testing"} <= found

    def test_new_skills_ignore_ordinary_english(self):
        assert "Distributed Systems" not in extract_skills("We distributed the report to partners.")
        assert "API Design & Development" not in extract_skills("You will consume third-party APIs.")
        assert "Software Testing" not in extract_skills("Own the integration of third-party systems.")

    def test_ai_coding_tools(self):
        """Claude Code 734 JDs · Cursor 647 · Codex 329."""
        found = set(extract_skills("Daily use of tools like Claude Code, Cursor, Codex."))
        assert {"Claude Code", "Cursor", "Codex"} <= found

    def test_hipaa(self):
        """475 JDs."""
        assert "HIPAA" in extract_skills("Experience working in a HIPAA regulated environment.")

    def test_fine_tuning_spellings(self):
        """+100 JDs over the old hyphen-only patterns, 0 lost."""
        for jd in ("supervised fine tuning", "finetune open-source models", "fine-tune LLMs", "finetuning"):
            assert "Fine-tuning" in extract_skills(jd), jd
        assert "Fine-tuning" not in extract_skills("We fine-tune solutions that meet customer needs.")

    def test_ordinary_recommendations_are_not_recommender_systems(self):
        """Bare "recommendations" tagged 1,365 JDs on ordinary English."""
        assert "Recommendation Systems" not in extract_skills(
            "Synthesize findings into prioritized recommendations that inform the roadmap.")
        assert "Recommendation Systems" not in extract_skills("Three letters of recommendation required.")
        for jd in ("build recommendation systems", "personalized recommendations", "content recommender"):
            assert "Recommendation Systems" in extract_skills(jd), jd

    def test_r_and_d_and_requisition_ids_are_not_the_R_language(self):
        """488 JDs tagged R only via "R&D" or ids like "R-102832"."""
        assert "R" not in extract_skills("Partner with Sales, Product Management, R&D and Field Science.")
        assert "R" not in extract_skills("Job ID: R-102832")
        assert "R" in extract_skills("Proficiency in Python, R, SQL.")

    def test_glued_capture_text_is_not_a_masters_degree(self):
        """118 JDs: unbounded "msc" inside glued text and names like MSCI."""
        assert "Master's Degree" not in extract_skills("the technical health of our systemsCollaborate with your manager")
        assert "Master's Degree" not in extract_skills("Experience at Goldman Sachs, MSCI, or similar.")
        assert "Master's Degree" in extract_skills("MSc in Computer Science")

    def test_embedding_as_a_verb_is_not_embeddings(self):
        """163 JDs tagged on the verb alone."""
        assert "Embeddings" not in extract_skills("We're embedding AI directly into clinical workflows.")
        assert "Embeddings" not in extract_skills("By embedding these principles into our culture.")
        for jd in ("experience with embeddings", "chunking and embedding, vector search",
                   "Embedding and ingestion pipelines", "retrieval and embedding systems"):
            assert "Embeddings" in extract_skills(jd), jd

    def test_resume_cv_is_not_computer_vision(self):
        """156 JDs tagged via "upload your CV" / "Resume/CV"."""
        assert "Computer Vision" not in extract_skills("By sending us your CV, you consent to the processing.")
        assert "Computer Vision" not in extract_skills("Upload Resume/CV (PDF only)")
        assert "Computer Vision" in extract_skills("Build CV/NLP applications and train CV models.")

    def test_spark_as_a_verb_is_not_apache_spark(self):
        """44 JDs tagged via "spark innovation" / "find your spark"."""
        for jd in ("help us think bigger, spark innovation, and succeed",
                   "opportunities for you to find your spark and grow with us", "to spark new ideas"):
            assert "Spark" not in extract_skills(jd), jd
        for jd in ("SQL, Python and spark", "tools like Airflow, Kafka, or Spark.", "Apache Spark", "PySpark"):
            assert "Spark" in extract_skills(jd), jd
