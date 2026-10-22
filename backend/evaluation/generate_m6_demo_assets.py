"""Generate clearly synthetic, locally licensed M6 demonstration papers."""

from pathlib import Path

import pymupdf


ROOT = Path(__file__).parent / "assets"
PAPERS = {
    "synthetic_pilot_a.pdf": (
        "SYNTHETIC DEMONSTRATION PAPER A\n"
        "Training outcome pilot with a convenience sample\n\n"
        "This document is generated test material, not a real publication or scientific finding.\n"
        "In a fictional small pilot, one group practiced method A and one group practiced method B.\n"
        "The recorded task score tended to be higher in the method A group in this invented example.\n"
        "The groups were not randomly assigned. No formal hypothesis test was reported.\n"
        "Limitations: convenience sampling, small sample, and uncertain baseline comparability.\n"
    ),
    "synthetic_pilot_b.pdf": (
        "SYNTHETIC DEMONSTRATION PAPER B\n"
        "A second fictional training outcome pilot\n\n"
        "This document is generated test material, not a real publication or scientific finding.\n"
        "In another invented pilot, groups using method A and method B had overlapping score ranges.\n"
        "The fictional data are insufficient to establish a stable difference between methods.\n"
        "No preregistered replication or causal design was available in this invented record.\n"
        "Limitations: small sample and uncertain measurement reliability.\n"
    ),
}


def main() -> None:
    ROOT.mkdir(exist_ok=True)
    for filename, content in PAPERS.items():
        document = pymupdf.open()
        page = document.new_page(width=595, height=842)
        page.insert_textbox(pymupdf.Rect(55, 55, 540, 780), content, fontsize=12, lineheight=1.45)
        document.set_metadata({"title": filename.removesuffix(".pdf"), "author": "溯理 SULI synthetic fixture",
            "creationDate": "D:20261005000000+00'00'", "modDate": "D:20261005000000+00'00'"})
        document.save(ROOT / filename, garbage=4, deflate=True, no_new_id=True)
        document.close()


if __name__ == "__main__":
    main()
