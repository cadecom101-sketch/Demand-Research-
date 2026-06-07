"""Source-type detection helpers.

Phase agents now build their own research prompts and construct SourceCards
directly from extracted data (see agents/phase_agents.py). This module retains
the product-type heuristic used to bias which platforms a phase searches first.
"""

import logging

logger = logging.getLogger(__name__)


class SourceCollector:
    """Lightweight helpers for source/platform selection."""

    def detect_product_type(self, product_name: str, buyer: str, channel: str) -> str:
        """Classify the product to bias source priority.

        Returns one of: 'etsy_template' | 'github_tool' | 'gumroad' | 'multi_platform'.
        """
        indicators = {
            "etsy_template": ["etsy", "seller", "shop", "listing", "digital product", "template"],
            "github_tool": ["developer", "api", "lib", "sdk", "tool", "github", "open source"],
            "gumroad": ["gumroad", "creator", "coach", "service", "course"],
        }
        product_text = f"{product_name} {buyer} {channel}".lower()

        type_scores = {
            ptype: sum(1 for kw in keywords if kw in product_text)
            for ptype, keywords in indicators.items()
        }
        best_type = max(type_scores, key=type_scores.get)
        return best_type if type_scores[best_type] > 0 else "multi_platform"
