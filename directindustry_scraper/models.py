"""Data models for DirectIndustry scaper."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Supplier:
    company_id: str
    company_name: str
    search_term: str
    location: Optional[str] = None
    state: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    profile_url: Optional[str] = None
    description: Optional[str] = None
    company_type: Optional[str] = None
    annual_revenue: Optional[str] = None
    num_employees: Optional[str] = None
    year_founded: Optional[str] = None
    brands: Optional[str] = None
