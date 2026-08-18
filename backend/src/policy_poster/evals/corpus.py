"""Golden eval corpus: three synthetic policies with known obligations."""

from __future__ import annotations

from ..models import Block

# (name, angle, sensitive_terms, blocks)
CORPUS: list[tuple[str, str, list[tuple[str, str]], list[Block]]] = [
    (
        "hr-leave",
        "clarity and reassurance around the new leave policy",
        [("Meridian Group", "org"), ("PeopleHub", "system")],
        [
            Block(text="1. Purpose", kind="heading", level=1),
            Block(text="This policy explains paid leave at Meridian Group.", kind="paragraph"),
            Block(text="2. Entitlement", kind="heading", level=1),
            Block(text="Employees accrue 1.75 days of paid leave per month.", kind="paragraph"),
            Block(text="Unused leave must be used before 31 March each year.", kind="paragraph"),
            Block(text="3. Requesting Leave", kind="heading", level=1),
            Block(text="Leave requests must be submitted in PeopleHub at least 5 working days in advance.", kind="paragraph"),
            Block(text="Managers must respond to requests within 2 working days.", kind="paragraph"),
        ],
    ),
    (
        "infosec",
        "urgency around incident reporting",
        [("Northwind Traders", "org"), ("SentinelOne Console", "system"),
         ("soc@northwind.example", "email")],
        [
            Block(text="1. Scope", kind="heading", level=1),
            Block(text="This standard applies to all staff and contractors of Northwind Traders.", kind="paragraph"),
            Block(text="2. Incident Reporting", kind="heading", level=1),
            Block(text="Suspected security incidents must be reported within 1 hour to soc@northwind.example.", kind="paragraph"),
            Block(text="Phishing attempts must be reported via the SentinelOne Console button.", kind="paragraph"),
            Block(text="3. Sanctions", kind="heading", level=1),
            Block(text="Failure to report a known incident may result in disciplinary action.", kind="paragraph"),
        ],
    ),
    (
        "ai-governance",
        "responsible AI awareness for all employees",
        [("Aurora Labs", "org")],
        [
            Block(text="1. Principles", kind="heading", level=1),
            Block(text="Aurora Labs develops AI systems that are fair, transparent, and accountable.", kind="paragraph"),
            Block(text="2. Obligations", kind="heading", level=1),
            Block(text="High-risk AI use cases must be approved by the AI Review Board before deployment.", kind="paragraph"),
            Block(text="Model decisions affecting customers must be explainable on request.", kind="paragraph"),
            Block(text="Incidents involving AI harm must be escalated within 24 hours.", kind="paragraph"),
        ],
    ),
]
