"""Generate a customer-facing PDF report from a LibreCrawl JSON export."""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Palette ──────────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#1a2e44")
BLUE    = colors.HexColor("#2563eb")
TEAL    = colors.HexColor("#0891b2")
GREEN   = colors.HexColor("#16a34a")
AMBER   = colors.HexColor("#d97706")
RED     = colors.HexColor("#dc2626")
LIGHT   = colors.HexColor("#f1f5f9")
BORDER  = colors.HexColor("#e2e8f0")
MUTED   = colors.HexColor("#64748b")
WHITE   = colors.white
BLACK   = colors.HexColor("#0f172a")


# ── Styles ────────────────────────────────────────────────────────────────────
def build_styles():
    base = getSampleStyleSheet()
    def s(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "h1": s("h1", fontName="Helvetica-Bold", fontSize=26, textColor=WHITE, spaceAfter=4),
        "h1sub": s("h1sub", fontName="Helvetica", fontSize=12, textColor=colors.HexColor("#93c5fd"), spaceAfter=0),
        "h2": s("h2", fontName="Helvetica-Bold", fontSize=14, textColor=NAVY, spaceBefore=14, spaceAfter=6),
        "h3": s("h3", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4),
        "body": s("body", fontName="Helvetica", fontSize=9.5, textColor=BLACK, leading=15, spaceAfter=6),
        "small": s("small", fontName="Helvetica", fontSize=8.5, textColor=MUTED, leading=13),
        "label": s("label", fontName="Helvetica-Bold", fontSize=8, textColor=MUTED, spaceAfter=2),
        "metric": s("metric", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY, spaceAfter=2),
        "score_hero": s("score_hero", fontName="Helvetica-Bold", fontSize=22, leading=46, textColor=NAVY, spaceAfter=2),
        "metric_label": s("metric_label", fontName="Helvetica", fontSize=8, textColor=MUTED),
        "url": s("url", fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=11),
        "fix": s("fix", fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#166534"), leading=13),
        "section_intro": s("section_intro", fontName="Helvetica", fontSize=9.5, textColor=MUTED, leading=15, spaceAfter=10),
        "footer": s("footer", fontName="Helvetica", fontSize=7.5, textColor=MUTED, alignment=TA_CENTER),
        "toc_item": s("toc_item", fontName="Helvetica", fontSize=9.5, textColor=NAVY, leading=18),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def hr(color=BORDER, thickness=0.75):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=8, spaceBefore=4)


def score_color(score):
    if score >= 80: return GREEN
    if score >= 55: return AMBER
    return RED


def score_label(score):
    if score >= 80: return "Good"
    if score >= 55: return "Needs Attention"
    return "Poor"


def severity_color(issue_type):
    return {"error": RED, "warning": AMBER}.get(issue_type, TEAL)


def severity_badge(issue_type):
    return {"error": "ERROR", "warning": "WARNING"}.get(issue_type, "INFO")


def issue_category_color(cat):
    return {
        "SEO": BLUE,
        "Technical": RED,
        "Duplication": AMBER,
        "Structured Data": TEAL,
        "Performance": AMBER,
        "Accessibility": colors.HexColor("#7c3aed"),
        "Content": NAVY,
        "Indexability": TEAL,
        "Mobile": BLUE,
        "Social": colors.HexColor("#7c3aed"),
    }.get(cat, MUTED)


REMEDIATION = {
    "Title Too Long": (
        "Rewrite titles to 50–60 characters. Focus on the primary keyword and brand name. "
        "Truncated titles in search results lose clicks."
    ),
    "Title Too Short": (
        "Expand titles to at least 30 characters. Include the main keyword and "
        "a differentiating phrase to improve click-through rates."
    ),
    "Meta Description Too Long": (
        "Trim meta descriptions to 120–155 characters. Google truncates longer ones, "
        "cutting off your call to action."
    ),
    "Meta Description Too Short": (
        "Write meta descriptions of at least 70 characters. Include a benefit-led "
        "summary and a call to action to lift click-through rates."
    ),
    "Missing Meta Description": (
        "Add a unique meta description to every page (120–155 chars). "
        "Although not a direct ranking factor, it strongly influences click-through rate."
    ),
    "Missing Title Tag": (
        "Every page must have a unique <title> tag. Missing titles hurt rankings "
        "and result in Google auto-generating one — usually poorly."
    ),
    "Duplicate Content Detected": (
        "Use canonical tags to indicate the preferred version of duplicate or near-duplicate pages. "
        "Consider consolidating thin pages or adding substantially unique content."
    ),
    "Canonical URL Different": (
        "Verify the canonical URL is intentional. If pages are self-canonicalising to a "
        "different URL, ensure that target is indexed and not itself canonicalised away."
    ),
    "No Structured Data": (
        "Add Schema.org markup (JSON-LD) to these pages. For a service business, "
        "LocalBusiness, Service and FAQPage schemas can unlock rich results in Google."
    ),
    "Missing H1 Tag": (
        "Add a single H1 tag per page that includes the primary keyword. "
        "The H1 is a strong relevance signal and helps users orient themselves."
    ),
    "Slow Response Time": (
        "Server response exceeds 1 second. Investigate hosting, caching and database queries. "
        "A sub-200ms TTFB is the target."
    ),
    "Images Without Alt Text": (
        "Add descriptive alt text to all images. This improves accessibility (WCAG 2.1) "
        "and provides Google with context for image indexing."
    ),
    "Missing OpenGraph Tags": (
        "Add og:title, og:description and og:image tags. These control how pages "
        "appear when shared on LinkedIn, Facebook and messaging apps."
    ),
    "Missing Twitter Card Tags": (
        "Add twitter:card, twitter:title and twitter:image tags so pages display "
        "a rich preview when shared on X/Twitter."
    ),
    "Thin Content": (
        "Expand these pages with substantive, genuinely useful content — aim for at least "
        "300 words of unique copy. Thin pages struggle to rank and can drag down site quality signals."
    ),
    "Missing Canonical URL": (
        "Add a self-referencing canonical tag to every indexable page. This protects against "
        "duplicate-content issues from URL parameters and tracking links."
    ),
    "Missing Viewport Meta Tag": (
        "Add <meta name='viewport' content='width=device-width, initial-scale=1'> to every page. "
        "Without it, pages render poorly on mobile and fail Google's mobile-friendly checks."
    ),
    "Missing Language Attribute": (
        "Add lang='en' (or the appropriate language) to the <html> tag. Screen readers rely on it "
        "to pronounce content correctly, and it helps search engines serve the right audience."
    ),
    "Large Page Size": (
        "Reduce page weight — compress images (WebP/AVIF), lazy-load below-the-fold media and "
        "trim unused CSS/JavaScript. Heavy pages hurt Core Web Vitals and mobile users."
    ),
    "Moderate Page Size": (
        "Page weight is acceptable but worth trimming. Compressing images and deferring "
        "non-critical scripts is usually the quickest win."
    ),
    "Moderate Response Time": (
        "Server response is acceptable but could be faster. Enabling caching or a CDN "
        "typically brings this under 200ms."
    ),
    "Noindex Tag Present": (
        "These pages tell search engines not to index them. This is often intentional "
        "(thank-you pages, admin areas) — verify each one is deliberately excluded from Google."
    ),
    "Nofollow Tag Present": (
        "These pages tell search engines not to follow their links, which stops link equity "
        "flowing through them. Verify this is intentional — it rarely should be on normal content pages."
    ),
}


def get_remediation(issue_name):
    if issue_name in REMEDIATION:
        return REMEDIATION[issue_name]
    if issue_name.endswith("Client Error"):
        return (
            "These pages return an error (e.g. 404 Not Found). Fix or remove internal links "
            "pointing to them, and add 301 redirects to the most relevant live page so "
            "visitors and link equity aren't lost."
        )
    if issue_name.endswith("Server Error"):
        return (
            "These pages return a server error. This needs urgent attention from your "
            "developer or host — pages that error are invisible to Google and lose visitors immediately."
        )
    if issue_name.endswith("Redirect"):
        return (
            "These URLs redirect elsewhere. Redirects are normal, but update internal links to "
            "point directly at the final destination to avoid unnecessary hops."
        )
    if issue_name.startswith("Broken Image"):
        return (
            "These images fail to load, leaving gaps in the page. Restore the missing files "
            "or update the image references to the correct paths."
        )
    return "Review each affected page and apply best practice guidance."


# ── Data analysis ─────────────────────────────────────────────────────────────
def analyse(data):
    urls = data["urls"]
    issues = data["issues"]
    internal = [u for u in urls if u.get("is_internal")]

    issue_counts = Counter(i["issue"] for i in issues)
    by_category  = Counter(i["category"] for i in issues)
    errors       = sum(1 for i in issues if i["type"] == "error")
    warnings     = sum(1 for i in issues if i["type"] == "warning")
    infos        = sum(1 for i in issues if i["type"] == "info")

    # Group affected URLs per issue type
    issue_urls = defaultdict(list)
    for i in issues:
        issue_urls[i["issue"]].append(i["url"])

    # Health score: only issues on the client's own pages count against them,
    # and info-level findings don't affect the score
    internal_set = {u["url"] for u in internal}
    scored = [i for i in issues if i["url"] in internal_set]
    s_errors   = sum(1 for i in scored if i["type"] == "error")
    s_warnings = sum(1 for i in scored if i["type"] == "warning")
    # Rational decay rather than a linear cliff: a penalty-per-page of 7
    # scores 50, and heavily-issued sites approach 0 without hitting it
    pages = len(internal) or 1
    penalty_per_page = (s_errors * 8 + s_warnings * 1.5) / pages
    score = round(100 / (1 + penalty_per_page / 7))

    # Response times
    times = [u["response_time"] for u in internal if u.get("response_time")]
    avg_time = round(sum(times) / len(times)) if times else 0

    # Schema coverage
    with_schema = sum(1 for u in internal if u.get("json_ld") or u.get("schema_org"))

    # Pages with OG tags
    with_og = sum(1 for u in internal if u.get("og_tags"))

    return {
        "score": score,
        "pages": len(internal),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "issue_counts": issue_counts,
        "by_category": by_category,
        "issue_urls": issue_urls,
        "avg_time": avg_time,
        "with_schema": with_schema,
        "with_og": with_og,
        "issues": issues,
    }


# ── Page template (header + footer) ───────────────────────────────────────────
class ReportTemplate(SimpleDocTemplate):
    def __init__(self, filename, client_name, report_date, **kw):
        super().__init__(filename, **kw)
        self.client_name = client_name
        self.report_date = report_date
        self._page = 0

    def handle_pageBegin(self):
        super().handle_pageBegin()
        self._page += 1

    def afterPage(self):
        canvas = self.canv
        W, H = A4
        canvas.saveState()

        # Footer bar
        canvas.setFillColor(LIGHT)
        canvas.rect(0, 0, W, 20 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 7 * mm, f"Website Audit — {self.client_name}")
        canvas.drawString(W / 2 - 20 * mm, 7 * mm, f"Prepared by SME Software Help")
        canvas.drawRightString(W - 15 * mm, 7 * mm, f"Page {self._page}")

        canvas.restoreState()


# ── Cover page ────────────────────────────────────────────────────────────────
def cover_page(st, client_name, base_url, report_date, score, a, toc_items):
    story = []
    W = A4[0]

    # We draw the header band via a 1-cell Table (avoids canvas calls)
    header_content = [
        [Paragraph("SME Software Help", st["h1"])],
        [Paragraph("Website Audit Report", st["h1sub"])],
    ]
    header_table = Table(header_content, colWidths=[W - 30 * mm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), NAVY),
        ("TOPPADDING",  (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 20),
        ("LEFTPADDING",  (0, 0), (-1, -1), 18),
        ("RIGHTPADDING", (0, 0), (-1, -1), 18),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14 * mm))

    # Client + date meta
    meta = Table([
        [Paragraph("<b>Prepared for</b>", st["label"]), Paragraph("<b>Website</b>", st["label"]), Paragraph("<b>Date</b>", st["label"])],
        [Paragraph(client_name, st["h3"]), Paragraph(base_url, st["body"]), Paragraph(report_date, st["body"])],
    ], colWidths=[70 * mm, 90 * mm, 40 * mm])
    meta.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING",  (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("LINEBELOW",   (0, 0), (-1, 0), 0.5, BORDER),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(meta)
    story.append(Spacer(1, 10 * mm))

    # Health score hero
    sc = score_color(score)
    sl = score_label(score)
    score_block = Table([[
        Paragraph(f"<font color='#{sc.hexval()[2:]}' size=40><b>{score}</b></font><font color='#64748b' size=14> / 100</font>", st["score_hero"]),
        Paragraph(f"<font color='#{sc.hexval()[2:]}'><b>{sl}</b></font><br/><font color='#64748b' size=8>Overall Site Health Score</font>", st["body"]),
    ]], colWidths=[60 * mm, 130 * mm])
    score_block.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING",  (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 16),
        ("LEFTPADDING",  (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBEFORE",  (0, 0), (0, -1), 4, sc),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(score_block)
    story.append(Spacer(1, 8 * mm))

    # Key stats row
    def stat_cell(value, label):
        return [Paragraph(str(value), st["metric"]), Paragraph(label, st["metric_label"])]

    stats = Table([
        [stat_cell(a["pages"], "Pages crawled"),
         stat_cell(a["errors"], "Errors found"),
         stat_cell(a["warnings"], "Warnings"),
         stat_cell(f"{a['avg_time']}ms", "Avg response time")],
    ], colWidths=[47 * mm] * 4)
    stats.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING",  (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
        ("LEFTPADDING",  (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("LINEAFTER",   (0, 0), (2, -1), 0.5, BORDER),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(stats)
    story.append(Spacer(1, 10 * mm))

    # What this report covers
    story.append(Paragraph("About this report", st["h2"]))
    story.append(hr())
    story.append(Paragraph(
        "This audit crawled every page of your website and analysed it against current SEO best practice, "
        "technical health standards and content quality signals. The findings are grouped by category and "
        "prioritised so you know exactly where to focus first. Each issue includes a plain-English explanation "
        "of why it matters and a concrete recommendation for fixing it.",
        st["body"]
    ))
    story.append(Spacer(1, 6 * mm))

    # Table of contents
    story.append(Paragraph("Contents", st["h2"]))
    story.append(hr())
    # Two columns so a report with many sections still fits on the cover
    half = (len(toc_items) + 1) // 2
    toc_rows = []
    for left, right in zip(toc_items[:half], toc_items[half:] + [None] * (2 * half - len(toc_items))):
        toc_rows.append([
            Paragraph(f"<b>{left[0]}</b>  {left[1]}", st["toc_item"]),
            Paragraph(f"<b>{right[0]}</b>  {right[1]}", st["toc_item"]) if right else "",
        ])
    toc_table = Table(toc_rows, colWidths=[90 * mm, 90 * mm])
    toc_table.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
    ]))
    story.append(toc_table)

    story.append(PageBreak())
    return story


# ── Executive summary ─────────────────────────────────────────────────────────
def executive_summary(st, a, data):
    story = []
    story.append(Paragraph("1. Executive Summary", st["h2"]))
    story.append(hr())
    story.append(Paragraph(
        f"The audit of <b>{data['stats']['baseUrl']}</b> crawled <b>{a['pages']} pages</b> and identified "
        f"<b>{a['errors']} errors</b>, <b>{a['warnings']} warnings</b> and <b>{a['infos']} informational findings</b> "
        f"across {len(a['issue_counts'])} distinct issue types. "
        f"The site scores <b>{a['score']}/100</b> for overall health ({score_label(a['score'])}).",
        st["body"]
    ))
    story.append(Spacer(1, 4 * mm))

    # Top 3 priorities callout
    story.append(Paragraph("Priority actions", st["h3"]))
    priorities = _top_priorities(a)
    for i, (issue, count, why) in enumerate(priorities, 1):
        cell = Table([[
            Paragraph(f"<b>{i}</b>", st["h3"]),
            [Paragraph(f"<b>{issue}</b> — {count} page{'s' if count>1 else ''}", st["body"]),
             Paragraph(why, st["small"])],
        ]], colWidths=[12 * mm, 168 * mm])
        cell.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
            ("TOPPADDING",  (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN",      (0, 0), (-1, -1), "TOP"),
            ("LINEBEFORE",  (0, 0), (0, -1), 4, BLUE),
        ]))
        story.append(cell)
        story.append(Spacer(1, 3 * mm))

    story.append(Spacer(1, 4 * mm))

    # Category breakdown table
    story.append(Paragraph("Issue breakdown by category", st["h3"]))
    headers = [
        Paragraph("<b>Category</b>", st["label"]),
        Paragraph("<b>Issues found</b>", st["label"]),
        Paragraph("<b>Impact</b>", st["label"]),
    ]
    impact_map = {
        "SEO": "Direct effect on search rankings and click-through rate",
        "Duplication": "Dilutes page authority and confuses search engines",
        "Technical": "Can prevent pages being indexed or crawled",
        "Structured Data": "Missed opportunity for rich results in Google",
        "Performance": "Affects user experience and Core Web Vitals score",
        "Content": "Thin pages struggle to rank and weaken site quality signals",
        "Indexability": "Controls which pages appear in Google — verify exclusions are intentional",
        "Mobile": "Affects mobile rendering and Google's mobile-first indexing",
        "Accessibility": "Affects users with disabilities and WCAG compliance",
        "Social": "Controls how pages look when shared on social platforms",
    }
    rows = [headers]
    for cat, count in a["by_category"].most_common():
        c = issue_category_color(cat)
        rows.append([
            Paragraph(f"<font color='#{c.hexval()[2:]}'><b>{cat}</b></font>", st["body"]),
            Paragraph(str(count), st["body"]),
            Paragraph(impact_map.get(cat, ""), st["small"]),
        ])
    t = Table(rows, colWidths=[45 * mm, 30 * mm, 105 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",   (0, 0), (-1, 0), WHITE),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 8),
        ("BACKGROUND",  (0, 1), (-1, -1), WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT]),
        ("GRID",        (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",  (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(PageBreak())
    return story


def _top_priorities(a):
    order = [
        "Canonical URL Different",
        "Missing Title Tag",
        "Missing Meta Description",
        "No Structured Data",
        "Title Too Long",
        "Meta Description Too Long",
        "Duplicate Content Detected",
        "Images Without Alt Text",
        "Slow Response Time",
    ]
    results = []
    for issue in order:
        if issue in a["issue_counts"] and len(results) < 3:
            count = a["issue_counts"][issue]
            why = get_remediation(issue)[:120]
            results.append((issue, count, why))
    return results


# ── Issue section ──────────────────────────────────────────────────────────────
def issue_section(st, section_num, title, category, issues_subset, a, intro=None):
    story = []
    story.append(Paragraph(f"{section_num}. {title}", st["h2"]))
    story.append(hr())
    if intro:
        story.append(Paragraph(intro, st["section_intro"]))

    if not issues_subset:
        story.append(Paragraph("No issues found in this category.", st["body"]))
        story.append(PageBreak())
        return story

    # Group by issue type
    grouped = defaultdict(list)
    for i in issues_subset:
        grouped[i["issue"]].append(i)

    for issue_name, items in sorted(grouped.items(), key=lambda x: -len(x[1])):
        count = len(items)
        severity = items[0]["type"]
        sc = severity_color(severity)
        badge = severity_badge(severity)
        remedy = get_remediation(issue_name)

        block = [
            # Issue header row
            Table([[
                Paragraph(f"<font color='#{sc.hexval()[2:]}'><b>● {badge}</b></font>", st["small"]),
                Paragraph(f"<b>{issue_name}</b>", st["h3"]),
                Paragraph(f"<b>{count}</b> page{'s' if count>1 else ''} affected", st["label"]),
            ]], colWidths=[22 * mm, 128 * mm, 30 * mm]),
            Spacer(1, 2 * mm),
            # Remedy
            Table([[
                Paragraph("How to fix it", st["label"]),
            ], [
                Paragraph(remedy, st["fix"]),
            ]], colWidths=[180 * mm]),
            Spacer(1, 3 * mm),
        ]

        # Affected URLs (cap at 12 to keep report readable)
        url_list = sorted(set(i["url"] for i in items))
        if url_list:
            shown = url_list[:12]
            url_rows = [[Paragraph(u, st["url"])] for u in shown]
            if len(url_list) > 12:
                url_rows.append([Paragraph(f"… and {len(url_list)-12} more pages", st["small"])])
            url_table = Table(url_rows, colWidths=[180 * mm])
            url_table.setStyle(TableStyle([
                ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
                ("TOPPADDING",  (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            block.append(url_table)
            block.append(Spacer(1, 2 * mm))

        block.append(hr(BORDER, 0.5))
        story.append(KeepTogether(block))

    story.append(PageBreak())
    return story


# ── Next steps ────────────────────────────────────────────────────────────────
def next_steps(st, score, section_num):
    story = []
    story.append(Paragraph(f"{section_num}. Next Steps", st["h2"]))
    story.append(hr())
    story.append(Paragraph(
        "Based on the findings above, here is a recommended order of work:",
        st["section_intro"]
    ))

    steps = [
        ("Week 1", "Fix critical errors", "Address all errors first — broken canonicals, missing titles and any 4xx/5xx pages. These are the highest-impact, lowest-effort fixes."),
        ("Week 2–3", "Meta data & headings", "Rewrite over-long or missing titles and meta descriptions. Prioritise your highest-traffic pages first (check Google Search Console for impressions)."),
        ("Week 3–4", "Structured data", "Add JSON-LD schema to all service, case study and blog pages. Use Google's Rich Results Test to validate."),
        ("Ongoing", "Content consolidation", "Review near-duplicate pages identified in the Duplicate Content section. Consolidate or differentiate with unique, substantive content."),
        ("Month 2+", "Performance & Core Web Vitals", "Once SEO fundamentals are solid, focus on page speed. Aim for LCP < 2.5s, CLS < 0.1, INP < 200ms (check PageSpeed Insights per page)."),
    ]

    for period, heading, detail in steps:
        row = Table([[
            Paragraph(period, st["label"]),
            [Paragraph(f"<b>{heading}</b>", st["body"]), Paragraph(detail, st["small"])],
        ]], colWidths=[28 * mm, 152 * mm])
        row.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, -1), LIGHT),
            ("TOPPADDING",  (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN",      (0, 0), (-1, -1), "TOP"),
            ("LINEBEFORE",  (0, 0), (0, -1), 4, TEAL),
        ]))
        story.append(row)
        story.append(Spacer(1, 3 * mm))

    story.append(Spacer(1, 8 * mm))
    story.append(hr())
    story.append(Paragraph(
        "This report was prepared by <b>SME Software Help</b>. "
        "For questions or to discuss implementation, get in touch at hello@smesoftwarehelp.co.uk.",
        st["small"]
    ))
    return story


# ── Section registry ──────────────────────────────────────────────────────────
# Preferred order and display config for every category the crawler can emit.
# Categories with no issues in the export are skipped; unknown ones are appended.
CATEGORY_SECTIONS = [
    ("SEO", "SEO Issues", None),
    ("Technical", "Technical Issues", None),
    ("Indexability", "Indexability",
     "These findings control which pages search engines are allowed to index. "
     "Exclusions are often deliberate (e.g. thank-you or admin pages) — each one "
     "should be verified rather than blindly 'fixed'."),
    ("Content", "Content Quality", None),
    ("Duplication", "Duplicate Content", None),
    ("Structured Data", "Structured Data", None),
    ("Performance", "Performance", None),
    ("Mobile", "Mobile Usability", None),
    ("Accessibility", "Accessibility", None),
    ("Social", "Social Sharing", None),
]


# ── Main ──────────────────────────────────────────────────────────────────────
def generate(json_path, output_path, client_name="Your Business"):
    data = json.loads(Path(json_path).read_text())
    a = analyse(data)

    report_date = datetime.now().strftime("%-d %B %Y")
    base_url = data["stats"]["baseUrl"]

    doc = ReportTemplate(
        str(output_path),
        client_name=client_name,
        report_date=report_date,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=22 * mm,
        title=f"Website Audit — {client_name}",
        author="SME Software Help",
    )

    st = build_styles()
    issues = data["issues"]

    by_cat = defaultdict(list)
    for i in issues:
        by_cat[i["category"]].append(i)

    # Sections for every category present in the export, in registry order,
    # with any category the registry doesn't know about appended at the end
    known = {cat for cat, _, _ in CATEGORY_SECTIONS}
    sections = [(cat, title, intro) for cat, title, intro in CATEGORY_SECTIONS if by_cat.get(cat)]
    sections += [(cat, cat, None) for cat in sorted(by_cat) if cat not in known]

    toc_items = [("1.", "Executive Summary")]
    toc_items += [(f"{n}.", title) for n, (_, title, _) in enumerate(sections, start=2)]
    next_steps_num = len(sections) + 2
    toc_items.append((f"{next_steps_num}.", "Next Steps"))

    story = []
    story += cover_page(st, client_name, base_url, report_date, a["score"], a, toc_items)
    story += executive_summary(st, a, data)
    for n, (cat, title, intro) in enumerate(sections, start=2):
        story += issue_section(st, n, title, cat, by_cat[cat], a, intro=intro)
    story += next_steps(st, a["score"], next_steps_num)

    doc.build(story)
    print(f"Report saved: {output_path}")


if __name__ == "__main__":
    json_file = sys.argv[1] if len(sys.argv) > 1 else "/home/james/Downloads/librecrawl_smesoftwarehelp.co.uk_2026-06-28T09-05-15.json"
    out_file  = sys.argv[2] if len(sys.argv) > 2 else "/home/james/Downloads/smesoftwarehelp_audit.pdf"
    client    = sys.argv[3] if len(sys.argv) > 3 else "SME Software Help"
    generate(json_file, out_file, client)
