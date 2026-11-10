"""Plain-text stats: response times, funnel, no-show rate by venue and by guest."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from .store import Store


def report(store: Store) -> str:
    leads = store.leads()
    if not leads:
        return "No leads yet."
    lines = []
    rt = [l["response_seconds"] for l in leads if l["response_seconds"] is not None]
    if rt:
        lines.append(f"Median first-response time: {median(rt):.0f}s  (slowest {max(rt):.0f}s)")
    counts = defaultdict(int)
    for l in leads:
        counts[l["status"]] += 1
    lines.append("Funnel: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    def rate(group):
        done = [l for l in group if l["status"] in ("arrived", "no_show")]
        ns = sum(1 for l in done if l["status"] == "no_show")
        return ns, len(done)

    lines.append("\nNo-show rate by venue (confirmed guests):")
    by_venue = defaultdict(list)
    for l in leads:
        by_venue[l["venue"] or "?"].append(l)
    for v, group in sorted(by_venue.items()):
        ns, n = rate(group)
        guests = sum(l["arrived_count"] or 0 for l in group if l["status"] == "arrived")
        if n:
            lines.append(f"  {v}: {ns}/{n} no-shows ({ns / n:.0%}), {guests} guests walked in")

    lines.append("\nGuests with 2+ no-shows:")
    by_guest = defaultdict(list)
    for l in leads:
        by_guest[l["username"] or l["ig_user_id"]].append(l)
    flagged = [(g, *rate(group)) for g, group in by_guest.items()]
    flagged = [f for f in flagged if f[1] >= 2]
    lines += [f"  {g}: {ns} of {n} nights" for g, ns, n in sorted(flagged, key=lambda f: -f[1])] or ["  none"]
    return "\n".join(lines)
