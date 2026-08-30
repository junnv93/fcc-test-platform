"""Quarantine deployment configuration inherited from the shell.

Two tests read `FCC_CENTRAL_PROVIDER_ID` out of the inherited environment — one
directly, one through `dict(os.environ)` handed to a subprocess probe — so they
were green on a machine that exported it and red on one that did not. The
production guard they tripped is correct and deliberately fail-closed: a guessed
provider id writes a whole namespace of wrong-but-plausible central uuid5 primary
keys, and fixing it later is an ADR-0005 re-key rather than an edit. The defect
is the tests' dependence on the shell, not the guard.

Repairing the two call sites alone would leave the mechanism intact and the third
instance would hide the same way, so the names are taken out of `os.environ`
before anything is collected — every machine now reproduces what a clean machine
sees.

**Quarantine, not deletion.** Some of these variables are how an operator
deliberately opts a proof in: `tests/integration/test_central_db_e2e_live.py`
runs against a real PostgreSQL only when `FCC_CENTRAL_DB_URL` and
`FCC_CENTRAL_DB_UPGRADE_URL` are supplied. Deleting them outright made that proof
unrunnable, and because it manifests as a *skip* the lane stayed green while the
coverage silently vanished. So the values move here, where a test must name what
it wants: implicit reads stop working, deliberate ones keep working and become
visible at their call site.

**Why this lives in `tests/support/` and not in `conftest.py`.** pytest imports
`conftest.py` under its own module name, and a test doing `from conftest import …`
gets a SECOND module object whose import re-runs the scrub — against an already
empty environment, so it captures nothing and every lookup returns the default.
That is not a hypothetical: it is what happened, and it presented as the live
proof still skipping with both variables exported. A stable dotted module name
that every importer shares is what makes the quarantine a single object.
"""
from __future__ import annotations

import os

#: Deployment-configuration env prefixes that must never reach a test from the
#: ambient shell.
#:
#: Only the three composition-root prefixes. Feature kill-switches
#: (`FCC_WLAN_DCCF_AUTOPULL`, …) are how a session investigates behaviour by
#: hand, and taking those away would remove a diagnostic tool in order to fix a
#: hygiene problem. `tests/test_ambient_env_hermeticity.py` asserts these
#: prefixes still cover every name in the runtime env SSOT dicts, so the set
#: cannot silently stop covering a new composition root.
AMBIENT_CONFIG_ENV_PREFIXES = ('FCC_CENTRAL_', 'FCC_HEADLESS_', 'FCC_PLATFORM_')

#: Composition-root env names that carry **no FCC prefix**, listed exactly.
#:
#: ⚠️ **A prefix axis cannot see these, and that is not a hypothetical.**
#: ``FORWARDED_ALLOW_IPS`` is uvicorn's own variable — deliberately not aliased,
#: because two names for one decision lets an operator set ours while uvicorn
#: keeps its default. The cost of that (correct) choice is that the shell can
#: reach a test through it: with ``FORWARDED_ALLOW_IPS='*'`` exported, every
#: ``create_app`` in the suite aborts at boot, and the failure blames whichever
#: test happened to build an app. Adversarial review measured exactly that.
#:
#: The coverage gate in ``tests/test_ambient_env_hermeticity.py`` derives the
#: required membership from the modules that own these names, so a second
#: unprefixed composition-root variable cannot silently stay outside.
AMBIENT_CONFIG_ENV_NAMES = ('FORWARDED_ALLOW_IPS',)


def _is_ambient_config(name: str) -> bool:
    return name.startswith(AMBIENT_CONFIG_ENV_PREFIXES) or name in AMBIENT_CONFIG_ENV_NAMES

#: What was taken, by name. Populated once, at import.
QUARANTINED: dict[str, str] = {}


def scrub_ambient_config_env() -> dict[str, str]:
    """Move deployment config out of `os.environ` into `QUARANTINED`.

    Idempotent: a second call finds nothing left to take and leaves the first
    call's capture intact. That matters because import order is not something a
    test file should have to reason about.
    """
    taken = {
        name: os.environ[name] for name in list(os.environ)
        if _is_ambient_config(name)
    }
    for name in taken:
        del os.environ[name]
    QUARANTINED.update(taken)
    return taken


def ambient_config_env(name: str, default: str = '') -> str:
    """Read a quarantined value, deliberately and by name."""
    return QUARANTINED.get(name, default)


# At import — which conftest triggers before collection, and therefore before any
# module-level test code can read the environment. A `pytest_configure` hook
# would already be too late for a module that captures configuration at import.
#
# Deliberately not an autouse fixture, which is what the wave plan first called
# for. A function-scoped autouse runs after `setUpClass`, so it would strip
# environment a test class legitimately set up for itself. The defect being fixed
# is AMBIENT leakage — the shell reaching a test — and that is fully addressed
# here. Leakage *between* tests inside one run is a different, unobserved defect,
# recorded in `tech-debt-tracker.md` rather than papered over with a fixture that
# breaks class-level setup.
scrub_ambient_config_env()
