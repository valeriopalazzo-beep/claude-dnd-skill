#!/usr/bin/env python3
"""The reminder a script prints when it opens a situation (ADR 0005).

Sections of the DM's rules that matter only now and then live in
SKILL-situations.md; SKILL.md -> *Situations* says when each must be read.
As a safety net, the command that opens a situation (a chase, a journey, a
vote, a trap, a map...) prints one line naming the section to read before
resolving it. The line goes to stderr, so output other tools parse is untouched.

    from situations import remind
    remind("Chases", ("start",))   # only when the subcommand is `start`
    remind("Images & Maps")        # every run

Scripts call it from their `if __name__ == "__main__":` block only, so the
display server, which imports them, never prints it.
"""
import sys

LINE = '>> SKILL-situations.md -> "{}": read it before resolving, unless read since the last /clear.'


def remind(section: str, cmds=None, argv=None) -> bool:
    """Print the reminder for `section`; with `cmds`, only when one of them is the subcommand."""
    args = list(sys.argv[1:] if argv is None else argv)
    if cmds is not None and not any(t in cmds for t in args[:4]):
        return False
    try:
        print(LINE.format(section), file=sys.stderr, flush=True)
    except Exception:
        return False
    return True

