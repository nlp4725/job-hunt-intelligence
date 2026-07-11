"""
Ad-hoc manual check for the Company Research Agent — run directly to print
each company's cached/generated report. Not an automated assertion-based
test; a quick way to eyeball real output before trusting it as Judge Agent
input. See judge/company_research_agent.py.
"""

from db.session import get_session
from db.models import Company
from judge.company_research_agent import get_or_create_report

COMPANY_IDS = [792]  # Hopper


def test_company_report(company_ids: list[int]) -> None:
    session = get_session()
    for company_id in company_ids:
        company = session.get(Company, company_id)
        if company is None:
            print(f"--- company_id={company_id}: not found ---\n")
            continue
        print(f"--- {company.name} (id={company_id}) ---")
        report = get_or_create_report(company, session)
        print(report)
        print()
    session.close()


if __name__ == "__main__":
    test_company_report(COMPANY_IDS)
