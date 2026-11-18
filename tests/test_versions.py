import random
import time
from pathlib import Path

from ragpreflight.checks import versions
from ragpreflight.models import Document, Page, Severity

ROOT = Path(__file__).resolve().parent  # a real directory, so rel() strips it


def doc(name, text, fmt="md", **meta):
    return Document(ROOT / name, fmt, [Page(1, text)], dict(meta))


def by_check(findings, check):
    return [f for f in findings if f.check == check]


INTRO = (
    "SkyFare Air wants every customer to understand how fares, changes and refunds work before they book. "
    "This policy applies to tickets purchased on the website, through the SkyFare app, or by phone. "
    "Fares are priced per passenger and include taxes and carrier-imposed fees shown at checkout. "
    "Basic fares cannot be changed after the cancellation window closes, while Standard and Flex fares "
    "can be changed online for the fare difference. Checked bags cost $35 each on domestic routes.\n\n"
)
POLICY_2022 = INTRO + (
    "## Bereavement fares\n\n"
    "Bereavement fares are offered to immediate family members travelling because of a death in the family.\n"
    "Customers may apply for the reduced bereavement fare up to 90 days after travel by submitting the "
    "online request form with a death certificate.\n"
    "Approved refunds are processed within 30 days of the request.\n"
)
POLICY_2024 = INTRO + (
    "## Bereavement fares\n\n"
    "Bereavement fares are offered to immediate family members travelling because of a death in the family, "
    "and must be requested before travel.\n"
    "Customers may not apply for the reduced bereavement fare after travel has been completed.\n"
    "Approved refunds are processed within 14 days of the request.\n"
)


def test_airline_policy_stale_and_conflicts():
    old = doc("refund-policy-2022.md", POLICY_2022, date="2022-03-01")
    new = doc("refund-policy-2024.md", POLICY_2024, date="2024-06-01")
    fs = versions.run([new, old], ROOT)

    [stale] = by_check(fs, "version-stale")
    assert stale.severity == Severity.ERROR
    assert stale.path == "refund-policy-2022.md"
    assert stale.related == ["refund-policy-2024.md"]
    assert stale.f_codes == ["F1"]
    assert "refund-policy-2024.md" in stale.title

    conflicts = by_check(fs, "version-conflict")
    assert conflicts and all(c.severity == Severity.ERROR for c in conflicts)
    assert all(c.f_codes == ["F14", "F1"] and c.path == "refund-policy-2022.md" for c in conflicts)
    text = " ".join(c.title + c.detail for c in conflicts)
    assert "90 days" in text
    assert any("30" in c.title and "14" in c.title for c in conflicts)
    # unchanged sentences (the $35 bag fee, the intro) must not be reported
    assert "$35" not in text
    assert len(conflicts) == 2


def test_dates_from_filenames_when_no_metadata():
    old = doc("refund-policy-2022.md", POLICY_2022)
    new = doc("refund-policy-2024.md", POLICY_2024)
    [stale] = by_check(versions.run([old, new], ROOT), "version-stale")
    assert stale.path == "refund-policy-2022.md"
    assert "from filename" in stale.detail


def test_dates_from_effective_line_in_text():
    old = doc("refunds.md", "Effective: March 1, 2022\n\n" + POLICY_2022)
    new = doc("refunds-final.md", "Effective: June 1, 2024\n\n" + POLICY_2024)
    [stale] = by_check(versions.run([old, new], ROOT), "version-stale")
    assert stale.path == "refunds.md" and "from text" in stale.detail


def test_no_dates_no_stale_but_conflict_still_reported():
    a = doc("refunds.md", POLICY_2022)
    b = doc("refunds-copy.md", POLICY_2024)
    fs = versions.run([a, b], ROOT)
    assert by_check(fs, "version-stale") == []
    conflicts = by_check(fs, "version-conflict")
    assert conflicts and "Could not tell which file is newer" in conflicts[0].detail


def test_superseded_status_downgrades_to_info():
    old = doc("refund-policy-2022.md", POLICY_2022, date="2022-03-01", status="superseded")
    new = doc("refund-policy-2024.md", POLICY_2024, date="2024-06-01")
    fs = versions.run([old, new], ROOT)
    [stale] = by_check(fs, "version-stale")
    assert stale.severity == Severity.INFO
    assert "already marked" in stale.title
    assert by_check(fs, "version-conflict") == []


def test_stale_without_conflict_is_warn():
    old = doc("handbook-v1.md", POLICY_2022 + "\nThe office opens at nine.\n")
    new = doc("handbook-v2.md", POLICY_2022 + "\nThe office opens at nine. Visitors sign in at the desk.\n")
    fs = versions.run([old, new], ROOT)
    [stale] = by_check(fs, "version-stale")
    assert stale.severity == Severity.WARN and stale.path == "handbook-v1.md"
    assert by_check(fs, "version-conflict") == []


def test_exact_duplicates():
    a = doc("lounge-access.md", POLICY_2024)
    b = doc("backup/lounge-access (1).md", POLICY_2024)
    fs = versions.run([a, b], ROOT)
    [dup] = by_check(fs, "duplicate")
    assert dup.severity == Severity.WARN and dup.f_codes == ["F1"]
    assert dup.title == "Exact duplicate of backup/lounge-access (1).md" or \
        dup.title == "Exact duplicate of lounge-access.md"
    # duplicates are not additionally reported as stale versions
    assert by_check(fs, "version-stale") == [] and by_check(fs, "version-conflict") == []


def _words(seed, n):
    rng = random.Random(seed)
    vocab = [f"w{i}" for i in range(3000)]
    return " ".join(rng.choice(vocab) for _ in range(n))


def test_unrelated_documents_have_no_findings():
    docs = [
        doc("refund-policy.md", POLICY_2024),
        doc("lounge-access.md", "Lounges are open to Elite members and Flex passengers on international "
            "itineraries. Day passes cost 59 USD and can be bought in the app. Each eligible passenger may "
            "bring one guest. Lounges close 30 minutes before the last departure from that terminal."),
        doc("notes.txt", _words(1, 300), fmt="txt"),
        doc("other.txt", _words(2, 300), fmt="txt"),
    ]
    assert versions.run(docs, ROOT) == []


def test_template_instances_with_different_names_are_not_versions():
    body = ("This Mutual Non-Disclosure Agreement is entered into by the parties named below. Each party "
            "agrees to keep the other party's confidential information secret and to use it only to "
            "evaluate the proposed business relationship. The receiving party shall protect the information "
            "with the same care it uses for its own confidential information. This agreement lasts {n} years "
            "from the effective date. Liability under this agreement is capped at ${amt}.")
    a = doc("nda-acme.md", "Party: Acme Corp.\n\n" + body.format(n=2, amt="50,000"))
    b = doc("nda-globex.md", "Party: Globex Ltd.\n\n" + body.format(n=3, amt="75,000"))
    assert versions.run([a, b], ROOT) == []


TENK_COMMON = (
    "Acme Corporation designs, manufactures and sells industrial pumps and valves worldwide. "
    "Our business is organised into two segments, Fluid Systems and Flow Control. "
    "We compete on product quality, service and price, and our customers include utilities and refiners. "
    "Risk factors include changes in raw material costs, currency movements and regulation. "
)


def tenk(year, revenue, staff):
    return (f"Annual report on Form 10-K for the fiscal year ended December 31, {year}.\n\n" + TENK_COMMON
            + f"Total revenue for fiscal {year} was ${revenue} million. We employed {staff} people at year end. "
            + _words(year, 120))


def test_10k_period_twins_not_stale():
    docs = [doc(f"acme-10k-{y}.pdf", tenk(y, r, s), fmt="pdf")
            for y, r, s in [(2021, 410, 1200), (2022, 455, 1310), (2023, 498, 1405)]]
    fs = versions.run(docs, ROOT)
    assert by_check(fs, "version-stale") == []
    assert by_check(fs, "version-conflict") == []
    [twins] = by_check(fs, "version-period-twins")
    assert twins.severity == Severity.WARN
    assert twins.f_codes == ["F1", "F16"]
    assert twins.title.startswith("acme-10k: 3 period versions (2021–2023)")


def test_two_period_twins_is_info():
    docs = [doc(f"acme-10k-{y}.pdf", tenk(y, r, s), fmt="pdf") for y, r, s in [(2022, 455, 1310), (2023, 498, 1405)]]
    [twins] = by_check(versions.run(docs, ROOT), "version-period-twins")
    assert twins.severity == Severity.INFO


def test_section_numbers_and_version_years_are_not_conflicts():
    base = ("3.2 Refund requests must be submitted through the online portal by the ticket holder. "
            "See section 4 for details about the refund request form and supporting documents.\n"
            "This {y} policy covers refund requests submitted through the online portal by ticket holders.\n")
    old = doc("refund-policy-2022.md", base.format(y=2022) + POLICY_2022.split("## ")[0])
    new = doc("refund-policy-2024.md", base.replace("section 4", "section 5").replace("3.2", "4.1").format(y=2024)
              + POLICY_2022.split("## ")[0])
    fs = versions.run([old, new], ROOT)
    assert by_check(fs, "version-conflict") == []
    [stale] = by_check(fs, "version-stale")
    assert stale.severity == Severity.WARN


def test_conflict_cap_per_family():
    lines_old = [f"Fee schedule item for service category {c} costs ${10 + i} per booking at the counter."
                 for i, c in enumerate("abcdefgh")]
    lines_new = [f"Fee schedule item for service category {c} costs ${50 + i} per booking at the counter."
                 for i, c in enumerate("abcdefgh")]
    old = doc("fees-2022.md", INTRO + "\n".join(lines_old), date="2022-01-01")
    new = doc("fees-2024.md", INTRO + "\n".join(lines_new), date="2024-01-01")
    conflicts = by_check(versions.run([old, new], ROOT), "version-conflict")
    assert len(conflicts) == versions.MAX_CONFLICTS_PER_FAMILY
    assert "8 conflicting statements" in conflicts[-1].detail


def test_no_crash_on_empty_and_single_docs():
    assert versions.run([], ROOT) == []
    assert versions.run([doc("a.md", "")], ROOT) == []
    assert versions.run([doc("a.md", POLICY_2022)], ROOT) == []
    odd = [doc("empty.md", ""), doc("empty-v2.md", ""), doc("x.md", "   \n\n "),
           Document(ROOT / "nopages.pdf", "pdf", [], {}),
           doc("2024.md", "short"), doc("final.md", "short")]
    fs = versions.run(odd, ROOT)
    assert all(f.check != "versions-crashed" for f in fs)


def test_deterministic():
    docs = [doc("refund-policy-2022.md", POLICY_2022), doc("refund-policy-2024.md", POLICY_2024),
            doc("dup.md", POLICY_2024)]
    a = [f.to_dict() for f in versions.run(docs, ROOT)]
    b = [f.to_dict() for f in versions.run(list(reversed(docs)), ROOT)]
    assert a == b


def test_performance_1500_docs():
    rng = random.Random(42)
    vocab = [f"term{i}" for i in range(5000)]
    docs = []
    for i in range(1500):
        words = [rng.choice(vocab) for _ in range(rng.randint(200, 1200))]
        text = ". ".join(" ".join(words[k:k + 12]) for k in range(0, len(words), 12))
        docs.append(doc(f"dir{i % 30}/doc-{i}-{rng.choice(['notes', 'report', 'policy', 'memo'])}.md", text))
    # a few planted versions so the slow paths run too
    for k in range(10):
        docs.append(doc(f"planted/refund-policy-{k}-2022.md", POLICY_2022 + _words(k, 200)))
        docs.append(doc(f"planted/refund-policy-{k}-2024.md", POLICY_2024 + _words(k, 200)))
    t = time.perf_counter()
    fs = versions.run(docs, ROOT)
    elapsed = time.perf_counter() - t
    assert elapsed < 10, elapsed
    stale = by_check(fs, "version-stale")
    assert len(stale) == 10
    assert all(f.path.startswith("planted/") for f in fs if f.check != "versions-crashed")


def test_ids_times_and_phone_numbers_are_not_conflicts():
    tmpl = ("Meeting invitation for the architecture discussion with the platform team.\n"
            "Join by phone at {phone} PIN: {pin} and more phone numbers are listed in the calendar.\n"
            "The session starts on Feb {day}, 2026 at {time} PM and runs for about one hour.\n"
            "Please review the attached design notes before the session and bring your questions.\n")
    a = doc("invite-2026-02-09.md", INTRO + tmpl.format(phone="216-525-9227", pin="268229107", day=9, time="7:07"),
            date="2026-02-09")
    b = doc("invite-2026-02-12.md", INTRO + tmpl.format(phone="650-667-2746", pin="262675317", day=12, time="10:56"),
            date="2026-02-12")
    assert by_check(versions.run([a, b], ROOT), "version-conflict") == []


def test_reframed_sentence_is_not_a_negation_conflict():
    a = doc("email-agent-post-v2.md", INTRO + "The app checks what the email service actually returned.\n",
            date="2025-01-01")
    b = doc("email-agent-post-final.md", INTRO + "If the app never checks what the email service returned, "
            "failures go unnoticed.\n", date="2025-02-01")
    assert by_check(versions.run([a, b], ROOT), "version-conflict") == []


def test_renamed_copy_found_by_text_similarity():
    old = doc("policies/refund-policy.md", POLICY_2022, date="2022-03-01")
    new = doc("current/refund-policy-airline.md", POLICY_2024, date="2024-06-01")
    fs = versions.run([old, new], ROOT)
    [stale] = by_check(fs, "version-stale")
    assert stale.path == "policies/refund-policy.md" and stale.severity == Severity.ERROR
