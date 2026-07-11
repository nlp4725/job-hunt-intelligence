"""
Ad-hoc manual check for the Tavily-based Company Research Agent variant —
run directly to print each company's cached/generated report. See
judge/company_research_agent_tavily.py.

Uses a different default company than tests_and_eval/test_company_report.py
(Humana, not Hopper) since both agent variants write to the same
Company.research_report cache column — testing them against the same
company would just return whichever one ran first.
"""

from db.session import get_session
from db.models import Company
from judge.company_research_agent_tavily import get_or_create_report

COMPANY_IDS = [436]  # Humana


def test_company_report_tavily(company_ids: list[int]) -> None:
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
    test_company_report_tavily(COMPANY_IDS)
