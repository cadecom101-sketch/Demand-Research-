"""Optional, passive screenshot capture for audit completeness (Priority C).

Screenshots document accepted public evidence pages; they are part of the
audit record, never the evidence itself:

  - Screenshot success alone can NEVER satisfy a gate.
  - Screenshot failure alone can NEVER invent or invalidate evidence.
  - When capture is technically available, accepted public evidence without a
    screenshot is marked audit-incomplete (reporting, not a gate input).
  - When capture is unavailable / not configured, that is recorded honestly
    and the run continues — no crash, no fabricated screenshots.

The capture layer is optional and off by default (ENABLE_SCREENSHOTS). It uses
Playwright when installed; otherwise a no-op implementation reports exactly
why nothing was captured. Tests use fakes — no browser install is required.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

_DEFAULT_VIEWPORT = "1280x720"
_CAPTURE_TIMEOUT_MS = 20000


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename(url: str) -> str:
    """Deterministic, filesystem-safe screenshot filename for a URL."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    stem = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:60]
    return f"{stem}-{digest}.png"


@dataclass
class ScreenshotResult:
    """Outcome of one capture attempt — recorded whether or not it succeeded."""

    url: str
    attempted: bool = False
    success: bool = False
    file_path: Optional[str] = None       # filename relative to the run dir
    sha256: Optional[str] = None
    captured_utc: Optional[str] = None
    viewport: str = _DEFAULT_VIEWPORT
    browser: Optional[str] = None
    error_reason: Optional[str] = None    # attempted but failed
    skipped_reason: Optional[str] = None  # not attempted (capture unavailable)

    def to_record(self) -> dict:
        return {
            "url": self.url,
            "attempted": self.attempted,
            "success": self.success,
            "file_path": self.file_path,
            "sha256": self.sha256,
            "captured_utc": self.captured_utc,
            "viewport": self.viewport,
            "browser": self.browser,
            "error_reason": self.error_reason,
            "skipped_reason": self.skipped_reason,
        }


class ScreenshotCapture:
    """Base interface. `configured` says whether capture was asked for at all;
    `is_available()` says whether it can actually run right now."""

    configured: bool = False

    def is_available(self) -> bool:
        raise NotImplementedError

    def availability_detail(self) -> str:
        raise NotImplementedError

    def capture(self, url: str, out_dir: Optional[Path]) -> ScreenshotResult:
        raise NotImplementedError


class NoOpScreenshotCapture(ScreenshotCapture):
    """Used when capture is disabled or impossible: records WHY, captures
    nothing, never fails the run."""

    def __init__(self, reason: str, *, configured: bool = False):
        self._reason = reason
        self.configured = configured

    def is_available(self) -> bool:
        return False

    def availability_detail(self) -> str:
        return self._reason

    def capture(self, url: str, out_dir: Optional[Path]) -> ScreenshotResult:
        return ScreenshotResult(
            url=url, attempted=False, success=False, skipped_reason=self._reason,
        )


class PlaywrightScreenshotCapture(ScreenshotCapture):
    """Real capture via Playwright (chromium, headless). Lazy-imported so the
    dependency stays optional; every failure degrades to a recorded miss."""

    configured = True

    def __init__(self, viewport: str = _DEFAULT_VIEWPORT):
        self._viewport = viewport

    def is_available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:  # noqa: BLE001 — absence/breakage means unavailable, not error
            return False

    def availability_detail(self) -> str:
        if self.is_available():
            return "Playwright is installed; headless chromium capture is enabled."
        return "Playwright is not installed; screenshot capture is unavailable."

    def capture(self, url: str, out_dir: Optional[Path]) -> ScreenshotResult:
        if not self.is_available():
            return ScreenshotResult(
                url=url, attempted=False, success=False,
                skipped_reason=self.availability_detail(),
            )
        if out_dir is None:
            return ScreenshotResult(
                url=url, attempted=False, success=False,
                skipped_reason="No output directory for screenshots (no recorder).",
            )
        try:
            from playwright.sync_api import sync_playwright

            out_dir.mkdir(parents=True, exist_ok=True)
            filename = _safe_filename(url)
            target = out_dir / filename
            w, h = (int(x) for x in self._viewport.split("x"))
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": w, "height": h})
                    page.goto(url, timeout=_CAPTURE_TIMEOUT_MS, wait_until="domcontentloaded")
                    page.screenshot(path=str(target))
                finally:
                    browser.close()
            data = target.read_bytes()
            return ScreenshotResult(
                url=url, attempted=True, success=True,
                file_path=f"screenshots/{filename}",
                sha256=hashlib.sha256(data).hexdigest(),
                captured_utc=_utc_now_iso(),
                viewport=self._viewport, browser="chromium-headless",
            )
        except Exception as exc:  # noqa: BLE001 — capture failure is recorded, never raised
            return ScreenshotResult(
                url=url, attempted=True, success=False,
                viewport=self._viewport, browser="chromium-headless",
                error_reason=f"{type(exc).__name__}: {exc}",
            )


def build_screenshot_capture(enabled: Optional[bool] = None) -> ScreenshotCapture:
    """Factory: honest capability detection, no prompts, no exceptions.

    - capture disabled (default)      -> NoOp, configured=False
    - enabled but Playwright missing  -> NoOp, configured=True (unavailable)
    - enabled and Playwright present  -> PlaywrightScreenshotCapture
    """
    if enabled is None:
        try:
            from demand_research.config import settings
            enabled = bool(settings.enable_screenshots)
        except Exception:  # noqa: BLE001
            enabled = False
    if not enabled:
        return NoOpScreenshotCapture(
            "Screenshot capture is disabled (ENABLE_SCREENSHOTS is off).",
            configured=False,
        )
    capture = PlaywrightScreenshotCapture()
    if capture.is_available():
        return capture
    return NoOpScreenshotCapture(
        "Screenshot capture was requested but Playwright is not installed; "
        "captures are skipped and recorded as unavailable.",
        configured=True,
    )


# --------------------------------------------------------------------------- #
# Coverage (audit completeness) — reporting only, never a gate input
# --------------------------------------------------------------------------- #
def build_screenshot_coverage(
    *,
    capture: Optional[ScreenshotCapture],
    screenshot_records: list,
    accepted_public_source_ids: list,
) -> dict:
    """Summarise screenshot coverage over the run's ACCEPTED public sources.

    Distinguishes the two E1 dimensions explicitly:
      1. gate evidence validity is decided by the gates (never by screenshots);
      2. audit completeness — accepted public evidence carries screenshot
         documentation wherever capture was technically available.

    `audit_complete_for_e1_recording` is True only when every accepted public
    source has a captured screenshot. Capture being unavailable/not configured
    keeps it False with an honest reason — it never blocks or passes a gate.
    """
    available = bool(capture is not None and capture.is_available())
    configured = bool(getattr(capture, "configured", False))
    detail = capture.availability_detail() if capture is not None else (
        "No screenshot capture layer present."
    )

    by_source = {r.get("source_id"): r for r in screenshot_records if r.get("source_id")}
    required = list(accepted_public_source_ids)
    attempted = sum(
        1 for sid in required if by_source.get(sid, {}).get("attempted")
    )
    captured = [
        sid for sid in required if by_source.get(sid, {}).get("success")
    ]
    failed = sum(
        1 for sid in required
        if by_source.get(sid, {}).get("attempted") and not by_source.get(sid, {}).get("success")
    )
    missing = [sid for sid in required if sid not in captured]

    complete = len(required) > 0 and not missing
    if not required:
        # Nothing accepted -> nothing to document; completeness is vacuous and
        # reported as such rather than claimed.
        complete = True
        reason = "No accepted public sources required screenshot documentation."
    elif complete:
        reason = None
    elif not configured:
        reason = (
            "capture_not_configured: screenshot capture is off; accepted public "
            "evidence has no screenshot documentation."
        )
    elif not available:
        reason = (
            "capture_unavailable: screenshot capture was requested but is not "
            "technically available this run."
        )
    else:
        reason = (
            "captures_failed_or_missing: capture was available but some accepted "
            "public sources have no captured screenshot."
        )

    return {
        "capture_configured": configured,
        "capture_available": available,
        "capture_detail": detail,
        "screenshots_required_for_accepted_public_sources": len(required),
        "screenshots_attempted": attempted,
        "screenshots_captured": len(captured),
        "screenshots_failed": failed,
        "accepted_sources_without_screenshots": missing,
        "audit_complete_for_e1_recording": complete,
        "reason": reason,
        "governance": (
            "Screenshots are audit documentation, never evidence: capture success "
            "cannot satisfy a gate, capture failure cannot invent or invalidate "
            "evidence, and missing screenshots only mark accepted evidence "
            "audit-incomplete for recording — they never change the verdict."
        ),
    }
