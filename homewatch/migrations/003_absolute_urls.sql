-- One-time cleanup: absolutize Apple support URLs stored before the 0.3.3
-- parser fix. upsert_release gap-fills url via COALESCE (keeps the first-seen
-- value), so a plain re-refresh won't replace a stored relative path — rewrite
-- them here. Every relative ("/…") release URL we store comes from the Apple
-- security scraper (support.apple.com); other sources store absolute URLs.
UPDATE releases
   SET url = 'https://support.apple.com' || url
 WHERE url LIKE '/%' AND url NOT LIKE '//%';
