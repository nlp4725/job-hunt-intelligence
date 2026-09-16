"""The agency name list filters recruiter postings out before screening.

Seniority classification no longer judges agencies (decided 2026-09-16), so a
recruiter that isn't on the list, and doesn't self-report "Staffing and
Recruiting", reaches the rubric as if it were a real employer.
"""

from types import SimpleNamespace

import pytest

from judge.agency_blocklist import is_agency_job


def _job(company_name, industry=None):
    return SimpleNamespace(company_name=company_name, company=SimpleNamespace(industry=industry) if industry else None)


@pytest.mark.parametrize("name", ["MeeBoss", "Calance", "Stefanini North America and APAC"])
def test_recruiters_found_in_the_seniority_gold_set_are_filtered(name):
    """None of these self-reports "Staffing and Recruiting"."""
    assert is_agency_job(_job(name, industry="IT Services and IT Consulting"))


@pytest.mark.parametrize("name", ["CBTS", "Zoom", "Accenture", "Tata Consultancy Services"])
def test_services_firms_hiring_for_themselves_are_not(name):
    assert not is_agency_job(_job(name, industry="IT Services and IT Consulting"))


def test_the_staffing_industry_is_still_filtered():
    assert is_agency_job(_job("Some Placement Co", industry="Staffing and Recruiting"))
