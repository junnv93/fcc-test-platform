"""Custody policy for the token revocation list — *who* pays when it is full.

The revocation list is what makes logout mean anything. Logout deliberately does
**not** advance ``session_version`` (raising it would end this user's sessions on
every other machine — see ``LocalAuthService.logout``), so an in-memory ``jti``
list is the only guarantee that a captured refresh token stops minting sessions.

That list has a ceiling, and a ceiling implies eviction, and eviction of a
not-yet-expired entry is a **silent security downgrade**: the evicted token
becomes usable again. So the question this module answers is not *"how do we
avoid evicting"* — it is *"whose entry is forfeit"*.

## The defect this module exists to close (measured 2026-08-22)

Adversarial review reproduced it end-to-end through the real HTTP routes::

    logout -> 200                    replay captured refresh -> 401
    ... attacker rotates their OWN refresh token 20,001 times ...
    replay captured refresh -> 200   GET /platform/auth/me -> 200 (victim identity)

Three facts multiplied:

1. logout's only guarantee is list membership (above);
2. the victim selection was **global** — soonest-to-expire first — and a logout
   entry always expires sooner than the fresh seven-day entry each rotation
   inserts, so the oldest logout was permanently first in line;
3. **the attacker controls insertion.** ``/platform/auth/refresh`` is ``public``
   and carries no bcrypt (measured: 380 rotations/second), so only the peer tier
   applies — and that tier is one bucket for the whole deployment.

⚠️ **Fixing (2) is not the repair.** Discarding the entry with the least
remaining protection is, on its own terms, the right choice. The defect is (3).

## Why a rate limit is not the repair either

Bounding rotations per subject requires a **measured** concurrent-session
envelope, and there is none — not in this repository and not in EMS, whose
``THROTTLE_PRESETS.TOKEN_REFRESH`` carries no derivation and is documented by its
own call site as bypassed in production. ADR-0021 **D-11** records what shipping
an unmeasured envelope cost: a source ceiling tightened without one reproduced a
production incident with thirty testers typing correct passwords.

## What this module does instead: a derived fair share

Every insertion into the list arrives from claims that
``verify_token`` has **already** authenticated, so the ``sub`` behind it cannot be
forged. Attribute each entry to that subject — its *custodian* — and let a
custodian over its fair share forfeit **its own** oldest entry.

This needs no envelope because **it denies nothing**. Every request still
succeeds; the only thing that changes is who pays at the ceiling.

And it closes the attack structurally, not statistically: to insert under
custodian *V* you must present a validly signed token for *V*. The captured
``RT_v`` the attacker wants resurrected was revoked by the logout, so it cannot
rotate; and holding a *different* live refresh token for *V* already is the
account, so eviction buys nothing.

## The share is derived, and the derivation is self-consistent

``fair_share = max_entries // custodian_count`` (floored at one). No new constant.

⚠️ **Read that arithmetic in the direction that is actually usable.** *If every
custodian sits at or below its share, the total is at most
``n * (max_entries // n) <= max_entries``.* The **contrapositive** is the rule:
**if the total exceeds the ceiling then some custodian is over its share** — so a
victim that owes something always exists, and the heaviest custodian is one of
them.

⚠️ The first draft read it as *"the fallback is only reachable in a transient"*
and keyed the decision on the custodian that had **just inserted**. That is a
different proposition — the arithmetic quantifies over *every* custodian, the
branch asked about *one* — and two independent adversarial reviewers turned the
gap into full account takeover through the real routes. **The premise is never
enforced below the ceiling**, so nothing stops one custodian from growing while
everyone else stays light; then any light custodian's ordinary insertion fell
through to the old global rule.

⚠️ **This module holds no counters, no clock and no identifiers.** It is given the
numbers and answers with numbers, exactly like ``outbox_retry_policy`` and
``analyzer_reset_policy``. The custody bookkeeping lives with the list.
"""
from __future__ import annotations

import math


__all__ = [
    'UNATTRIBUTED_CUSTODIAN',
    'custodian_should_yield',
    'custody_fair_share',
    'steady_state_entries_per_session',
    'steady_state_sessions_to_fill',
]


#: Bucket for an entry whose custodian could not be established.
#:
#: ⚠️ **Its own bucket, deliberately — not a share of somebody else's.** Folding
#: unattributable entries into a real custodian would let them dilute that
#: custodian's share, and folding them into "no custody at all" would exempt them
#: from the ceiling entirely. A distinct bucket is the only option that neither
#: punishes nor exempts.
#:
#: In production this is unreachable: every insertion site verifies a signature
#: first and a verified token carries ``sub``. It exists because the list must be
#: a total function over its inputs, and because tests construct entries directly.
UNATTRIBUTED_CUSTODIAN = '\x00unattributed'


def custody_fair_share(max_entries: object, custodian_count: object) -> int:
    """How many entries one custodian may hold before it starts paying for itself.

    Derived from the ceiling and the number of custodians actually being
    tracked — there is no constant here, and adding one would be the thing this
    module exists to avoid (see the module docstring on measured envelopes).

    Floored at ``1``: a share of zero would make every custodian permanently
    over-share, which turns the fallback branch into the normal path and loses the
    self-consistency argument.

    Total function — any input answers. A non-numeric or non-positive
    ``max_entries`` answers ``1``, the most conservative share, rather than
    raising inside a lock held by every authenticated request.
    """
    ceiling = _positive_int(max_entries)
    if ceiling is None:
        return 1
    count = _positive_int(custodian_count)
    if count is None:
        return ceiling
    return max(1, ceiling // count)


def custodian_should_yield(*, held: object, fair_share: object) -> bool:
    """Does the custodian that just inserted have to forfeit one of its own?

    ``True`` exactly when it holds **more** than its share. Strictly more, not at
    least: a custodian sitting exactly at its share is by definition not the
    reason the ceiling was crossed, and making it pay would mean that in a
    perfectly balanced deployment everyone pays on every insertion.

    Total function; unreadable inputs answer ``False``, which routes the decision
    to the global fallback branch. That direction is safe because the fallback
    still bounds memory — it only loses the attribution — whereas answering
    ``True`` on garbage would make a custodian forfeit entries it does not owe.
    """
    holding = _positive_int(held)
    if holding is None:
        return False
    share = _positive_int(fair_share)
    if share is None:
        return False
    return holding > share


def steady_state_entries_per_session(
    refresh_ttl_seconds: object, access_ttl_seconds: object,
) -> int:
    """Entries one continuously-active session leaves in the list, in steady state.

    A rotation inserts one entry whose expiry is the **presented** refresh
    token's, i.e. roughly ``refresh_ttl`` ahead of the insertion; and a client
    rotates about once per ``access_ttl`` because that is when its access token
    runs out. So the count a single live session settles at is the ratio.

    ⚠️ **This is a measuring stick, not a budget.** Nothing consults it to decide
    anything. It exists because the arithmetic it performs was invisible until
    2026-08-22 and is load-bearing for sizing the ceiling: with this repository's
    defaults it answers **672**, so ``REVOCATION_LIST_MAX_ENTRIES = 20_000``
    is reached by roughly **29** continuously-active sessions — from ordinary use,
    with no attacker. Leaving that as a number in a comment is how it gets stale;
    deriving it from the two TTLs that actually govern it is how it stays true.

    The repair for *that* is not a bigger ceiling — it is a rotation scheme that
    does not accumulate one entry per rotation (OAuth 2.0 Security BCP §4.14.2
    replay detection collapses a chain to a single record). That is a separate
    axis and it is on the ledger.

    Total function; unreadable inputs answer ``0`` (*"cannot say"*), never a
    fabricated size.
    """
    refresh_ttl = _positive_int(refresh_ttl_seconds)
    access_ttl = _positive_int(access_ttl_seconds)
    if refresh_ttl is None or access_ttl is None:
        return 0
    return int(math.ceil(refresh_ttl / access_ttl))


def steady_state_sessions_to_fill(
    max_entries: object, refresh_ttl_seconds: object, access_ttl_seconds: object,
) -> int:
    """상한을 **공격자 없이** 채우는 데 필요한 연속 활성 세션 수.

    :func:`steady_state_entries_per_session` 의 짝이고, 나눗셈이 **올림**인 것이 요점이다:
    상한이 20 000 이고 세션당 672 면 29 세션은 19 488 로 상한에 **닿지 않는다**. 답은
    30 이다. ⚠️ 첫 판은 이 산술을 내림으로 적어 산문이 *"~29 세션이면 도달한다"* 고
    말했고, 그것은 도달하지 않는 수였다(적대 평가 2R F8).

    이 수는 축출 경고가 **공격을 주장하지 않게** 하려고 존재한다 — 운영자가 그 줄을 보고
    무고한 시험원에게 비밀번호 변경을 시키지 않도록, 정상 포화의 규모를 함께 싣는다.

    Total function; 읽을 수 없으면 ``0``(*"말할 수 없다"*).
    """
    per_session = steady_state_entries_per_session(
        refresh_ttl_seconds, access_ttl_seconds,
    )
    ceiling = _positive_int(max_entries)
    if per_session <= 0 or ceiling is None:
        return 0
    return int(math.ceil(ceiling / per_session))


def _positive_int(raw: object) -> "int | None":
    """``raw`` as a positive int, or ``None`` when it is not readable as one.

    ⚠️ ``bool`` is rejected on purpose. ``True`` is an ``int`` in Python and would
    answer ``1``, so a caller that accidentally passes a flag where a count
    belongs would silently get the most restrictive share instead of a diagnosis.

    ⚠️ **The ``except`` is deliberately broad, and the narrow version was a live
    defect in this module's first draft.** ``int(x)`` runs ``x.__int__``, which is
    caller code and may raise anything at all — the seal found it with an object
    whose ``__int__`` raises ``RuntimeError``. Declaring totality and then
    catching three named exceptions is the shape ``fingerprint_digest`` already
    paid for with unpaired surrogates: a promise that holds until one byte of
    hostile input, at which point the failure lands inside a lock that every
    authenticated request waits on. The sibling
    ``extract_credential_identifier`` catches broadly for the same reason.
    """
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)  # type: ignore[call-overload]
    except Exception:  # noqa: BLE001 — 위 문단: 총함수 선언은 __int__ 까지 포함한다
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        # ``__int__`` 가 int 가 아닌 것을 돌려줄 수 있다 — 그 값으로 비교하면
        # 여기서 터지지 않고 **호출부에서** 터진다.
        return None
    return value if value > 0 else None
