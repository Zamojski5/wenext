"""Domain layer of the control plane — commands, values and the ports they speak through.

THIS PACKAGE MUST NOT KNOW WHAT A DATABASE IS. The DoD grep for the driver's name over this
directory returns nothing — the name itself is therefore absent even from these comments, since
a docstring explaining the rule by quoting it breaks it. That grep is a Definition-of-Done line,
not a convention: "zmiana transportu jobów nie dotyka żadnej komendy domenowej —
potwierdzone testem kontraktowym portu" (L5216). A command that could reach a connection would
make the E3 broker swap untestable, and the deferral of that broker (L5205) is admissible only
because the swap is testable.

Nothing is re-exported from here on purpose. `from control_plane.domain import commands` names
the layer at every call site, and the flat aliases would be the shortcut by which `api/` starts
importing values without going through the commands.
"""
