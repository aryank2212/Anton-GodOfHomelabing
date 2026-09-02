"""Sentinel — the perception and situational awareness subsystem for Anton.

Sentinel observes Anton from many independent sources, correlates the
observations into situations, tracks presence and device inventory, and
publishes standardized events to Hermes.

Sentinel never repairs (that is Phoenix), never decides (that is Guardian),
never notifies (that is Hermes) and never runs AI (that is Oracle).

Philosophy: Observe -> Correlate -> Understand -> Publish
"""

__version__ = "1.0.0"
