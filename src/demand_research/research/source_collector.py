"""Source collection using Claude Agent SDK with real web research."""

import logging
from typing import Optional
from datetime import datetime
from demand_research.models import SourceCard
from pydantic import HttpUrl

logger = logging.getLogger(__name__)


class SourceCollector:
    """Collects research sources from real market sources."""

    def __init__(self):
        self.session_id: Optional[str] = None

    def detect_product_type(
        self,
        product_name: str,
        buyer: str,
        channel: str,
    ) -> str:
        """
        Detect product type to inform source priority.
        Returns: 'etsy_template' | 'github_tool' | 'gumroad' | 'multi_platform'
        """
        indicators = {
            "etsy_template": ["etsy", "seller", "shop", "listing", "digital product", "template"],
            "github_tool": ["developer", "api", "lib", "sdk", "tool", "github", "open source"],
            "gumroad": ["gumroad", "creator", "coach", "service", "template", "course"],
        }

        product_text = f"{product_name.lower()} {buyer.lower()} {channel.lower()}".lower()

        type_scores = {}
        for ptype, keywords in indicators.items():
            score = sum(1 for keyword in keywords if keyword in product_text)
            type_scores[ptype] = score

        best_type = max(type_scores, key=type_scores.get)
        if type_scores[best_type] > 0:
            return best_type
        return "multi_platform"

    async def create_source_card(
        self,
        source_number: int,
        source_name: str,
        url: str,
        platform: str,
        what_it_proves: str,
        what_it_does_not_prove: str,
        search_phrase: Optional[str] = None,
        price: Optional[float] = None,
        buyer_language: Optional[str] = None,
        screenshot_filename: Optional[str] = None,
        is_direct_quote: Optional[bool] = None,
    ) -> SourceCard:
        """Create a source card with validation."""
        try:
            return SourceCard(
                source_number=source_number,
                source_name=source_name,
                url=HttpUrl(url),
                date_observed=datetime.utcnow(),
                platform=platform,
                search_phrase_used=search_phrase,
                price_observed=price,
                buyer_language_captured=buyer_language,
                what_this_proves=what_it_proves,
                what_this_does_not_prove=what_it_does_not_prove,
                screenshot_filename=screenshot_filename,
                is_direct_quote=is_direct_quote,
            )
        except Exception as e:
            logger.error(f"Failed to create source card: {e}")
            raise

    def get_etsy_search_prompt(self, query: str) -> str:
        """Generate prompt for searching Etsy."""
        return f"""Search Etsy for: "{query}"

Focus on:
- Digital product listings
- Templates and tools
- Similar product category
- Prices
- Product descriptions
- Number of reviews and ratings

Return structured data:
1. Product title
2. Shop name
3. Price
4. Number of reviews
5. Product description summary
6. Direct product URL
7. What buyer pain points are mentioned

Look for 3-5 real listings."""

    def get_competitor_search_prompt(self, query: str) -> str:
        """Generate prompt for searching competitor products."""
        return f"""Find competitor products for: "{query}"

Search across:
- Etsy
- Notion Marketplace
- Gumroad
- Similar marketplaces

For each product found, collect:
1. Product name
2. Price
3. Target buyer
4. Main promise/description
5. Features included
6. Direct URL
7. Review/rating summary (if available)

Find 3-5 real, distinct products."""

    def get_buyer_language_prompt(self, product_category: str) -> str:
        """Generate prompt for mining buyer language."""
        return f"""Find real buyer language about: {product_category}

Search in:
- Etsy product reviews
- Reddit threads (r/entrepreneur, r/smallbusiness, r/ecommerce, etc.)
- Notion Marketplace reviews
- YouTube comments on competitor products
- Forum discussions

For each quote/pain point:
1. Exact quote (verbatim)
2. Who said it (reviewer name, forum user, etc.)
3. Source URL
4. Date posted
5. What pain this reveals
6. Context

Find 3-5 direct quotes showing real buyer pain or needs."""
