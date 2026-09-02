"""Phoenix — the autonomous recovery and self-healing subsystem for Anton.

Phoenix owns monitoring, incident recording, recovery orchestration and the
publication of standardized recovery events to Hermes. It never notifies users
directly and never runs AI workloads — those belong to Hermes and Oracle.

Philosophy: Observe -> Diagnose -> Recover -> Learn -> Report
"""

__version__ = "1.0.0"
