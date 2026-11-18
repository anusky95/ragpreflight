"""Regenerate the bundled demo corpus (src/ragpreflight/demo/).

SkyFare Air is fictional. The planted failures mirror real incidents: a stale refund policy
that contradicts the current one (Moffatt v. Air Canada, 2024), a scanned form, a fee table,
personal data in support notes, and documents with no date or owner.

    python scripts/make_demo.py      # needs pillow and python-docx
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "ragpreflight" / "demo"

FILLER_2024 = (
    "SkyFare Air wants every customer to understand how fares, changes and refunds work before they book. "
    "This policy applies to tickets purchased on skyfare-air.com, through the SkyFare app, or by phone. "
    "Fares are priced per passenger and include taxes and carrier-imposed fees shown at checkout. "
    "Basic fares cannot be changed after the 24-hour cancellation window closes, while Standard and Flex fares "
    "can be changed online for the fare difference. Flex fares may also be refunded to the original form of "
    "payment if the request is made at least two hours before departure. "
)

POLICY_2022 = """---
title: Fares and Refunds Policy
owner: Customer Policy Team
updated: 2022-03-01
---

# Fares and Refunds Policy

{filler}

## Bereavement fares

Bereavement fares are offered to immediate family members travelling because of a death in the family.
Customers may apply for the reduced bereavement fare up to 90 days after travel by submitting the online request form with a death certificate.
Approved refunds are processed within 30 days of the request.

## Contact

Questions can be sent to the Customer Relations team through the website.
"""

POLICY_2024 = """---
title: Fares and Refunds Policy
owner: Customer Policy Team
updated: 2024-06-01
---

# Fares and Refunds Policy

{filler}

## Bereavement fares

Bereavement fares are offered to immediate family members travelling because of a death in the family, and must be requested before travel.
Customers may not apply for the reduced bereavement fare after travel has been completed.
Approved refunds are processed within 14 days of the request.

## Contact

Questions can be sent to the Customer Relations team through the website.
"""

FARES_HTML = """<!doctype html><html><head><title>Fees at a glance</title>
<meta name="author" content="Revenue Management"><meta name="date" content="2024-05-10"></head><body>
<h1>Fees at a glance</h1>
<p>All amounts in USD per passenger, per direction.</p>
<table>
<tr><th>Fee</th><th>Basic</th><th>Standard</th><th>Flex</th><th>Elite</th></tr>
<tr><td>Change fee</td><td>n/a</td><td>75.00</td><td>0.00</td><td>0.00</td></tr>
<tr><td>Same-day standby</td><td>n/a</td><td>50.00</td><td>25.00</td><td>0.00</td></tr>
<tr><td>1st checked bag</td><td>40.00</td><td>35.00</td><td>0.00</td><td>0.00</td></tr>
<tr><td>2nd checked bag</td><td>55.00</td><td>45.00</td><td>45.00</td><td>0.00</td></tr>
<tr><td>Overweight 51-70 lb</td><td>100.00</td><td>100.00</td><td>100.00</td><td>50.00</td></tr>
<tr><td>Seat selection</td><td>12.00-49.00</td><td>0.00</td><td>0.00</td><td>0.00</td></tr>
<tr><td>Pet in cabin</td><td>125.00</td><td>125.00</td><td>125.00</td><td>100.00</td></tr>
</table>
<p>Fees are subject to change. See the Fares and Refunds Policy for eligibility.</p>
</body></html>
"""

NOTES_TXT = """Support escalation notes - week 22

Case 48213: customer Jordan Reyes (jordan.reyes84@gmail.com, 415-555-0187) asked about a bereavement refund after travel.
Agent promised a callback. Customer verified identity with SSN 219-09-9999 - DO NOT store this, remove after review.
Case 48219: card on file 4111 1111 1111 1111 declined twice, customer asked us to retry.

Tooling: the refund lookup script uses api_key sk-proj-Z3x9Q2mVb7Lw4Kp8Rt6Yn1Hs5Df0Gj2Ac4Ee6Ii8Oo
Escalate unresolved cases to the duty manager.
"""

LOUNGE_MD = """# Lounge access

SkyFare Lounges are open to Elite members, Flex fare passengers on international itineraries,
and guests holding a valid day pass. Day passes cost 59 USD and can be bought in the app.
Each eligible passenger may bring one guest. Children under 2 enter free with an adult.
Lounges close 30 minutes before the last scheduled departure from that terminal.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refund-policy-2022.md").write_text(POLICY_2022.format(filler=FILLER_2024 * 3), encoding="utf-8")
    (OUT / "refund-policy-2024.md").write_text(POLICY_2024.format(filler=FILLER_2024 * 3), encoding="utf-8")
    (OUT / "fees-at-a-glance.html").write_text(FARES_HTML, encoding="utf-8")
    (OUT / "support-escalation-notes.txt").write_text(NOTES_TXT, encoding="utf-8")
    (OUT / "lounge-access.md").write_text(LOUNGE_MD, encoding="utf-8")

    # A scanned form: an image-only PDF with no text layer.
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(["SKYFARE AIR - DAMAGED BAGGAGE CLAIM FORM", "",
                              "Claims must be filed within 7 days of arrival.",
                              "Attach your bag tag and boarding pass.", "Compensation is capped at 3,800 USD."]):
        d.text((120, 160 + i * 60), line, fill="black")
    img.save(OUT / "baggage-claim-form-scanned.pdf", "PDF", resolution=150)

    # A Word document with placeholder metadata.
    import docx

    doc = docx.Document()
    doc.add_heading("Checked baggage allowance", 1)
    doc.add_paragraph("Standard and Flex fares include one checked bag up to 50 lb. Elite members may check two bags. "
                      "Bags over 62 linear inches are oversized and incur a fee shown in Fees at a glance.")
    from datetime import datetime

    doc.core_properties.author = "user"
    # python-docx's template carries a 2013 date; give the demo a real one
    doc.core_properties.created = doc.core_properties.modified = datetime(2024, 4, 2)
    doc.core_properties.title = "Document1"
    doc.save(OUT / "baggage-allowance.docx")
    print("demo written to", OUT)


if __name__ == "__main__":
    main()
