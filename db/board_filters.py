"""Filters for jobs shown on a user's board, shared by the cloud access layer
and the batch scorers."""

from sqlalchemy import and_, func, or_

from db.models import Company, Job
from judge.agency_blocklist import AGENCY_COMPANY_NAME_SUBSTRINGS


def not_agency():
    """The same agency filter the local app applies before screening. The
    query must outer-join Company."""
    name = func.lower(func.coalesce(Job.company_name, ""))
    return and_(*(~name.contains(fragment, autoescape=True) for fragment in AGENCY_COMPANY_NAME_SUBSTRINGS),
                or_(Company.industry.is_(None), Company.industry != "Staffing and Recruiting"))
