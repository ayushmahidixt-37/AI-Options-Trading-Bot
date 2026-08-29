"""Machine-readable ledger of which date ranges have been used for what.

Exists to make the anti-overfitting discipline in ``BACKTEST_FINDINGS.md``
mechanically enforceable instead of prose-only: a "Confirmed" backtest
result requires a test range that has genuinely never been looked at
before, in any form. Before this module, that rule depended on a human
(or an LLM) remembering and correctly re-deriving the full history every
time -- which is exactly how a real leakage bug slipped through once
already (a "confirmed" result that had actually reused an already-touched
range). This module turns the check into a mechanical SQL query instead.

Only this module may certify a range/candidate combination as eligible
for a "confirmed" label (see ``classify_confirmation``). Nothing else in
the codebase should hand-write that judgement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from .market_archive import MarketArchive

VALID_OUTCOME_LABELS = frozenset({"confirmed", "exploratory", "rejected", "open"})

# Labels a CLI caller may hand-supply. "confirmed" is deliberately excluded --
# it may only ever be derived by classify_confirmation(), never asserted.
CALLER_ASSIGNABLE_OUTCOME_LABELS = frozenset(VALID_OUTCOME_LABELS - {"confirmed"})

_PRE_LEDGER_SEED_CANDIDATE = "_pre_ledger_history_seed"

_USAGE_COLUMNS = (
    "id",
    "recorded_at",
    "candidate_name",
    "role",
    "underlying_key",
    "timeframe",
    "range_start",
    "range_end",
    "outcome_label",
    "forced_override_reason",
    "git_commit",
    "notes",
    "params_fingerprint",
)


class UsageRole(str, Enum):
    SCREENING = "screening"
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    TEST = "test"


@dataclass(frozen=True)
class RangeUsage:
    id: int
    recorded_at: datetime
    candidate_name: str
    role: UsageRole
    underlying_key: str
    timeframe: str
    range_start: date
    range_end: date
    outcome_label: str | None
    forced_override_reason: str | None
    git_commit: str | None
    notes: str
    params_fingerprint: str | None = None


@dataclass(frozen=True)
class RangeCheckResult:
    allowed: bool
    reason: str
    conflicting_rows: tuple[RangeUsage, ...] = ()


def _row_to_usage(row) -> RangeUsage:
    return RangeUsage(
        id=row[0],
        recorded_at=datetime.fromisoformat(row[1]),
        candidate_name=row[2],
        role=UsageRole(row[3]),
        underlying_key=row[4],
        timeframe=row[5],
        range_start=date.fromisoformat(row[6]),
        range_end=date.fromisoformat(row[7]),
        outcome_label=row[8],
        forced_override_reason=row[9],
        git_commit=row[10],
        notes=row[11],
        params_fingerprint=row[12] if len(row) > 12 else None,
    )


def _fetch_all_usage(archive: MarketArchive, underlying_key: str, timeframe: str) -> tuple[RangeUsage, ...]:
    with archive.connect() as con:
        rows = con.execute(
            f"""SELECT {",".join(_USAGE_COLUMNS)} FROM range_usage
                WHERE underlying_key=? AND timeframe=?
                ORDER BY id""",
            (underlying_key, timeframe),
        ).fetchall()
    return tuple(_row_to_usage(row) for row in rows)


def compute_params_fingerprint(params_json: str) -> str:
    """Canonical fingerprint of a ``--params-json`` payload.

    Two candidates with the same name but different parameter values must
    not be able to share test/development/validation history -- this is
    what ``verify_params_fingerprint`` checks against.
    """
    canonical = json.dumps(json.loads(params_json), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def verify_params_fingerprint(
    archive: MarketArchive, *, candidate_name: str, params_fingerprint: str
) -> str | None:
    """Return an error reason if ``candidate_name`` was ever recorded with a
    different parameter fingerprint, else ``None``.

    Deliberately not overridable by ``forced_override_reason`` -- a name
    reused for a different parameter set is a data-integrity problem, not
    the kind of exceptional judgement call a forced override exists for.
    """
    with archive.connect() as con:
        rows = con.execute(
            """SELECT DISTINCT params_fingerprint FROM range_usage
               WHERE candidate_name=? AND params_fingerprint IS NOT NULL""",
            (candidate_name,),
        ).fetchall()
    prior = {row[0] for row in rows}
    if prior and params_fingerprint not in prior:
        return (
            f"candidate {candidate_name!r} was previously recorded with a different "
            "parameter set (fingerprint mismatch) -- use a new candidate name for a "
            "different BacktestParameters configuration"
        )
    return None


def resolve_archive_date_range(
    archive: MarketArchive, underlying_key: str, timeframe: str
) -> tuple[date, date] | None:
    """The full [min, max] date span of Upstox candle data already archived
    for this underlying/timeframe, or ``None`` if there is none yet."""
    with archive.connect() as con:
        row = con.execute(
            """SELECT MIN(date(started_at)), MAX(date(started_at))
               FROM market_candles WHERE instrument_token=? AND timeframe=? AND source='upstox'""",
            (underlying_key, timeframe),
        ).fetchone()
    if not row or row[0] is None:
        return None
    return date.fromisoformat(row[0]), date.fromisoformat(row[1])


def has_underlying_data(
    archive: MarketArchive, *, underlying_key: str, timeframe: str, start: date, end: date
) -> bool:
    """Whether any Upstox underlying candle exists inside [start, end].

    Used to refuse spending a candidate's one test attempt on a range that
    was simply never ingested, rather than letting it come back
    "INSUFFICIENT DATA" and be permanently unretriable (see
    ``_do_validate_split`` / ``_do_run``).
    """
    with archive.connect() as con:
        row = con.execute(
            """SELECT 1 FROM market_candles
               WHERE instrument_token=? AND timeframe=? AND source='upstox'
                 AND date(started_at)>=? AND date(started_at)<=? LIMIT 1""",
            (underlying_key, timeframe, start.isoformat(), end.isoformat()),
        ).fetchone()
    return row is not None


def initialize_ledger(archive: MarketArchive) -> None:
    """Idempotent, safe to call on every CLI invocation.

    Closes the exact gap a fresh/empty ``range_usage`` table has against an
    archive that already contains historical candle data: without this,
    the first ``check_range`` after adding the ledger would see
    already-analyzed history as genuinely fresh. For every underlying/
    timeframe scope with existing Upstox candle data and zero recorded
    range_usage rows, seed one conservative ``screening`` row spanning the
    full existing candle span, so pre-ledger history can never be
    certified as an untouched test range.

    Scoped to exclude individual option-contract tokens (anything present
    in ``instruments`` -- option-contract metadata only, never an
    underlying index): every real ``check_range``/``record_usage`` call in
    this codebase scopes to the underlying index token, never a specific
    option contract, so seeding a screening row per contract is both
    unnecessary and, once an archive has thousands of expired contracts
    (each traded only a handful of days near its own expiry), a real
    performance problem -- this seeding loop once took over an hour and
    left two zombie processes contending for the same write lock before
    this scope was added.

    Also removes any such option-contract-scoped seed rows left behind by
    an earlier version of this function that didn't have this filter --
    an archive already upgraded once must not be stuck carrying that bloat
    forever. Only ever deletes rows recorded under the seed's own sentinel
    candidate name; never touches a real usage row.
    """
    with archive.connect() as con:
        scopes = con.execute(
            """SELECT DISTINCT instrument_token, timeframe FROM market_candles
               WHERE source='upstox'
                 AND instrument_token NOT IN (SELECT token FROM instruments)"""
        ).fetchall()
        valid_index_keys = {underlying_key for underlying_key, _ in scopes} or {""}
        placeholders = ",".join(["?"] * len(valid_index_keys))
        con.execute(
            f"""DELETE FROM range_usage
                WHERE candidate_name=? AND underlying_key NOT IN ({placeholders})""",
            (_PRE_LEDGER_SEED_CANDIDATE, *valid_index_keys),
        )
    for underlying_key, timeframe in scopes:
        if _fetch_all_usage(archive, underlying_key, timeframe):
            continue
        span = resolve_archive_date_range(archive, underlying_key, timeframe)
        if span is None:
            continue
        start, end = span
        record_usage(
            archive,
            candidate_name=_PRE_LEDGER_SEED_CANDIDATE,
            role=UsageRole.SCREENING,
            underlying_key=underlying_key,
            timeframe=timeframe,
            start=start,
            end=end,
            notes=(
                "Auto-seeded at ledger initialization from pre-existing archive candle "
                "data -- conservative cutoff so history predating this ledger can never "
                "be certified as an untouched test range."
            ),
        )


def _evaluate_test_eligibility(
    all_rows: tuple[RangeUsage, ...], *, candidate_name: str, start: date, end: date
) -> RangeCheckResult:
    """The actual TEST-role eligibility rule, given an already-fetched row
    set. Factored out so ``check_range`` (plain read) and
    ``reserve_test_range`` (serialized read+write) share one rule."""
    if all_rows:
        latest_end = max(row.range_end for row in all_rows)
        if start <= latest_end:
            conflicting = tuple(row for row in all_rows if row.range_end >= start)
            return RangeCheckResult(
                False,
                f"start ({start.isoformat()}) must be strictly after every range ever "
                f"recorded for this underlying/timeframe (latest touched: {latest_end.isoformat()})",
                conflicting,
            )
    prior_tests = tuple(
        row for row in all_rows if row.role == UsageRole.TEST and row.candidate_name == candidate_name
    )
    if prior_tests:
        return RangeCheckResult(
            False,
            f"candidate {candidate_name!r} already has a test range recorded "
            f"({prior_tests[0].range_start.isoformat()} to {prior_tests[0].range_end.isoformat()}); "
            "test is spent once per candidate",
            prior_tests,
        )
    return RangeCheckResult(True, "no prior usage conflicts; range is genuinely fresh for a test")


def check_range(
    archive: MarketArchive,
    *,
    candidate_name: str,
    role: UsageRole,
    underlying_key: str,
    timeframe: str,
    start: date,
    end: date,
) -> RangeCheckResult:
    """Decide whether a proposed range/role/candidate combination may proceed.

    Development/validation/screening ranges may always be reused -- that is
    expected and how this discipline is meant to work. Test ranges are the
    scarce resource: a proposed test range must start strictly after every
    range ever recorded for this underlying/timeframe (any role, any
    candidate -- not just "non-overlapping"), and this candidate must have
    zero prior test rows. There is no bypass here; ``record_usage`` accepts
    a ``forced_override_reason`` for exceptional cases, but that can never
    produce a "confirmed" label (see module docstring and
    ``classify_confirmation``).

    This is a plain read -- fine for informational/planning checks, but a
    TEST-role run must go through ``reserve_test_range`` instead, which
    performs the same check serialized against other writers so two
    concurrent callers (or a check followed by a crash before recording)
    can't both observe "allowed".
    """
    if start > end:
        return RangeCheckResult(False, "start must not be after end")
    if role != UsageRole.TEST:
        return RangeCheckResult(
            True,
            f"{role.value} ranges may always be reused; recorded for future test-eligibility checks",
        )
    all_rows = _fetch_all_usage(archive, underlying_key, timeframe)
    return _evaluate_test_eligibility(all_rows, candidate_name=candidate_name, start=start, end=end)


def reserve_test_range(
    archive: MarketArchive,
    *,
    candidate_name: str,
    underlying_key: str,
    timeframe: str,
    start: date,
    end: date,
    forced_override_reason: str | None = None,
    params_fingerprint: str | None = None,
) -> RangeUsage | None:
    """Atomically re-check and reserve a TEST range in one serialized
    transaction (``MarketArchive.connect_exclusive``), closing the race
    between a plain ``check_range`` read and a later ``record_usage``
    write. Returns the reservation row if the range was reserved, or
    ``None`` if it was blocked and no ``forced_override_reason`` was given.

    The reservation row is inserted immediately, before the backtest engine
    runs -- so a crash mid-run still leaves this range correctly marked as
    touched (fails closed) instead of silently invisible to future checks.
    Callers must follow a successful reservation with
    ``finalize_test_reservation`` once the engine result is known.
    """
    if start > end:
        return None
    with archive.connect_exclusive() as con:
        rows = con.execute(
            f"""SELECT {",".join(_USAGE_COLUMNS)} FROM range_usage
                WHERE underlying_key=? AND timeframe=? ORDER BY id""",
            (underlying_key, timeframe),
        ).fetchall()
        all_rows = tuple(_row_to_usage(row) for row in rows)
        check = _evaluate_test_eligibility(all_rows, candidate_name=candidate_name, start=start, end=end)
        if not check.allowed and not forced_override_reason:
            return None
        recorded_at = datetime.now().isoformat()
        cursor = con.execute(
            """INSERT INTO range_usage(
                   recorded_at, candidate_name, role, underlying_key, timeframe,
                   range_start, range_end, outcome_label, forced_override_reason, git_commit,
                   notes, params_fingerprint
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recorded_at,
                candidate_name,
                UsageRole.TEST.value,
                underlying_key,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                None,
                forced_override_reason if not check.allowed else None,
                None,
                "RESERVED: backtest in progress",
                params_fingerprint,
            ),
        )
        row_id = cursor.lastrowid
    return RangeUsage(
        id=row_id,
        recorded_at=datetime.fromisoformat(recorded_at),
        candidate_name=candidate_name,
        role=UsageRole.TEST,
        underlying_key=underlying_key,
        timeframe=timeframe,
        range_start=start,
        range_end=end,
        outcome_label=None,
        forced_override_reason=forced_override_reason if not check.allowed else None,
        git_commit=None,
        notes="RESERVED: backtest in progress",
        params_fingerprint=params_fingerprint,
    )


def finalize_test_reservation(
    archive: MarketArchive,
    reservation: RangeUsage,
    *,
    outcome_label: str | None = None,
    git_commit: str | None = None,
    notes: str = "",
) -> RangeUsage:
    """Record the real outcome for a range ``reserve_test_range`` already
    reserved. Appends a second row with the same candidate/role/range
    (append-only design -- see module docstring); ``classify_confirmation``
    always reads the most recent matching row, so this finalized row is
    what gets certified, while the reservation row that came before it is
    what protected the range from a concurrent or crashed second attempt.
    """
    return record_usage(
        archive,
        candidate_name=reservation.candidate_name,
        role=UsageRole.TEST,
        underlying_key=reservation.underlying_key,
        timeframe=reservation.timeframe,
        start=reservation.range_start,
        end=reservation.range_end,
        outcome_label=outcome_label,
        forced_override_reason=reservation.forced_override_reason,
        git_commit=git_commit,
        notes=notes,
        params_fingerprint=reservation.params_fingerprint,
    )


def record_usage(
    archive: MarketArchive,
    *,
    candidate_name: str,
    role: UsageRole,
    underlying_key: str,
    timeframe: str,
    start: date,
    end: date,
    outcome_label: str | None = None,
    forced_override_reason: str | None = None,
    git_commit: str | None = None,
    notes: str = "",
    params_fingerprint: str | None = None,
) -> RangeUsage:
    """Append-only. There is no update/delete function in this module by design."""
    if outcome_label is not None and outcome_label not in VALID_OUTCOME_LABELS:
        raise ValueError(f"outcome_label must be one of {sorted(VALID_OUTCOME_LABELS)}, got {outcome_label!r}")
    if forced_override_reason and outcome_label == "confirmed":
        raise ValueError(
            "a forced_override_reason can never be combined with outcome_label='confirmed' -- "
            "a range that needed a forced override is exploratory at best"
        )
    if start > end:
        raise ValueError("start must not be after end")
    recorded_at = datetime.now().isoformat()
    with archive.connect() as con:
        cursor = con.execute(
            """INSERT INTO range_usage(
                   recorded_at, candidate_name, role, underlying_key, timeframe,
                   range_start, range_end, outcome_label, forced_override_reason, git_commit,
                   notes, params_fingerprint
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recorded_at,
                candidate_name,
                role.value,
                underlying_key,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                outcome_label,
                forced_override_reason,
                git_commit,
                notes,
                params_fingerprint,
            ),
        )
        row_id = cursor.lastrowid
    return RangeUsage(
        id=row_id,
        recorded_at=datetime.fromisoformat(recorded_at),
        candidate_name=candidate_name,
        role=role,
        underlying_key=underlying_key,
        timeframe=timeframe,
        range_start=start,
        range_end=end,
        outcome_label=outcome_label,
        forced_override_reason=forced_override_reason,
        git_commit=git_commit,
        notes=notes,
        params_fingerprint=params_fingerprint,
    )


def classify_confirmation(
    archive: MarketArchive,
    *,
    candidate_name: str,
    underlying_key: str,
    timeframe: str,
    start: date,
    end: date,
) -> str:
    """The only function that may certify a "confirmed"-eligible result.

    Looks up the actual test row recorded for this exact candidate/range
    (not a hypothetical re-check) and classifies it:

    - ``"eligible_confirmed"`` -- a test row exists with no forced
      override. Whatever the backtest's actual result was, the
      methodology is clean: label the finding "Confirmed" if the result
      was good, "Rejected" if it wasn't -- either way it's trustworthy.
    - ``"exploratory"`` -- a test row exists but required a forced
      override to run. Never trustworthy enough to call Confirmed or a
      clean Rejected, regardless of the number.
    - ``"blocked_reused_test"`` -- no such test was ever recorded (it was
      never allowed to run, or hasn't been attempted yet).

    ``BACKTEST_FINDINGS.md`` entries must quote this return value verbatim
    rather than a bot's own judgement.
    """
    with archive.connect() as con:
        row = con.execute(
            """SELECT forced_override_reason FROM range_usage
               WHERE candidate_name=? AND role=? AND underlying_key=? AND timeframe=?
                 AND range_start=? AND range_end=?
               ORDER BY id DESC LIMIT 1""",
            (
                candidate_name,
                UsageRole.TEST.value,
                underlying_key,
                timeframe,
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchone()
    if row is None:
        return "blocked_reused_test"
    return "exploratory" if row[0] else "eligible_confirmed"


def research_context_for_ideation(archive: MarketArchive) -> dict:
    """Redacted context for the idea-generation role.

    Contains ONLY candidate names, roles, date ranges, and outcome
    labels -- never any numeric backtest result. This is the actual
    anti-p-hacking mechanism: an idea generator that cannot see P&L
    cannot implicitly chase it.
    """
    with archive.connect() as con:
        rows = con.execute(
            """SELECT candidate_name, role, underlying_key, timeframe,
                      range_start, range_end, outcome_label
               FROM range_usage ORDER BY id"""
        ).fetchall()
    return {
        "usage_history": [
            {
                "candidate_name": row[0],
                "role": row[1],
                "underlying_key": row[2],
                "timeframe": row[3],
                "range_start": row[4],
                "range_end": row[5],
                "outcome_label": row[6],
            }
            for row in rows
        ],
        "note": (
            "This context intentionally omits every numeric result (net P&L, "
            "win rate, drawdown, trade count). Propose ideas from domain "
            "reasoning and what's already been tried -- never from what "
            "looked profitable before."
        ),
    }


def research_context_for_evaluation(archive: MarketArchive) -> dict:
    """Full, unredacted ledger context for the judging role."""
    with archive.connect() as con:
        rows = con.execute(
            f"""SELECT {",".join(_USAGE_COLUMNS)} FROM range_usage ORDER BY id"""
        ).fetchall()
    return {"usage_history": [dict(zip(_USAGE_COLUMNS, row)) for row in rows]}


def export_ledger_json(archive: MarketArchive, target: str | Path, *, redact: bool = False) -> Path:
    """Write a human-readable, diffable JSON export.

    This (small, metadata-only) file is what gets committed to git --
    never the raw archive database, which stays local per
    ``BACKTEST_FINDINGS.md``'s existing rule against committing trading
    data.

    ``redact=True`` writes the same no-numeric-results context the Idea
    role gets from ``research_context_for_ideation`` -- required so an
    exported file handed to that role can't smuggle P&L/drawdown/trade
    counts through ``notes`` the way the full evaluation context can.
    """
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    context = research_context_for_ideation(archive) if redact else research_context_for_evaluation(archive)
    destination.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
