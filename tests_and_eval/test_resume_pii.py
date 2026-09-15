"""redact_pii() produces the only resume text allowed to leave the skill
extractor: into logs, LLM calls or embeddings. Skill extraction itself runs on
the unredacted text, so over-redacting costs little and under-redacting leaks.

Three layers, each enough on its own for what it covers, so no single missed
detection leaks contact details (docs/resume_jd_skill_pipeline.md §4.2).
"""

from resume.pii import KnownIdentity, redact_pii

JANE = KnownIdentity(name="Jane Q. Doe", email="Jane.Doe@example.com")


class TestKnownIdentity:
    def test_the_users_own_name_and_email_are_removed_in_any_case(self):
        """The login token already gives us both, so they're redacted
        wherever they appear, not only in a detected header."""
        text = "JANE Q. DOE\nContact jane.doe@EXAMPLE.com\nSKILLS\nPython, SQL"
        redacted = redact_pii(text, JANE)
        assert "jane" not in redacted.lower()
        assert "example.com" not in redacted.lower()
        assert "Python, SQL" in redacted


# Shaped like a real resume PDF's extracted header (label and value on
# separate lines, bare 10-digit phone, bare LinkedIn path). Values are fake.
HEADER = (
    "Phone: \n5551234567\n|\nEmail: \nj.doe.work@mail.example.org\n|\n"
    "Linkedin: \n/in/jane-q-doe/\n \n|\nGithub: \nhttps://github.com/jqd4725\n"
)


class TestPatternPii:
    """Found by pattern anywhere, so they're removed even when the user's
    resume uses a different email than their login, or no header is found."""

    def test_contact_details_in_an_extracted_pdf_header_are_removed(self):
        redacted = redact_pii(HEADER, JANE)
        for leaked in ("5551234567", "j.doe.work", "mail.example.org", "jane-q-doe", "jqd4725", "github.com"):
            assert leaked not in redacted, leaked

    def test_common_phone_formats_are_removed(self):
        for phone in ("(555) 123-4567", "555-123-4567", "555.123.4567", "+1 555 123 4567"):
            redacted = redact_pii(f"Call me at {phone} anytime", JANE)
            assert "123" not in redacted and "4567" not in redacted, phone

    def test_pattern_pii_is_removed_even_when_no_heading_is_found(self):
        """Layer 3 finding nothing must not switch layers 1–2 off."""
        redacted = redact_pii("Reach me: 555-123-4567, jqd@mail.example.org\nI build RAG systems.", JANE)
        assert "4567" not in redacted and "@" not in redacted
        assert "I build RAG systems." in redacted

    def test_dates_money_and_metrics_are_kept(self):
        """Numbers in experience bullets carry meaning later matching needs."""
        text = "03/2022 - Present\nGrew revenue from $0 to $1.7M; 61K+ listings; cut 2+ hours to 30 seconds"
        assert redact_pii(text, JANE) == text


class TestHeaderBlock:
    """Layer 3: the block above the first section heading is almost always
    contact details, including ones no pattern catches (a street address)."""

    def test_everything_above_the_first_section_heading_is_dropped(self):
        text = "Jane Doe\n42 Wallaby Way, Cary, NC 27513\nSUMMARY\nML engineer.\nSKILLS\nPython"
        redacted = redact_pii(text, JANE)
        assert "Wallaby" not in redacted and "27513" not in redacted
        assert redacted.startswith("SUMMARY\nML engineer.\nSKILLS\nPython")

    def test_heading_word_inside_a_sentence_is_not_a_heading(self):
        """Only a line that is just a section name counts, or the header
        block would end at "...skills in Python" and keep the address."""
        text = "42 Wallaby Way, Cary, NC\nStrong skills in Python\nExperience\nBuilt RAG"
        redacted = redact_pii(text, JANE)
        assert "Wallaby" not in redacted
        assert redacted.startswith("Experience\nBuilt RAG")

    def test_markdown_or_bold_heading_ends_the_header_block(self):
        """A .md resume marks headings with '#', and templates often bold
        them. Matching only bare names kept the whole contact block, including
        an address no pattern catches."""
        text = "# Jane Doe\n42 Wallaby Way, Cary, NC 27513\n\n## SUMMARY\nML engineer.\n**SKILLS**\nPython"
        redacted = redact_pii(text, JANE)
        assert "Wallaby" not in redacted and "27513" not in redacted
        assert redacted.startswith("## SUMMARY")

    def test_no_heading_keeps_all_text(self):
        text = "42 Wallaby Way\nI build RAG systems in Python."
        assert "I build RAG systems in Python." in redact_pii(text, JANE)
