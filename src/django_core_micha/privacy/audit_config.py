"""Privacy decisions are pruned by this app's bounded evidence retention command.

They are intentionally not registered with the generic audit log, whose separate
retention would create an additional copy of consent evidence.
"""

